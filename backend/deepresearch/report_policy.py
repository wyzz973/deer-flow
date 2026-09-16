"""Deterministic citation eligibility; never interprets an MCP business payload."""

from urllib.parse import urlsplit

from pydantic import Field, create_model

from .contracts import ComparisonRow, ComparisonTable, ReportSection, Segment, SourcePolicy, StructuredReport


class BriefParagraph(Segment):
    """Local editing bounds are easier to repair than a whole-report word count."""

    text: str = Field(min_length=1, max_length=280, description="One concise paragraph, at most 280 characters; keep conditions attached to facts.")
    evidence_ids: list[str] = Field(default_factory=list, max_length=3)


class BriefCell(Segment):
    text: str = Field(min_length=1, max_length=120, description="A short comparison-table cell, not a paragraph copied from research notes.")
    evidence_ids: list[str] = Field(default_factory=list, max_length=2)


class BriefRow(ComparisonRow):
    cells: list[BriefCell] = Field(min_length=2, max_length=4)


class BriefTable(ComparisonTable):
    rows: list[BriefRow] = Field(min_length=1, max_length=5)


class BriefSection(ReportSection):
    segments: list[BriefParagraph] = Field(min_length=1, max_length=2)


def report_schema(plan):
    """Narrow the output boundary, never the provider's budget or tool results.

    The public/stored report AST stays backward-compatible. Only generation of
    a newly requested brief report uses these paragraph-level editing bounds;
    historical reports remain readable and exportable under StructuredReport.
    """
    if plan.report_style != "brief":
        return StructuredReport
    return create_model(
        "BriefStructuredReport",
        __base__=StructuredReport,
        executive_summary=(list[BriefParagraph], Field(min_length=1, max_length=2)),
        comparison_table=(BriefTable | None, None),
        sections=(list[BriefSection], Field(min_length=len(plan.research_units), max_length=len(plan.research_units) + 1)),
        conclusion=(list[BriefParagraph], Field(min_length=1, max_length=2)),
    )


def source_allowed(evidence, policy):
    policy = SourcePolicy.model_validate(policy or {})
    item = evidence.model_dump() if hasattr(evidence, "model_dump") else evidence
    url = item.get("url") or item.get("canonical_url")
    if policy.allowed_domains:
        host = (urlsplit(url or "").hostname or "").lower()
        if not any(host == domain or host.endswith("." + domain) for domain in policy.allowed_domains):
            return False
    if policy.require_original and item.get("provenance") not in {"document", "fetched_document"}:
        return False
    for prefix in policy.excluded_url_prefixes:
        candidate, excluded = urlsplit(url or ""), urlsplit(prefix)
        same_host = (candidate.hostname or "").removeprefix("www.") == (excluded.hostname or "").removeprefix("www.")
        path = excluded.path.rstrip("/")
        if same_host and (candidate.path == path or candidate.path.startswith(path + "/")):
            return False
    return True


def report_character_limit(style):
    return {"brief": 6000, "standard": 12000, "detailed": 36000}[style]
