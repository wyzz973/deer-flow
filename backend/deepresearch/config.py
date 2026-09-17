"""Configuration is local and opt-in; never modifies the host's tool registry."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, PrivateAttr, model_validator

from .contracts import Contract, Identifier, Level, Origin, ResearchBudget

ROOT = Path(__file__).resolve().parents[2]


class SkillSpec(Contract):
    agent: Identifier
    path: str
    description: str
    model: str | None = None
    system_prompt: str = ""
    # The host interprets this as graph recursion steps, not model exchanges.
    # Inherit its configured limit unless the operator explicitly tightens it.
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    # A deep-research unit reads many pages; the native Agent timeout still applies.
    timeout_seconds: int = Field(default=600, ge=5, le=1800)


class SourceSpec(Contract):
    name: Identifier
    origin: Origin
    kind: Literal["mcp", "native"] = "mcp"
    server: str | None = None
    tool: str  # Exact host tool name; no prefix guessing or response adapter.
    # How the tool participates in research, chosen by the operator rather than
    # inferred from its payload: search results are discovery only, read tools
    # open documents, data tools return citable records (e.g. a knowledge base).
    role: Literal["search", "read", "data"] = "data"
    level: Level = "L4"
    priority: int = 100
    publisher: str = "unclassified"
    # Accepted only for migration from V1. Native tools now own their argument
    # and response formats; these legacy fields never modify a tool call.
    query_arg: str = Field(default="query", deprecated=True)
    fixed_args: dict = Field(default_factory=dict)
    results_path: str = "results"  # dotted path in structuredContent/text JSON
    response_mode: Literal["auto", "mapped"] = "auto"
    fields: dict[str, str] = Field(
        default_factory=lambda: {
            "title": "title",
            "url": "url",
            "snippet": "snippet",
            "source_uri": "document_id",
            "published_at": "published_at",
        }
    )

    @model_validator(mode="after")
    def valid_binding(self):
        if self.kind == "mcp" and not self.server:
            raise ValueError("MCP sources need an explicit server name")
        if self.kind == "native" and self.server is not None:
            raise ValueError("Native sources do not select an MCP server")
        return self


class Settings(Contract):
    _skill_cache: dict[str, str] = PrivateAttr(default_factory=dict)
    runner: Literal["demo", "deerflow"] = "demo"
    runner_factory: str | None = None  # optional admin-controlled module:factory(settings, store)
    data_dir: str = ".deerflow/deepresearch"
    skills: dict[str, SkillSpec]
    sources: list[SourceSpec] = Field(default_factory=list)
    source_priority_file: str | None = None
    source_fallback: list[str] = Field(default_factory=list)
    max_concurrency: int = Field(default=3, ge=1, le=8)
    max_active_runs: int = Field(default=8, ge=1, le=100)
    plan_countdown_seconds: float = Field(default=45, ge=0.05, le=600)
    tool_timeout_seconds: int = Field(default=45, ge=1, le=300)
    tool_retries: int = Field(default=1, ge=0, le=3)
    max_output_tokens: int = Field(default=4096, ge=128, le=393216)
    output_retries: int = Field(default=2, ge=0, le=4)
    extraction_model: str | None = None
    native_tools: list[str] | None = None  # Optional ceiling; native Agent/Skill authorization is authoritative.
    trace_capture_content: bool = True
    trace_max_chars: int = Field(default=16000, ge=1000, le=100000)
    # When bounded supplementation cannot close every gap, write the report
    # with explicit caveats (ChatGPT-style) instead of failing. Evidence-free
    # runs still fail, and strict deployments can require owner consent.
    allow_limited_report: bool = True
    # Search results and links merely seen in tool output are discovery aids.
    # Enable only for deployments whose search tool returns full documents.
    cite_search_results: bool = False
    max_synthesis_repairs: int = Field(default=1, ge=0, le=3)
    max_report_sections: int = Field(default=8, ge=2, le=12)
    # Site icons for cited domains are fetched by the gateway (public hosts only).
    # Disable for deployments without outbound access; the UI shows letter badges.
    favicons: bool = True
    require_dual_source: bool = True
    # Automatic LangSmith tracing is disabled; local spans redact captured content.
    budget_ceiling: ResearchBudget = Field(default_factory=ResearchBudget)
    # Explicit, local-only fallback. These are ENVIRONMENT VARIABLE NAMES, not values.
    local_secret_env: dict[str, str] = Field(default_factory=dict)
    # Header -> secret context key; opt-in and never persisted or sent to the LLM.
    request_secret_headers: dict[str, str] = Field(default_factory=dict)
    # Optional async callable(request, run_dict) -> bool for live enterprise ACL checks.
    access_policy: str | None = None

    @model_validator(mode="after")
    def valid_registry(self):
        if "deepresearch" not in self.skills or "report-synthesis" not in self.skills:
            raise ValueError("Registry needs deepresearch and report-synthesis skills")
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("Source names must be unique")
        if self.runner == "deerflow" and not self.sources:
            raise ValueError("DeerFlow mode needs at least one configured source")
        if self.runner == "deerflow" and self.require_dual_source and {s.origin for s in self.sources} != {"internal", "external"}:
            raise ValueError("DeerFlow mode needs explicit internal and external source bindings")
        if not set(self.source_fallback).issubset(names):
            raise ValueError("Unknown fallback source")
        return self

    def resolve(self, path: str) -> Path:
        value = Path(path).expanduser()
        return value.resolve() if value.is_absolute() else (ROOT / value).resolve()

    def read_skill(self, name: str) -> str:
        if name in self._skill_cache:
            return self._skill_cache[name]
        spec = self.skills.get(name)
        if spec is None:
            raise ValueError(f"Unknown skill: {name}")
        path = self.resolve(spec.path)
        text = path.read_text(encoding="utf-8")
        if len(text) > 100000:
            raise ValueError(f"Skill too large: {name}")
        return text

    def check_plan(self, plan, budget, request_sources=()):
        if len(plan.research_units) > budget.max_units:
            raise ValueError("Plan exceeds max_units")
        allowed = {s.name for s in self.sources}
        if not set(request_sources).issubset(allowed):
            raise ValueError("Unknown requested source")
        for unit in plan.research_units:
            if unit.skill not in self.skills or unit.skill in {"deepresearch", "report-synthesis"}:
                raise ValueError(f"Unsupported researcher skill: {unit.skill}")
            if not set(unit.source_strategy.source_names).issubset(allowed):
                raise ValueError("Plan selected an unknown source")
            if self.require_dual_source and set(unit.source_strategy.required_origins) != {"internal", "external"}:
                raise ValueError("This deployment requires both internal and external research")


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path) if path else ROOT / "deepresearch.example.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Settings.model_validate(raw)
