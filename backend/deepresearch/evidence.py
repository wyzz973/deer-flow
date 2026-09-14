"""Deterministic evidence merge, conservative URL normalization and source policy."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import BoundFinding, Evidence, RawEvidence, ResearchResult, safe_http_url

TRACKING = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid"}


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    safe_http_url(url)
    parts = urlsplit(url)
    # Do NOT discard business params, fragments alone are presentation anchors.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
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
            findings.append(BoundFinding(
                unit_id=result.unit_id, claim=finding.claim, confidence=finding.confidence,
                high_risk=finding.high_risk,
                evidence_ids=list(dict.fromkeys(lineage[f"{result.unit_id}:{ref}"] for ref in finding.raw_evidence_refs)),
            ).model_dump())
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
    ordered = sorted(settings.sources, key=lambda source: (
        0 if source.name in preferred else 1, preferred.get(source.name, 999),
        source.level, source.priority, source.name,
    ))
    return ordered, provenance


def at_path(value, path: str):
    for part in filter(None, path.split(".")):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def decode_mcp_result(value):
    # StructuredTool content_and_artifact or MCP CallToolResult/ToolMessage.
    if isinstance(value, tuple) and len(value) == 2:
        content, artifact = value
        if isinstance(artifact, dict) and "structured_content" in artifact:
            return artifact["structured_content"]
        value = content
    if hasattr(value, "structuredContent") and value.structuredContent is not None:
        return value.structuredContent
    if hasattr(value, "content"):
        value = value.content
    if isinstance(value, dict):
        return value.get("structuredContent", value)
    if isinstance(value, list):
        texts = [block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "") for block in value]
        value = "\n".join(texts)
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError("MCP result is not structured JSON; configure an explicit adapter")


def normalize_mcp(value, source, *, limit=12) -> list[RawEvidence]:
    decoded = decode_mcp_result(value)
    rows = at_path(decoded, source.results_path) if source.results_path else decoded
    if not isinstance(rows, list):
        raise ValueError("Configured MCP results_path does not point to a list")
    result = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        fields = {key: at_path(row, path) for key, path in source.fields.items()}
        date = fields.get("published_at")
        if date:
            try:
                date = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                date = None  # Unknown, never manufacture a publication date.
        else:
            date = None
        try:
            identity = digest([source.name, fields.get("url"), fields.get("source_uri"), fields.get("snippet")])[:24]
            result.append(RawEvidence(
                raw_id=f"raw_{identity}", title=fields.get("title") or "", url=fields.get("url") or None,
                source_uri=str(fields["source_uri"]) if fields.get("source_uri") is not None else None,
                snippet=fields.get("snippet") or "", published_at=date,
                source_name=source.name, origin=source.origin, publisher=source.publisher,
                source_level=source.level,
            ))
        except (ValueError, TypeError):
            continue  # Unverifiable/empty hits cannot become evidence.
    return result
