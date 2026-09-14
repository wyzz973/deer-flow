"""Deterministic evidence merge, conservative URL normalization and source policy."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import BoundFinding, Evidence, ResearchResult, safe_http_url

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
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", urlencode(query), ""))


def digest(value) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
            elif result.unit_id not in evidences[eid].unit_ids:
                evidences[eid].unit_ids.append(result.unit_id)
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
    configured = {source.name: source for source in settings.sources}
    if not set(names).issubset(configured):
        raise ValueError("Source priority contains an unconfigured source")
    preferred = {name: i for i, name in enumerate(names)}
    ordered = sorted(
        settings.sources,
        key=lambda source: (
            0 if source.name in preferred else 1,
            preferred.get(source.name, 999),
            source.level,
            source.priority,
            source.name,
        ),
    )
    return ordered, provenance
