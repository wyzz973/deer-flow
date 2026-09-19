"""Public, versioned contracts. No model is trusted to manufacture source metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Origin = Literal["internal", "external"]
Level = Literal["L1", "L2", "L3", "L4"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")]


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    AWAITING_PLAN_CONFIRMATION = "AWAITING_PLAN_CONFIRMATION"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    EDITING_PLAN = "EDITING_PLAN"
    RESPONDING = "RESPONDING"
    RESEARCHING = "RESEARCHING"
    VALIDATING = "VALIDATING"
    GAP_FOUND = "GAP_FOUND"
    RESEARCH_COMPLETE = "RESEARCH_COMPLETE"
    SYNTHESIZING = "SYNTHESIZING"
    CITATION_BINDING = "CITATION_BINDING"
    FINAL_VALIDATING = "FINAL_VALIDATING"
    RENDERING = "RENDERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class ResearchBudget(Contract):
    max_iterations: int = Field(default=2, ge=0, le=8)
    max_units: int = Field(default=12, ge=1, le=64)
    # Null explicitly disables that resource ceiling. Admission still rejects
    # unbounded requests when the operator configured a finite ceiling.
    max_tool_calls: int | None = Field(default=60, ge=2, le=1000)
    max_elapsed_seconds: int | None = Field(default=900, ge=10, le=14400)
    max_model_tokens: int | None = Field(default=120000, ge=1000, le=2000000)


class SourceStrategy(Contract):
    # Empty source_names means resolve request preferences > file > fallback.
    source_names: list[Identifier] = Field(default_factory=list, max_length=50)
    required_origins: list[Origin] = Field(default_factory=lambda: ["internal", "external"], min_length=1)
    max_results: int = Field(default=12, ge=1, le=50)
    not_before: datetime | None = None

    @field_validator("required_origins", "source_names")
    @classmethod
    def unique(cls, values):
        return list(dict.fromkeys(values))


def bounded(limit: int):
    """Truncate display text from the open web instead of failing validation.

    A page without a title is labeled by its URL, and a URL with a signed query
    string can run to thousands of characters. Titles and publisher labels are
    display metadata, so one such page must not fail a whole research run when
    results are revalidated. Identity fields (url, source_uri, ids) stay strict.
    """

    def clamp(value):
        if isinstance(value, str) and len(value) > limit:
            return value[: limit - 1] + "…"
        return value

    return clamp


class ResearchUnit(Contract):
    id: Identifier
    skill: Identifier
    # Short, user-facing step label shown on the plan card. The objective keeps
    # the detailed researcher instruction; historical plans have no title.
    title: str = Field(default="", max_length=80)
    _title = field_validator("title", mode="before")(bounded(80))
    objective: str = Field(min_length=3, max_length=4000)
    priority: int = Field(default=10, ge=0, le=1000)
    source_strategy: SourceStrategy = Field(default_factory=SourceStrategy)
    depends_on: list[Identifier] = Field(default_factory=list, max_length=64)
    parent_gap_id: str | None = None


class SourcePolicy(Contract):
    """Intent-derived citation scope, not a schema imposed on MCP results."""

    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    excluded_url_prefixes: list[str] = Field(default_factory=list, max_length=50)
    require_original: bool = False

    @field_validator("allowed_domains")
    @classmethod
    def valid_domains(cls, values):
        result = []
        for value in values:
            domain = value.lower().strip().rstrip(".")
            if any(char in domain for char in "/:@*?# ") or "." not in domain:
                raise ValueError("Source domains must be hostnames, not URLs or wildcards")
            result.append(domain.encode("idna").decode("ascii"))
        return list(dict.fromkeys(result))

    @field_validator("excluded_url_prefixes")
    @classmethod
    def valid_prefixes(cls, values):
        return [safe_http_url(value) for value in values]


class ResearchRequest(Contract):
    """The conversation rewritten into one complete research request.

    ChatGPT's conversation model produces this before a deep research session
    starts (the ``user_query`` of its research tool call). The planner, the
    researchers and the writer share it as the research brief.
    """

    user_query: str = Field(min_length=3, max_length=12000)
    # One or two sentences confirming how a revision changes the research.
    acknowledgement: str = Field(default="", max_length=1000)
    # Only when the conversation has no identifiable research subject.
    clarification_questions: list[str] = Field(default_factory=list, max_length=3)


class ResearchPlan(Contract):
    goal: str = Field(min_length=3, max_length=12000)
    # A short plan title for the card and report defaults.
    title: str = Field(default="", max_length=120)
    _title = field_validator("title", mode="before")(bounded(120))
    # The rewritten research brief: focus areas, time anchor, source
    # preferences, evidence distinctions and expected report structure. It is
    # the shared specification for researchers and the report writer.
    brief: str = Field(default="", max_length=12000)
    research_units: list[ResearchUnit] = Field(min_length=1, max_length=64)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    # Explicit scenario assumptions replace questions about the user's private
    # context. They are shown to the user and stated in the report.
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    expected_output: str = "有证据支撑的结构化研究报告"
    plan_version: int = Field(default=1, ge=1)
    clarification_questions: list[str] = Field(default_factory=list, max_length=3)
    # One or two sentences confirming a conversational revision, shown before
    # the revised plan. Empty for the first plan.
    acknowledgement: str = Field(default="", max_length=1000)
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    report_style: Literal["brief", "standard", "detailed"] = "standard"

    @model_validator(mode="after")
    def acyclic(self):
        units = {unit.id: unit for unit in self.research_units}
        if len(units) != len(self.research_units):
            raise ValueError("ResearchUnit ids must be unique")
        visiting, visited = set(), set()

        def visit(key):
            if key not in units:
                raise ValueError(f"Unknown dependency: {key}")
            if key in visiting:
                raise ValueError("ResearchPlan dependencies contain a cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in units[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in units:
            visit(key)
        return self


def safe_http_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    _ = parsed.port  # Reject malformed ports before evidence enters the pool.
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Expected an http(s) URL without embedded credentials")
    if any(ord(char) < 32 for char in value):
        raise ValueError("Control characters are not allowed in URLs")
    return value


class RawEvidence(Contract):
    raw_id: Identifier
    title: str = Field(min_length=1, max_length=1000)
    url: str | None = None
    source_uri: str | None = Field(default=None, max_length=2000)
    origin: Literal["internal", "external", "runtime"]
    source_name: Identifier
    source_level: Level = "L4"
    # Source independence is assigned by configuration (e.g. publisher), not the model.
    publisher: str = Field(min_length=1, max_length=200)
    published_at: datetime | None = None
    retrieved_at: str = Field(default_factory=utcnow)
    snippet: str = Field(min_length=1, max_length=20000)
    raw_content_ref: str | None = None
    provenance: Literal["document", "tool_output", "observed_source", "fetched_document"] = "document"
    source_id: str | None = None
    document_hash: str | None = None
    # Decided where the evidence was gathered: a search result is citable when
    # the step that found it had no tool to open originals. Later stages
    # (gap review, writing, final validation) read this instead of deciding
    # again from deployment-wide settings, which disagreed for a role limited
    # to an internal search tool in a deployment that also had a web reader. None:
    # decide from provenance and the source's role (results saved earlier).
    citable: bool | None = None

    _url = field_validator("url")(safe_http_url)
    _title = field_validator("title", mode="before")(bounded(1000))
    _publisher = field_validator("publisher", mode="before")(bounded(200))
    # An excerpt is already an excerpt: keeping a longer one bounded loses less
    # than dropping the record. Empty text stays a failure — there is nothing to cite.
    _snippet = field_validator("snippet", mode="before")(bounded(20000))

    @model_validator(mode="after")
    def locator_required(self):
        if not self.url and not self.source_uri:
            raise ValueError("Evidence needs a URL or a source_uri/document_id")
        if self.provenance == "tool_output" and not (self.source_uri or "").startswith(("mcp-result://", "tool-result://")):
            raise ValueError("Opaque tool output must reference a registered MCP result")
        if self.origin == "external" and not self.url and self.provenance != "tool_output":
            raise ValueError("External evidence needs a verifiable http(s) URL")
        return self


class Finding(Contract):
    claim: str = Field(min_length=1, max_length=6000)
    raw_evidence_refs: list[Identifier] = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    high_risk: bool = False


class SourceAnnotation(Contract):
    raw_id: Identifier
    title: str = Field(default="", max_length=1000)
    quote: str = Field(default="", max_length=20000)


# Bounds on one unit's durable result. A long unit can observe more links than
# this; the runner trims surplus discovery links rather than failing the unit.
RAW_EVIDENCE_LIMIT = 500
LIMITATION_LIMIT = 30


class ResearchResult(Contract):
    unit_id: Identifier
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    raw_evidences: list[RawEvidence] = Field(default_factory=list, max_length=RAW_EVIDENCE_LIMIT)
    # Only publicly researchable questions that would change the conclusions.
    # They are the sole researcher-reported input to gap review.
    open_questions: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=LIMITATION_LIMIT)
    # Unknown user-private context (scale, budget, deployment). These become
    # stated assumptions, never research gaps.
    assumptions_needed: list[str] = Field(default_factory=list, max_length=20)
    # A short user-facing summary for the activity timeline and the writer.
    summary: str = Field(default="", max_length=2000)
    confidence: float = Field(ge=0, le=1)
    searched_origins: list[Origin] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_references(self):
        ids = [e.raw_id for e in self.raw_evidences]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate raw evidence id")
        for finding in self.findings:
            if not set(finding.raw_evidence_refs).issubset(ids):
                raise ValueError("Finding refers to unregistered raw evidence")
        return self


class Evidence(Contract):
    url: str | None = None  # Preserve the tool-returned locator; canonical_url is for dedup.
    evidence_id: str = Field(pattern=r"^E[0-9]{3,}$")
    canonical_url: str | None
    source_uri: str | None
    title: str
    origin: Literal["internal", "external", "runtime"]
    source_name: Identifier
    source_level: Level
    publisher: str
    published_at: datetime | None
    retrieved_at: str
    snippet: str
    content_hash: str
    unit_ids: list[str]
    raw_content_ref: str | None
    provenance: Literal["document", "tool_output", "observed_source", "fetched_document"] = "document"
    source_id: str | None = None
    document_hash: str | None = None
    citable: bool | None = None  # see RawEvidence.citable


class BoundFinding(Contract):
    unit_id: str
    claim: str
    evidence_ids: list[str]
    confidence: float
    high_risk: bool = False


class ResearchGap(Contract):
    gap_id: str
    unit_id: str
    code: str
    description: str


class Segment(Contract):
    text: str = Field(min_length=1, max_length=12000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    segment_type: Literal["fact", "analysis", "recommendation"] = "fact"


class ReportSection(Contract):
    heading: str = Field(min_length=1, max_length=500)
    unit_ids: list[str] = Field(min_length=1, max_length=64)
    segments: list[Segment] = Field(min_length=1, max_length=200)


class ComparisonRow(Contract):
    label: str = Field(min_length=1, max_length=100, description="Dimension label, rendered in a separate first column; not an alternative cell.")
    cells: list[Segment] = Field(min_length=2, max_length=4)


class ComparisonTable(Contract):
    headers: list[str] = Field(min_length=2, max_length=4, description="Alternative names ONLY, e.g. ['SQLite', 'PostgreSQL']. Do not include the implicit dimension/row-label heading.")
    rows: list[ComparisonRow] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def matching_columns(self):
        if any(len(row.cells) != len(self.headers) for row in self.rows):
            raise ValueError(
                "Each comparison row must match the header count: headers must contain only alternative names, not the dimension/row label. For two alternatives use two headers and two cells; each row.label is rendered separately."
            )
        return self


class StructuredReport(Contract):
    title: str = Field(min_length=1, max_length=500)
    executive_summary: list[Segment] = Field(min_length=1, max_length=100)
    comparison_table: ComparisonTable | None = None
    sections: list[ReportSection] = Field(min_length=1, max_length=64)
    conclusion: list[Segment] = Field(min_length=1, max_length=100)

    def segments(self):
        yield from self.executive_summary
        if self.comparison_table:
            for row in self.comparison_table.rows:
                yield from row.cells
        for section in self.sections:
            yield from section.segments
        yield from self.conclusion


class OutlineSection(Contract):
    heading: str = Field(min_length=1, max_length=200)
    # What this section must establish for the reader; not a research task.
    purpose: str = Field(min_length=1, max_length=2000)
    unit_ids: list[Identifier] = Field(default_factory=list, max_length=64)
    # Optional presentation intent, e.g. "table: priority matrix" or
    # "mermaid: target architecture". The section writer decides the syntax.
    visuals: list[str] = Field(default_factory=list, max_length=4)


class ReportOutline(Contract):
    """Writer plan for a Markdown report. Sections follow decision logic, not
    the order of research units or tools."""

    title: str = Field(min_length=1, max_length=300)
    key_conclusions: list[str] = Field(min_length=1, max_length=8)
    sections: list[OutlineSection] = Field(min_length=1, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    # Reader-facing caveats merged from the raw limitation records.
    limitations: list[str] = Field(default_factory=list, max_length=6)


class CreateResearch(Contract):
    query: str = Field(min_length=3, max_length=12000)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    source_names: list[Identifier] = Field(default_factory=list, max_length=50)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)


class PlanDecision(Contract):
    plan_version: int = Field(ge=1)


class RetryResearch(Contract):
    # Explicit owner consent, not a model-controlled completeness decision.
    allow_limited_report: bool | None = None


class PlanEdit(PlanDecision):
    plan: ResearchPlan


class ConversationMessage(Contract):
    text: str = Field(min_length=1, max_length=12000)
    client_message_id: Identifier
    plan_version: int | None = Field(default=None, ge=1)


class FollowupResult(Contract):
    action: Literal["answer", "revise", "research"]
    text: str = Field(min_length=1, max_length=20000)


class ResearchError(RuntimeError):
    """Safe public error: never construct this with a provider exception message."""

    def __init__(self, code: str, message: str, *, recoverable: bool = True):
        super().__init__(message)
        self.code, self.recoverable = code, recoverable
