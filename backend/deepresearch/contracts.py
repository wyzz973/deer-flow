"""Public, versioned contracts. No model is trusted to manufacture source metadata."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Origin = Literal["internal", "external"]
Level = Literal["L1", "L2", "L3", "L4"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    AWAITING_PLAN_CONFIRMATION = "AWAITING_PLAN_CONFIRMATION"
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
    max_tool_calls: int = Field(default=60, ge=2, le=1000)
    max_elapsed_seconds: int = Field(default=900, ge=10, le=14400)
    max_model_tokens: int = Field(default=120000, ge=1000, le=2000000)


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


class ResearchUnit(Contract):
    id: Identifier
    skill: Identifier
    objective: str = Field(min_length=3, max_length=4000)
    priority: int = Field(default=10, ge=0, le=1000)
    source_strategy: SourceStrategy = Field(default_factory=SourceStrategy)
    depends_on: list[Identifier] = Field(default_factory=list, max_length=64)
    parent_gap_id: str | None = None


class ResearchPlan(Contract):
    goal: str = Field(min_length=3, max_length=12000)
    research_units: list[ResearchUnit] = Field(min_length=1, max_length=64)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    expected_output: str = "有证据支撑的结构化研究报告"
    plan_version: int = Field(default=1, ge=1)

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
    origin: Origin
    source_name: Identifier
    source_level: Level = "L4"
    # Source independence is assigned by configuration (e.g. publisher), not the model.
    publisher: str = Field(min_length=1, max_length=200)
    published_at: datetime | None = None
    retrieved_at: str = Field(default_factory=utcnow)
    snippet: str = Field(min_length=1, max_length=20000)
    raw_content_ref: str | None = None

    _url = field_validator("url")(safe_http_url)

    @model_validator(mode="after")
    def locator_required(self):
        if not self.url and not self.source_uri:
            raise ValueError("Evidence needs a URL or a source_uri/document_id")
        if self.origin == "external" and not self.url:
            raise ValueError("External evidence needs a verifiable http(s) URL")
        return self


class Finding(Contract):
    claim: str = Field(min_length=1, max_length=6000)
    raw_evidence_refs: list[Identifier] = Field(min_length=1, max_length=100)
    confidence: float = Field(ge=0, le=1)
    high_risk: bool = False


class ResearchResult(Contract):
    unit_id: Identifier
    findings: list[Finding] = Field(default_factory=list, max_length=100)
    raw_evidences: list[RawEvidence] = Field(default_factory=list, max_length=500)
    open_questions: list[str] = Field(default_factory=list, max_length=50)
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
    origin: Origin
    source_name: Identifier
    source_level: Level
    publisher: str
    published_at: datetime | None
    retrieved_at: str
    snippet: str
    content_hash: str
    unit_ids: list[str]
    raw_content_ref: str | None


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


class StructuredReport(Contract):
    title: str = Field(min_length=1, max_length=500)
    executive_summary: list[Segment] = Field(min_length=1, max_length=100)
    sections: list[ReportSection] = Field(min_length=1, max_length=64)
    conclusion: list[Segment] = Field(min_length=1, max_length=100)

    def segments(self):
        yield from self.executive_summary
        for section in self.sections:
            yield from section.segments
        yield from self.conclusion


class CreateResearch(Contract):
    query: str = Field(min_length=3, max_length=12000)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    source_names: list[Identifier] = Field(default_factory=list, max_length=50)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)


class PlanDecision(Contract):
    plan_version: int = Field(ge=1)


class PlanEdit(PlanDecision):
    plan: ResearchPlan


class ResearchError(RuntimeError):
    """Safe public error: never construct this with a provider exception message."""
    def __init__(self, code: str, message: str, *, recoverable: bool = True):
        super().__init__(message)
        self.code, self.recoverable = code, recoverable
