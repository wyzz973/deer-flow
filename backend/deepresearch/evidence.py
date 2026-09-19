"""Deterministic evidence merge, conservative URL normalization and source policy."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from .contracts import BoundFinding, Evidence, RawEvidence, ResearchResult, safe_http_url

TRACKING = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid"}


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    safe_http_url(url)
    parts = urlsplit(url)
    # Do NOT discard business params, fragments alone are presentation anchors.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and not (parts.scheme == "https" and port == 443 or parts.scheme == "http" and port == 80):
        host += f":{port}"
    # "#section" is a position inside a page. "#/doc/5" and "#!/doc/5" are how a
    # single-page application (many internal wikis) names the page itself.
    route = parts.fragment if parts.fragment.startswith(("/", "!")) else ""
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", urlencode(query), route))


def digest(value) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def valid_result(body: dict) -> tuple[ResearchResult, list[dict]]:
    """Validate a stored unit result, dropping only the evidence it cannot keep.

    Evidence is derived from open-web payloads and from results written by an
    earlier version of this schema, so a single record can be unusable: a
    locator the contract refuses, text that arrived empty, a field a later rule
    bounds. Failing the whole unit there would throw away a completed research
    step, so the unusable records are dropped with the fields that rejected
    them. Findings keep every reference that survived; one left without any
    reference is dropped with them. Anything else still fails loudly.
    """
    records, dropped, kept = [], [], set()
    for index, item in enumerate(body.get("raw_evidences") or []):
        try:
            evidence = RawEvidence.model_validate(item)
        except ValidationError as error:
            fields = sorted({str(detail["loc"][0]) for detail in error.errors(include_input=False, include_url=False) if detail.get("loc")})
            dropped.append({"raw_id": str(item.get("raw_id", index))[:80], "fields": fields})
            continue
        records.append(evidence.model_dump(mode="json"))
        kept.add(evidence.raw_id)
    findings = []
    for finding in body.get("findings") or []:
        refs = [ref for ref in finding.get("raw_evidence_refs") or [] if ref in kept]
        if refs:
            findings.append({**finding, "raw_evidence_refs": refs})
        elif not dropped:  # References were never about dropped evidence; let validation report it.
            findings.append(finding)
    return ResearchResult.model_validate({**body, "raw_evidences": records, "findings": findings}), dropped


def merge_results(results: list[ResearchResult], pool: dict[str, dict] | None = None):
    """Same ordered input -> same ids. Never collapse ACL/origin or source identity."""
    evidences = {key: Evidence.model_validate(value) for key, value in (pool or {}).items()}

    def key(e):
        return (e.origin, e.source_name, e.canonical_url or e.source_uri, e.content_hash)

    index = {key(e): eid for eid, e in evidences.items()}
    next_id = max((int(eid[1:]) for eid in evidences), default=0) + 1
    lineage, findings = {}, []
    for result in results:  # caller uses plan order, never completion order
        for raw in sorted(result.raw_evidences, key=lambda item: item.raw_id):
            normalized = re.sub(r"\s+", " ", raw.snippet).strip()
            data = raw.model_dump(exclude={"raw_id"})
            data.update(canonical_url=canonical_url(raw.url), content_hash=digest(normalized), unit_ids=[result.unit_id])
            candidate = Evidence(evidence_id=f"E{next_id:03d}", **data)
            identity = key(candidate)
            eid = index.get(identity)
            if eid is None:
                eid = candidate.evidence_id
                evidences[eid] = candidate
                index[identity] = eid
                next_id += 1
            else:
                if result.unit_id not in evidences[eid].unit_ids:
                    evidences[eid].unit_ids.append(result.unit_id)
                if raw.citable and not evidences[eid].citable:
                    evidences[eid].citable = True  # citable for the step that could not open originals
            lineage[f"{result.unit_id}:{raw.raw_id}"] = eid
        for finding in result.findings:
            findings.append(
                BoundFinding(
                    unit_id=result.unit_id,
                    claim=finding.claim,
                    confidence=finding.confidence,
                    high_risk=finding.high_risk,
                    evidence_ids=list(dict.fromkeys(lineage[f"{result.unit_id}:{ref}"] for ref in finding.raw_evidence_refs)),
                ).model_dump()
            )
    return {key: e.model_dump(mode="json") for key, e in evidences.items()}, findings, lineage


def ordered_sources(settings, strategy, request_names=()):
    """Explicit names set priority, not a fallback that drops the other origin."""
    names = strategy.source_names or list(request_names)
    provenance = "injected"
    if not names and settings.source_priority_file:
        import yaml

        names = yaml.safe_load(settings.resolve(settings.source_priority_file).read_text(encoding="utf-8"))
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError("source_priority_file must be a YAML list of source names")
        provenance = "file"
    if not names:
        names, provenance = settings.source_fallback, "fallback"
    active = settings.active_sources() if hasattr(settings, "active_sources") else list(settings.sources)
    known = {source.name for source in settings.sources}
    if not set(names).issubset(known):
        raise ValueError("Source priority contains an unconfigured source")
    # A source switched off on the settings page may still be named by a plan,
    # a request or the fallback list; it simply is not offered.
    preferred = {name: i for i, name in enumerate(names)}
    ordered = sorted(
        active,
        key=lambda source: (
            0 if source.name in preferred else 1,
            preferred.get(source.name, 999),
            source.level,
            source.priority,
            source.name,
        ),
    )
    return ordered, provenance
