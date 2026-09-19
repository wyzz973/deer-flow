"""DeepResearch configuration, independent of the DeerFlow host configuration.

DeerFlow is the execution engine: its subagent executor, model classes, sandbox
and middleware run the research. What runs is decided here: research models,
sources with their provider failover chains, MCP servers, roles, methodologies,
prompts and limits. The host ``config.yaml`` only registers this extension.
Older files that bind roles to host subagents, host models or host tools stay
readable for compatibility.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, PrivateAttr, model_validator

from .contracts import Contract, Identifier, Level, Origin, ResearchBudget
from .prompts import PromptSet

ROOT = Path(__file__).resolve().parents[2]
# The planner and the report writer are fixed roles; every other skill is a researcher.
FIXED_ROLES = ("deepresearch", "report-synthesis")
# A reference to a credential: "$ENV_NAME" or "secret:NAME" (saved on the settings page).
SecretRef = str

# Engine model classes behind each provider. "custom" names a class path.
MODEL_PROVIDERS = {
    "openai": "langchain_openai:ChatOpenAI",
    "deepseek": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
    "vllm": "deerflow.models.vllm_provider:VllmChatModel",
    "anthropic": "langchain_anthropic:ChatAnthropic",
}
# Model-facing tools of the engine that a role may be allowed to use. read_file
# lets a researcher continue a long tool result the engine moved to a file.
ENGINE_TOOLS = {
    "read_file": "deerflow.sandbox.tools:read_file_tool",
    "ls": "deerflow.sandbox.tools:ls_tool",
    "glob": "deerflow.sandbox.tools:glob_tool",
    "grep": "deerflow.sandbox.tools:grep_tool",
}


# A conversation id must never overwrite what authenticates or frames a request.
RESERVED_HEADERS = {"authorization", "proxy-authorization", "cookie", "host", "content-type", "content-length", "accept", "user-agent", "x-api-key", "api-key"}
RESERVED_PARAMS = {"model", "messages", "tools", "tool_choice", "stream", "stream_options", "max_tokens", "max_completion_tokens", "temperature", "top_p", "response_format", "stop", "n", "thinking"}


class ModelSpec(Contract):
    """A research model. Credentials are references, never literal keys."""

    name: Identifier
    display_name: str = Field(default="", max_length=120)
    provider: Literal["openai", "deepseek", "vllm", "anthropic", "custom"] = "openai"
    use: str | None = None  # Engine or LangChain class path for provider "custom".
    model: str = Field(min_length=1, max_length=300)
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: SecretRef | None = Field(default=None, max_length=300)
    max_tokens: int | None = Field(default=None, ge=128, le=393216)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    timeout_seconds: float = Field(default=600, gt=0, le=7200)
    max_retries: int = Field(default=2, ge=0, le=10)
    # Ask for token usage in streamed answers (``stream_options.include_usage``).
    # OpenAI-compatible clients switch this off as soon as base_url is set, which
    # leaves a model gateway without any token or cost figures. None turns it
    # on for the OpenAI-compatible providers; set false for a gateway that
    # rejects stream_options.
    stream_usage: bool | None = None
    # Name of the output-cap parameter. OpenAI renamed max_tokens to
    # max_completion_tokens; gateways and servers that still implement the
    # earlier Chat Completions protocol ignore or reject the new name. None
    # sends max_tokens to any OpenAI-compatible base_url other than OpenAI's.
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] | None = None
    # A prompt-prefix cache lives on one replica. A gateway that spreads requests
    # over replicas serves a conversation from cache only when it can recognise
    # the conversation, so research can send the conversation's id with every
    # request: as a request field (OpenAI's ``prompt_cache_key``; some gateways
    # hash ``user``) and/or as a header (for example ``x-session-affinity``).
    # Nothing is sent unless named here, because a strict gateway rejects
    # unknown fields; only OpenAI's own endpoint gets ``prompt_cache_key`` unasked.
    session_param: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    session_header: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
    # The provider can switch reasoning on and off. Research always runs with it
    # off; the engine sends the provider-specific switch.
    supports_thinking: bool = False
    # Extra constructor arguments (for example extra_body or default_headers).
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_provider(self):
        if self.provider == "custom" and not self.use:
            raise ValueError("A custom model provider needs a class path in use")
        if self.api_key is not None and not self.api_key.startswith(("$", "secret:")):
            raise ValueError("api_key must reference an environment variable ($NAME) or a saved secret (secret:NAME)")
        if self.session_header and self.session_header.lower() in RESERVED_HEADERS:
            raise ValueError("session_header cannot replace a transport or credential header")
        if self.session_param and self.session_param in RESERVED_PARAMS:
            raise ValueError("session_param cannot replace a field research sends itself")
        return self


class McpServerSpec(Contract):
    """An MCP server used only by DeepResearch sources."""

    transport: Literal["stdio", "http", "sse", "streamable_http"] = "http"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    # Values may reference $ENV or secret:NAME.
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=60, gt=0, le=3600)
    enabled: bool = True
    description: str = Field(default="", max_length=2000)
    # Tools of this server research may call. None allows every tool a source or
    # provider names; a list is an allowlist enforced at load time and again
    # when the tool is resolved, so a server that grows new tools exposes none
    # of them to research until the operator lists them.
    allowed_tools: list[str] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def valid_transport(self):
        if self.transport == "stdio" and not self.command:
            raise ValueError("A stdio MCP server needs a command")
        if self.transport != "stdio" and not self.url:
            raise ValueError("A remote MCP server needs a url")
        return self


# Provider types by research role. "http" is a custom HTTP API and "mcp" calls a
# tool of a configured MCP server; the others are built-in API presets.
SEARCH_PROVIDERS = ("tavily", "serper", "brave", "exa", "bocha", "searxng", "jina_search", "duckduckgo")
READ_PROVIDERS = ("jina_reader", "tavily_extract", "firecrawl", "direct")
DATA_PROVIDERS = ("ragflow", "lightrag")
GENERIC_PROVIDERS = ("http", "mcp")


class ProviderSpec(Contract):
    """One backend of a source. Providers are tried in order until one succeeds."""

    id: Identifier
    type: Literal[SEARCH_PROVIDERS + READ_PROVIDERS + DATA_PROVIDERS + GENERIC_PROVIDERS]
    enabled: bool = True
    api_key: SecretRef | None = Field(default=None, max_length=300)
    base_url: str | None = Field(default=None, max_length=2000)
    # Preset options (for example search_depth, dataset_ids, mode, region).
    options: dict[str, Any] = Field(default_factory=dict)
    # Custom HTTP API. Templates may use {query}, {max_results}, {url} and {time_range};
    # header values may reference $ENV or secret:NAME.
    method: Literal["GET", "POST"] = "GET"
    url: str | None = Field(default=None, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    # MCP: a tool of a server in mcp_servers. arguments uses the same templates;
    # empty arguments are inferred from the tool's input schema.
    server: str | None = None
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    # A private-network endpoint for direct page reads (intranet documents).
    allow_private_network: bool = False

    @model_validator(mode="after")
    def valid_provider(self):
        if self.api_key is not None and not self.api_key.startswith(("$", "secret:")):
            raise ValueError("api_key must reference an environment variable ($NAME) or a saved secret (secret:NAME)")
        if self.type == "http" and not self.url:
            raise ValueError("A custom HTTP provider needs a url")
        if self.type == "mcp" and not (self.server and self.tool):
            raise ValueError("An MCP provider needs server and tool")
        if self.type in {"searxng", "ragflow", "lightrag"} and not self.base_url:
            raise ValueError(f"The {self.type} provider needs base_url")
        return self


class SkillSpec(Contract):
    """One research role: its prompt, methodology, model, tools and limits.

    A role is self-contained. ``agent`` (binding a DeerFlow subagent) is read
    only for compatibility with older configuration files.
    """

    agent: Identifier | None = None
    path: str | None = None
    methodology: str | None = Field(default=None, max_length=100000)
    name: str = Field(default="", max_length=80)
    description: str = Field(min_length=1, max_length=2000)
    model: str | None = None
    system_prompt: str = Field(default="", max_length=60000)
    # Tool allowlist by model-facing name: source tools and engine tools.
    # None allows every configured source tool plus engine_tools; [] allows none.
    tools: list[str] | None = None
    enabled: bool = True
    # The engine interprets this as graph recursion steps, not model exchanges.
    max_turns: int | None = Field(default=None, ge=1, le=1000)
    # A deep-research unit reads many pages; the native Agent timeout still applies.
    # Slow self-hosted models need far longer than hosted ones for the same step.
    timeout_seconds: int = Field(default=600, ge=5, le=14400)

    @model_validator(mode="after")
    def has_methodology(self):
        if self.path is None and self.methodology is None:
            raise ValueError("A research role needs a methodology text or a Skill file path")
        return self


class SourceSpec(Contract):
    """A research source exposed to researchers as one tool.

    ``providers`` makes it a DeepResearch source: a stable tool (``tool``) whose
    backends fail over in order. ``kind: mcp`` exposes a tool of a configured
    MCP server with its own schema. ``kind: native`` and an MCP ``server`` that
    is not in ``mcp_servers`` bind host tools (older configuration files).
    """

    name: Identifier
    origin: Origin
    kind: Literal["channel", "mcp", "native"] = "channel"
    # A disabled source stays configured but is never offered to planners or
    # researchers, e.g. web search switched off where only MCP tools
    # may be used. Evidence it produced in earlier runs stays citable.
    enabled: bool = True
    tool: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # How the tool participates in research, chosen by the operator rather than
    # inferred from its payload: search results are discovery only, read tools
    # open documents, data tools return citable records (e.g. a knowledge base).
    role: Literal["search", "read", "data"] = "data"
    level: Level = "L4"
    priority: int = 100
    publisher: str = "unclassified"
    # Model-facing description; a role-specific default is used when empty.
    description: str = Field(default="", max_length=4000)
    providers: list[ProviderSpec] = Field(default_factory=list, max_length=20)
    server: str | None = None
    # The MCP tool a ``kind: mcp`` source calls on a DeepResearch MCP server,
    # when its name differs from the model-facing ``tool`` name.
    mcp_tool: str | None = Field(default=None, max_length=300)
    # Accepted only for migration from V1. These legacy fields never modify a tool call.
    query_arg: str = Field(default="query", deprecated=True)
    fixed_args: dict = Field(default_factory=dict)
    results_path: str = "results"
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

    @model_validator(mode="before")
    @classmethod
    def infer_kind(cls, value):
        # Older files omitted kind for MCP bindings (then the default).
        if isinstance(value, dict) and "kind" not in value:
            value = {**value, "kind": "channel" if value.get("providers") else "mcp" if value.get("server") else "channel"}
        return value

    @model_validator(mode="after")
    def valid_binding(self):
        if self.kind == "channel":
            if not self.providers:
                raise ValueError(f"Source {self.name} needs at least one provider")
            if self.server is not None:
                raise ValueError("Provider-based sources select MCP servers per provider")
            ids = [provider.id for provider in self.providers]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Provider ids must be unique within source {self.name}")
            allowed = {"search": SEARCH_PROVIDERS, "read": READ_PROVIDERS, "data": DATA_PROVIDERS}[self.role] + GENERIC_PROVIDERS
            wrong = [provider.type for provider in self.providers if provider.type not in allowed]
            if wrong:
                raise ValueError(f"Provider types {wrong} cannot serve a {self.role} source")
        elif self.providers:
            raise ValueError("Only provider-based sources declare providers")
        if self.kind == "mcp" and not self.server:
            raise ValueError("MCP sources need an explicit server name")
        if self.kind == "native" and self.server is not None:
            raise ValueError("Native sources do not select an MCP server")
        return self


def methodology_text(text: str) -> str:
    """A Skill file's body without its YAML front matter (name/description metadata)."""
    match = re.match(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", text, re.S)
    return text[match.end() :].strip() if match else text.strip()


class ModelPricing(Contract):
    """Operator-entered list prices per million tokens; never fetched or assumed."""

    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    # Prompt-cache hits; omitted means cached input is billed as normal input.
    cached_input_per_million: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=8)


class CompactionSpec(Contract):
    """When and how a role's context is compacted mid-execution.

    Long researcher loops outgrow any context window: opened pages alone are
    thousands of tokens each. The engine compacts the conversation and keeps a
    summary; these fields decide when that happens and how much recent work
    survives verbatim. They are research settings, independent of the host's
    own chat summarization thresholds.
    """

    enabled: bool = True
    # Share of the model's declared context window that triggers compaction.
    trigger_fraction: float = Field(default=0.6, gt=0, le=1)
    # Used when the model declares no context window.
    fallback_trigger_tokens: int = Field(default=48000, ge=2000, le=2_000_000)
    # Share of the trigger kept verbatim as the most recent messages. Retention
    # is measured in tokens, not messages: a fetched page is worth many turns of
    # notes, and a message-count policy whose tail already exceeds the trigger
    # would compact on every single turn without ever shrinking the context.
    keep_fraction: float = Field(default=0.4, gt=0, lt=1)
    # Upper bound on the text handed to the summary model.
    max_summary_input_tokens: int = Field(default=24000, ge=1000, le=400_000)
    # Research model that writes the summary; empty uses the role's own model.
    model: str | None = None


# Workflow nodes that call a model. "research" covers every researcher role;
# the writer's three tasks and the planner's follow-up classification are
# separate because each wants different sampling: a plan or outline must be
# valid JSON (low temperature, short), a section is long prose.
NODE_NAMES = ("rewrite", "plan", "research", "conversion", "outline", "section", "summary", "revision", "follow_up")
NodeName = Literal[NODE_NAMES]
# Nodes research can do without: the raw request is planned as it stands, and
# the executive summary falls back to the outline's key conclusions.
OPTIONAL_NODES = ("rewrite", "summary")
GAP_CODES = ("coverage", "unsupported", "missing-internal", "missing-external", "date", "open-questions")


class NodeSpec(Contract):
    """Model and call parameters of one workflow node; unset fields inherit.

    Tuning a node means choosing which model answers it and how: a slow
    self-hosted model may plan and convert at temperature 0 with a short
    output cap while sections are written by a larger model, and a gateway
    that only speaks Chat Completions needs nothing beyond these fields.
    """

    # Only OPTIONAL_NODES may be switched off.
    enabled: bool = True
    # A research model by name. Researchers keep their own role model first.
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    # Output cap of one call of this node; empty uses max_output_tokens.
    max_tokens: int | None = Field(default=None, ge=128, le=393216)
    # One execution of the node: the whole agent loop of a research step, or one
    # direct call for rewrite and conversion. Empty uses the role's timeout.
    timeout_seconds: int | None = Field(default=None, ge=5, le=14400)
    # Bounded repair attempts when the answer misses its contract; empty uses output_retries.
    output_retries: int | None = Field(default=None, ge=0, le=4)
    # Ask the provider for a JSON object (``response_format``). Only direct
    # calls (rewrite, conversion) can use it, and only on gateways that support it.
    json_mode: bool = False
    # Merged into the request body, e.g. {"repetition_penalty": 1.05} or a
    # gateway-specific switch.
    extra_body: dict[str, Any] = Field(default_factory=dict)


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
    # Report sections written at once; empty uses max_concurrency. A gateway
    # that serves one slow model often wants fewer parallel long generations
    # than parallel research steps, or the reverse.
    writer_concurrency: int | None = Field(default=None, ge=1, le=16)
    max_active_runs: int = Field(default=8, ge=1, le=100)
    plan_countdown_seconds: float = Field(default=45, ge=0.05, le=600)
    tool_timeout_seconds: int = Field(default=45, ge=1, le=300)
    tool_retries: int = Field(default=1, ge=0, le=3)
    max_output_tokens: int = Field(default=8192, ge=128, le=393216)
    # Searches (and page reads) one research step may make. The step is told its
    # allowance and, once it is used, its source tools answer with a stop
    # instruction instead of ending the run with BUDGET_EXHAUSTED. null leaves
    # only the deployment ceiling (budget_ceiling.max_tool_calls).
    max_searches_per_unit: int | None = Field(default=30, ge=1, le=500)
    # Soft time allowance of one research step. After it, the step's source
    # tools answer with a stop instruction and the researcher writes its notes
    # from what it has; the role timeout stays the hard limit. null: no allowance.
    max_seconds_per_unit: int | None = Field(default=None, ge=30, le=14400)
    # Time kept back from budget.max_elapsed_seconds for writing the report.
    # Research winds down when only this much is left instead of the whole run
    # failing at the ceiling. null: a quarter of the ceiling, 120-900 seconds.
    report_time_reserve_seconds: int | None = Field(default=None, ge=30, le=7200)
    # Findings one research step hands to the writer. The converter is asked for
    # the ones that decide the answer and surplus ones are cut: every finding
    # is copied into the outline and its section, and a converter that lists
    # twenty spends minutes of a slow model's time on each step.
    max_findings_per_unit: int = Field(default=12, ge=1, le=100)
    # How many research steps the planner is asked for.
    plan_min_units: int = Field(default=3, ge=1, le=12)
    plan_max_units: int = Field(default=6, ge=1, le=24)
    # Gaps that start another round of research. An empty list never
    # supplements: every gap becomes a stated limitation of the report.
    supplement_gap_codes: list[Literal[GAP_CODES]] = Field(default_factory=lambda: list(GAP_CODES))
    # Per-node model and call parameters; see NodeSpec.
    nodes: dict[NodeName, NodeSpec] = Field(default_factory=dict)
    output_retries: int = Field(default=2, ge=0, le=4)
    extraction_model: str | None = None
    native_tools: list[str] | None = None  # Optional ceiling; native Agent/Skill authorization is authoritative.
    trace_capture_content: bool = True
    trace_max_chars: int = Field(default=16000, ge=1000, le=100000)
    # Complete prompts and answers of every model call (requires trace_capture_content).
    llm_audit: bool = True
    # Every instruction sent to models; defaults live in prompts.py.
    prompts: PromptSet = Field(default_factory=PromptSet)
    # Context compaction for long role executions (engine summarization).
    compaction: CompactionSpec = Field(default_factory=CompactionSpec)
    # The engine can show a role the ledger of its own tool calls. The ledger
    # is rewritten right after the system prompt on every turn, so everything
    # behind it stops being a reusable prompt prefix: researcher loops measured
    # 4% cache hits with it and an append-only conversation without it. Evidence
    # is read from the tool messages, never from that ledger, so it stays off.
    tool_receipt_ledger: bool = False
    # Research models. Roles, rewriting and extraction refer to them by name;
    # default_model serves every role without its own model.
    models: list[ModelSpec] = Field(default_factory=list, max_length=50)
    default_model: str | None = None
    rewrite_model: str | None = None
    mcp_servers: dict[str, McpServerSpec] = Field(default_factory=dict)
    # Engine tools every role may use unless its own allowlist says otherwise.
    engine_tools: list[Literal[tuple(ENGINE_TOOLS)]] = Field(default_factory=lambda: ["read_file"])
    # When bounded supplementation cannot close every gap, write the report
    # with explicit caveats (ChatGPT-style) instead of failing. Evidence-free
    # runs still fail, and strict deployments can require owner consent.
    allow_limited_report: bool = True
    # Search results and links merely seen in tool output are discovery aids.
    # Enable only for deployments whose search tool returns full documents.
    cite_search_results: bool = False
    max_synthesis_repairs: int = Field(default=1, ge=0, le=3)
    max_report_sections: int = Field(default=8, ge=2, le=12)
    # Multiplies the per-section and summary length targets of the plan's report
    # style. Models follow a length loosely: one question gave about 29,000
    # characters at 1.0 and about 18,000 at 0.45 (ChatGPT deep research wrote
    # about 5,000 plus tables). Below 1, a section far past its ceiling gets one
    # shortening repair. Guidance, never a failure.
    report_length_scale: float = Field(default=1.0, ge=0.2, le=3.0)
    # Site icons for cited domains are fetched by the gateway (public hosts only).
    # Disable for deployments without outbound access; the UI shows letter badges.
    favicons: bool = True
    # Model name -> list prices, used only to estimate research cost in metrics.
    # Excluded from the configuration fingerprint, so a price update never
    # blocks resuming earlier runs.
    pricing: dict[str, ModelPricing] = Field(default_factory=dict)
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
        if not any(spec.enabled for name, spec in self.skills.items() if name not in FIXED_ROLES):
            raise ValueError("Registry needs at least one enabled researcher skill")
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("Source names must be unique")
        tools = [source.tool for source in self.sources if source.kind == "channel"]
        if len(tools) != len(set(tools)):
            raise ValueError("Source tool names must be unique")
        models = [model.name for model in self.models]
        if len(models) != len(set(models)):
            raise ValueError("Model names must be unique")
        for source in self.sources:
            for provider in source.providers:
                if provider.type == "mcp" and provider.server not in self.mcp_servers:
                    raise ValueError(f"Provider {source.name}/{provider.id} uses an unknown MCP server: {provider.server}")
        # The MCP allowlist is checked where the file is loaded, not only when a
        # researcher first calls the tool in the middle of a run.
        for label, server, tool in self.mcp_bindings():
            allowed = self.mcp_servers[server].allowed_tools if server in self.mcp_servers else None
            if allowed is not None and tool not in allowed:
                raise ValueError(f"{label} uses MCP tool {tool}, which is not in mcp_servers.{server}.allowed_tools")
        if models:
            # With research models configured, every reference must name one of
            # them. Files without models still refer to host models (older setups).
            references = {"default_model": self.default_model, "rewrite_model": self.rewrite_model, "extraction_model": self.extraction_model, "compaction.model": self.compaction.model}
            references |= {f"skills.{name}.model": spec.model for name, spec in self.skills.items()}
            references |= {f"nodes.{name}.model": spec.model for name, spec in self.nodes.items()}
            unknown = {field: value for field, value in references.items() if value and value not in models}
            if unknown:
                raise ValueError("Unknown research model: " + ", ".join(f"{field}={value}" for field, value in unknown.items()))
        required = [name for name, spec in self.nodes.items() if not spec.enabled and name not in OPTIONAL_NODES]
        if required:
            raise ValueError("Only the rewrite and summary nodes can be disabled: " + ", ".join(required))
        if self.plan_min_units > self.plan_max_units:
            raise ValueError("plan_min_units cannot exceed plan_max_units")
        active = self.active_sources()
        if self.runner == "deerflow" and not active:
            raise ValueError("DeerFlow mode needs at least one enabled source")
        if self.runner == "deerflow" and self.require_dual_source and {s.origin for s in active} != {"internal", "external"}:
            raise ValueError("DeerFlow mode needs explicit internal and external source bindings")
        if not set(self.source_fallback).issubset(names):
            raise ValueError("Unknown fallback source")
        return self

    def active_sources(self):
        """Sources research may use; a disabled one stays configured but is never offered."""
        return [source for source in self.sources if source.enabled]

    def mcp_bindings(self):
        """(label, server, tool) for every MCP tool a source or provider calls."""
        found = []
        for source in self.sources:
            if source.kind == "mcp" and source.server:
                found.append((f"Source {source.name}", source.server, source.mcp_tool or source.tool))
            for provider in source.providers:
                if provider.type == "mcp":
                    found.append((f"Provider {source.name}/{provider.id}", provider.server, provider.tool))
        return found

    def node(self, name: str) -> NodeSpec:
        """Parameters of a workflow node; an unconfigured node inherits everything."""
        return self.nodes.get(name) or NodeSpec()

    def resolve(self, path: str) -> Path:
        value = Path(path).expanduser()
        return value.resolve() if value.is_absolute() else (ROOT / value).resolve()

    def read_skill(self, name: str) -> str:
        if name in self._skill_cache:
            return self._skill_cache[name]
        spec = self.skills.get(name)
        if spec is None:
            raise ValueError(f"Unknown skill: {name}")
        if spec.methodology is not None:
            return spec.methodology
        path = self.resolve(spec.path)
        text = path.read_text(encoding="utf-8")
        if len(text) > 100000:
            raise ValueError(f"Skill too large: {name}")
        return text

    def methodology(self, name: str) -> str:
        """A role's methodology without Skill-file front matter (name/description metadata)."""
        return methodology_text(self.read_skill(name))

    def researchers(self):
        return {name: spec for name, spec in self.skills.items() if name not in FIXED_ROLES and spec.enabled}

    def fit_origins(self, plan):
        """Drop required origins that no configured source serves.

        Only for deployments that do not require both origins. A planner (weak
        local models especially) may keep the internal+external default even
        when only one kind of source exists, which would deny every unit.
        """
        available = {source.origin for source in self.active_sources()}
        if self.require_dual_source or not available:
            return plan
        for unit in plan.research_units:
            strategy = unit.source_strategy
            strategy.required_origins = [origin for origin in strategy.required_origins if origin in available] or sorted(available)
        return plan

    def check_plan(self, plan, budget, request_sources=()):
        if len(plan.research_units) > budget.max_units:
            raise ValueError("Plan exceeds max_units")
        allowed = {s.name for s in self.active_sources()}
        if not set(request_sources).issubset(allowed):
            raise ValueError("Unknown requested source")
        researchers = self.researchers()
        for unit in plan.research_units:
            if unit.skill not in researchers:
                raise ValueError(f"Unsupported researcher skill: {unit.skill}")
            if not set(unit.source_strategy.source_names).issubset(allowed):
                raise ValueError("Plan selected an unknown source")
            if self.require_dual_source and set(unit.source_strategy.required_origins) != {"internal", "external"}:
                raise ValueError("This deployment requires both internal and external research")


# The settings of the run being executed. The service binds a run's own
# configuration snapshot here, so edits made while it runs never change it.
_active: ContextVar[Settings | None] = ContextVar("research_settings", default=None)


def active_settings(default: Settings) -> Settings:
    return _active.get() or default


@contextmanager
def use_settings(settings: Settings):
    token = _active.set(settings)
    try:
        yield settings
    finally:
        _active.reset(token)


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path) if path else ROOT / "deepresearch.example.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Settings.model_validate(raw)
