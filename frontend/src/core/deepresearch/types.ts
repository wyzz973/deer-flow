export type Origin = "internal" | "external";
export type Budget = {
  max_iterations: number;
  max_units: number;
  max_tool_calls: number | null;
  max_elapsed_seconds: number | null;
  max_model_tokens: number | null;
};
export type Unit = {
  id: string;
  skill: string;
  /** Short user-facing step label; historical plans only have an objective. */
  title?: string;
  objective: string;
  priority: number;
  depends_on: string[];
  parent_gap_id: string | null;
  source_strategy: {
    source_names: string[];
    required_origins: Origin[];
    max_results: number;
    not_before: string | null;
  };
};
export type Plan = {
  goal: string;
  title?: string;
  /** Rewritten research brief shared by researchers and the writer. */
  brief?: string;
  research_units: Unit[];
  constraints: string[];
  assumptions?: string[];
  acknowledgement?: string;
  expected_output: string;
  plan_version: number;
  clarification_questions?: string[];
  report_style?: "brief" | "standard" | "detailed";
  source_policy?: {
    allowed_domains: string[];
    excluded_url_prefixes: string[];
    require_original: boolean;
  };
};
export type Segment = {
  text: string;
  evidence_ids: string[];
  segment_type: "fact" | "analysis" | "recommendation";
};
export type Evidence = {
  evidence_id: string;
  url?: string | null;
  canonical_url: string | null;
  source_uri: string | null;
  title: string;
  origin: Origin | "runtime";
  source_level: string;
  publisher: string;
  published_at: string | null;
  snippet: string;
  unit_ids: string[];
  source_id?: string | null;
  provenance?:
    | "document"
    | "tool_output"
    | "observed_source"
    | "fetched_document";
  document_hash?: string | null;
};
export type ReportStats = {
  elapsed_seconds: number | null;
  searches: number;
  pages_read: number;
  citations: number;
};
export type Citation = Evidence & {
  number: number;
  domain?: string;
  /** What the citation rests on: the page itself, a tool record, or only what
   * a search tool showed. Reports from before the field omit it. */
  basis?: "page" | "record" | "search excerpt";
  evidence_ids?: string[];
  excerpts?: { evidence_id: string; text: string }[];
};
/** A published, immutable report version.
 * `markdown-v2` reports are Markdown documents whose citations were bound by
 * the server; historical reports keep the structured `report` AST. */
export type Report = {
  version: number;
  format?: "markdown-v2";
  title?: string;
  document?: string;
  display_markdown?: string;
  toc?: { level: number; text: string }[];
  assumptions?: string[];
  stats?: ReportStats;
  report: {
    title: string;
    executive_summary?: Segment[];
    comparison_table?: {
      headers: string[];
      rows: { label: string; cells: Segment[] }[];
    } | null;
    sections?: { heading: string; unit_ids: string[]; segments: Segment[] }[];
    conclusion?: Segment[];
  };
  markdown: string;
  citations: Citation[];
  citation_map: Record<string, number>;
  limitations: string[];
  demo: boolean;
};
export type Run = {
  run_id: string;
  thread_id: string;
  query: string;
  status: string;
  created_at: string;
  updated_at: string;
  plan: Plan | null;
  units: Unit[];
  unit_statuses: Record<string, string>;
  /** Steps that could not finish; research continued and discloses the gap. */
  unit_failures?: Record<string, string>;
  evidence_count: number;
  iteration: number;
  gaps: { gap_id: string; description: string }[];
  usage: {
    tool_calls: number;
    model_tokens: number;
    reported_model_tokens: number;
    elapsed_seconds: number;
  };
  report?: Report | null;
  error: { code: string; message: string; recoverable: boolean } | null;
  demo: boolean;
  budget: Budget;
  auto_start_at?: string | null;
  auto_start_paused?: boolean;
  server_time?: string;
  /** Newest event when this snapshot was read; the stream continues from it. */
  last_event_seq?: number;
  /** Client-only monotonic receipt time; never sent back or persisted. */
  client_received_at?: number;
  cycle?: number;
  conversation?: ResearchMessage[];
  steering?: { id: string; text: string; at: string }[];
  /** Current rewritten research request (the brief researchers share). */
  request?: {
    user_query: string;
    acknowledgement?: string;
    clarification_questions?: string[];
  } | null;
  /** Settings snapshot this run executes with. */
  profile?: { hash: string; version: number } | null;
};
export type ResearchMessage = {
  id: string;
  role: "user" | "assistant";
  kind: "text" | "plan" | "report" | "clarification" | "rewrite";
  text: string;
  at: string;
  plan?: Plan;
  report?: Report;
  /** The conversation rewritten into one complete research request. */
  rewrite?: {
    user_query: string;
    revision: boolean;
    clarification_questions?: string[];
  };
  cycle?: number;
  /** A user update accepted while research was running. */
  steering?: boolean;
};
export type DiscoveredSource = {
  id: string;
  url: string;
  domain: string;
  title: string;
  title_observed: boolean;
  connector: string | null;
  origin: Origin | "runtime";
  status: "discovered" | "read";
  call_ids: string[];
  excerpt: string;
};
export type ResearchCall = {
  id: string;
  tool_name: string;
  agent_name?: string;
  unit_id?: string;
  execution_id?: string;
  provider_call_id?: string;
  started_at?: string;
  ended_at?: string;
  status: "running" | "success" | "error";
  duration_ms?: number;
  domains?: string[];
  source_ids?: string[];
  error_type?: string;
};
export type ResearchSources = {
  sources: DiscoveredSource[];
  calls: ResearchCall[];
};
export type ActivityItem =
  | { kind: "plan"; at: string; title: string; version?: number }
  | { kind: "step"; at: string; unit_id: string; title: string }
  | { kind: "note"; at: string; unit_id?: string; text: string }
  | {
      kind: "search";
      at: string;
      unit_id?: string;
      count: number;
      queries: string[];
      domains: string[];
    }
  | {
      kind: "read";
      at: string;
      unit_id?: string;
      url?: string | null;
      domain: string;
      title: string;
      status?: string;
    }
  | {
      kind: "tool";
      at: string;
      unit_id?: string;
      name: string;
      status?: string;
    }
  | {
      kind: "step_done";
      at: string;
      unit_id: string;
      title: string;
      summary: string;
      findings?: number;
    }
  | {
      kind: "step_failed";
      at: string;
      unit_id: string;
      title: string;
      code?: string;
    }
  | { kind: "gap" | "limited"; at: string; count: number }
  | { kind: "update"; at: string; text: string }
  | { kind: "writing"; at: string }
  | { kind: "outline"; at: string; sections: string[] }
  | { kind: "section"; at: string; title: string }
  | { kind: "done"; at: string; title: string; version?: number }
  | {
      kind: "failed" | "cancelled";
      at: string;
      code?: string;
      message?: string;
    };
export type ActivityCurrent = {
  kind:
    | "planning"
    | "responding"
    | "writing"
    | "note"
    | "search"
    | "read"
    | "researching";
  text?: string;
  title?: string;
  query?: string;
  url?: string;
  domain?: string;
};
/** Concise, user-facing research timeline; the Trace stays the debug record. */
export type ResearchActivity = {
  status: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number | null;
  counts: {
    searches: number;
    pages_read: number;
    steps: number;
    steps_done: number;
  };
  current: ActivityCurrent | null;
  items: ActivityItem[];
};
export type Capabilities = {
  mode: string;
  ready: boolean;
  skills: Record<string, { description: string; agent: string }>;
  sources: { name: string; origin: Origin; level: string }[];
  budget_ceiling: Budget;
  plan_countdown_seconds: number;
};
export type ResearchEvent = {
  seq: number;
  type: string;
  run_id: string;
  at: string;
  data: Record<string, unknown>;
};
export const terminal = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
export const reviewStatuses = new Set([
  "AWAITING_PLAN_CONFIRMATION",
  "EDITING_PLAN",
  "AWAITING_CLARIFICATION",
]);
/** Statuses in which a message becomes a non-interrupting research update. */
export const steerableStatuses = new Set([
  "RESEARCHING",
  "VALIDATING",
  "GAP_FOUND",
  "RESEARCH_COMPLETE",
  "SYNTHESIZING",
]);

export type LatencySummary = {
  count: number;
  avg: number | null;
  p50: number | null;
  p95: number | null;
  max: number | null;
};
/** Token, time, call and cost totals for one breakdown key (phase, model, unit…). */
export type MetricGroup = {
  key: string;
  model_calls: number;
  model_errors: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  model_ms: number;
  cost: number | null;
  priced_calls: number;
  tool_calls: number;
  tool_errors: number;
  tool_ms: number;
};
/** Model work of one graph node (GET /metrics `breakdown.by_node`). */
export type NodeMetric = {
  /** rewrite, plan, research, conversion, outline, section, summary,
   * revision, follow_up, compaction or unknown. */
  node: string;
  models: string[];
  model_calls: number;
  model_errors: number;
  /** Successful calls whose provider reported no usage. */
  unreported_usage: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  max_input_tokens: number | null;
  avg_output_tokens: number | null;
  /** Cached share of this node's input tokens, as the provider reported it. */
  cache_read_ratio?: number | null;
  /** Share of this node's prompt text that repeated the previous request of
   * its thread: the ceiling a prefix cache could serve. Null when every
   * request opened a new thread. */
  prefix_reuse_ratio?: number | null;
  /** Answers cut off by the output cap (`finish_reason: length`). */
  truncated: number;
  /** Extra attempts after JSON or citation validation failed. */
  retries: number;
  model_ms: number;
  latency_ms: LatencySummary;
  cost: number | null;
};
/** Usage of a task that already ended in this conversation. */
export type ClosedTaskUsage = {
  cycle: number | null;
  closed_at: string | null;
  model_tokens: number | null;
  tool_calls: number | null;
  elapsed_seconds: number | null;
};
export type AgentRunMetric = {
  id: string;
  skill: string | null;
  agent_name: string | null;
  unit_id: string | null;
  phase: string | null;
  cycle: number | null;
  status: string;
  error_code: string | null;
  stop_reason: string | null;
  model_calls: number | null;
  tool_calls: number | null;
  tool_errors: number | null;
  max_input_tokens: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
  seconds: number | null;
  cost: number | null;
};
/** What one research unit cost and contributed to the report. */
export type UnitOutcomeMetric = {
  unit_id: string;
  title: string | null;
  skill: string | null;
  supplement: boolean;
  failed: boolean;
  seconds: number | null;
  /** Null for runs from before per-call metering. */
  model_calls: number | null;
  total_tokens: number | null;
  cost: number | null;
  tool_calls: number;
  searches: number;
  pages_read: number;
  evidence: number;
  citations: number;
  tokens_per_citation: number | null;
};
/** Cost and efficiency of one research run (GET /metrics). */
export type ResearchMetrics = {
  run_id: string;
  status: string;
  generated_at: string;
  /** False for runs from before per-call metering: only ledger totals exist. */
  metered: boolean;
  time: {
    wall_seconds: number | null;
    active_seconds: number | null;
    waiting_seconds: number | null;
    model_seconds: number | null;
    tool_seconds: number | null;
    queue_seconds: number | null;
    in_progress: boolean;
    phases: { phase: string; seconds: number; runs: number; errors: number }[];
  };
  tokens: {
    input: number | null;
    output: number | null;
    cache_read: number | null;
    reasoning: number | null;
    total: number;
    unreported_calls: number | null;
    estimated_unreported: number | null;
    cache_read_ratio: number | null;
    /** Ceiling of what a prefix cache could serve; see MetricNode. */
    prefix_reuse_ratio?: number | null;
  };
  cost: {
    currency: string | null;
    total: number | null;
    by_currency: Record<string, number>;
    priced_calls: number;
    unpriced_models: string[];
  };
  model_calls: {
    count: number | null;
    running: number | null;
    errors: number | null;
    error_codes: Record<string, number>;
    finish_reasons: Record<string, number>;
    latency_ms: LatencySummary;
    max_input_tokens: number | null;
  };
  tools: {
    count: number;
    errors: number;
    error_rate: number | null;
    /** Exception class, `HTTP nnn`, `EmptyContent` or `ToolReturnedError`. */
    error_types: Record<string, number>;
    latency_ms: LatencySummary;
    output_chars: number;
    searches: number;
    reads: number;
    read_errors: number;
    pages_read: number;
    /** Identical requests (same tool and arguments) after one succeeded. */
    /** Null for runs recorded before request keys existed. */
    repeat_calls: number | null;
    repeat_calls_same_agent: number | null;
    failing_domains: {
      domain: string;
      reads: number;
      errors: number;
      error_types: Record<string, number>;
    }[];
    by_tool: {
      tool: string;
      role: string | null;
      count: number;
      errors: number;
      error_types: Record<string, number>;
      repeats: number | null;
      latency_ms: LatencySummary;
      output_chars: number;
    }[];
  };
  agents: {
    count: number | null;
    completed: number | null;
    failed: number | null;
    cancelled: number | null;
    failure_codes: Record<string, number>;
    max_parallel: number | null;
    by_skill: {
      skill: string;
      count: number;
      failed: number;
      seconds: number | null;
      avg_seconds: number | null;
      model_calls: number;
      tool_calls: number;
      total_tokens: number;
      cost: number | null;
    }[];
    runs: AgentRunMetric[];
  };
  research: {
    planned_units: number;
    supplement_units: number;
    iterations: number;
    failed_units: number;
    raw_evidence: number;
    evidence_pool: number | null;
    trimmed_evidence: number;
    pruned_references: number;
    conversion_retries: number;
    deferred_supplements: number;
  };
  units: UnitOutcomeMetric[];
  /** Share of each run limit consumed; a null maximum is unlimited. */
  budget: {
    max_model_tokens: number | null;
    model_tokens_used: number | null;
    max_tool_calls: number | null;
    tool_calls_used: number | null;
    max_elapsed_seconds: number | null;
    elapsed_used: number | null;
    /** Budgets are per task: a follow-up after the report opens a new one. */
    earlier_tasks?: ClosedTaskUsage[];
  };
  report: {
    versions: number;
    characters: number;
    sections: number;
    tables: number;
    diagrams: number;
    citations: number;
    cited_domains: number;
    draft_repairs: number;
    dropped_statements: number;
    validation_retries: number;
  };
  cache: { hits: number; by_kind: Record<string, number> };
  efficiency: {
    tokens_per_citation: number | null;
    cost_per_citation: number | null;
    active_seconds_per_citation: number | null;
    pages_read_per_citation: number | null;
    citations_per_page_read: number | null;
    searches_per_unit: number | null;
    repeat_tool_call_ratio: number | null;
    tokens_per_report_char: number | null;
    conversion_token_share: number | null;
    failed_agent_token_share: number | null;
    cache_read_ratio: number | null;
  };
  breakdown: {
    by_phase: MetricGroup[];
    by_purpose: MetricGroup[];
    by_skill: MetricGroup[];
    by_model: MetricGroup[];
    by_unit: MetricGroup[];
    by_cycle: MetricGroup[];
    /** Absent from a gateway older than per-node metrics. */
    by_node?: NodeMetric[];
  };
};

/** One model request and its response, with complete prompts (GET /llm-calls/{id}). */
export type AuditContentBlock = {
  type: string;
  text?: string;
  mime_type?: string | null;
  url?: string | null;
  bytes?: number | null;
  [key: string]: unknown;
};
export type AuditToolCall = {
  id?: string | null;
  name?: string | null;
  args?: unknown;
  error?: string | null;
};
export type AuditMessage = {
  role: string;
  content: string | AuditContentBlock[];
  name?: string;
  tool_call_id?: string;
  status?: string;
  tool_calls?: AuditToolCall[];
  invalid_tool_calls?: AuditToolCall[];
  reasoning?: string;
};
export type AuditGeneration = AuditMessage & {
  finish_reason?: string | null;
  response_model?: string | null;
  usage?: Record<string, unknown>;
};
export type LlmCallSummary = {
  id: string;
  audited: boolean;
  status?: string;
  model?: string;
  response_model?: string | null;
  phase?: string;
  purpose?: string;
  skill?: string;
  agent_name?: string;
  unit_id?: string;
  execution_id?: string;
  contract?: string;
  cycle?: number;
  group?: string;
  /** Engine graph node that made the call (agent model node, summarization…). */
  node?: string | null;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number | null;
  message_count?: number;
  roles?: Record<string, number>;
  system_chars?: number;
  prompt_chars?: number;
  tools?: string[];
  last_input?: { role: string; name?: string | null; preview: string } | null;
  preview?: string;
  output_chars?: number;
  reasoning_chars?: number;
  tool_calls?: string[] | number;
  finish_reason?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cache_read_tokens?: number | null;
  reasoning_tokens?: number | null;
  total_tokens?: number | null;
  usage?: Record<string, number | null> | null;
  error?: {
    code?: string;
    type?: string;
    status_code?: number;
    stage?: string;
  } | null;
  error_code?: string | null;
  params?: Record<string, unknown>;
};
/** In a detail response ``tools`` holds the tool schemas, not just their names. */
export type LlmCallDetail = Omit<LlmCallSummary, "tools"> & {
  messages: (AuditMessage | null)[];
  message_hashes: string[];
  tools: Record<string, unknown>[] | null;
  response?: { generations: AuditGeneration[] } | null;
  previous_call_id: string | null;
  repeated_prefix: number;
  /** Messages not present in the previous request of the same agent loop. */
  new_message_indexes?: number[];
  openai_request: Record<string, unknown>;
};

export type SecretStatus = "set" | "missing" | "none";
export type ModelSpec = {
  name: string;
  display_name?: string;
  provider: "openai" | "deepseek" | "vllm" | "anthropic" | "custom";
  use?: string | null;
  model: string;
  base_url?: string | null;
  api_key?: string | null;
  max_tokens?: number | null;
  context_window?: number | null;
  temperature?: number | null;
  top_p?: number | null;
  timeout_seconds?: number;
  max_retries?: number;
  /** Token usage in streamed answers; null lets the server decide per provider. */
  stream_usage?: boolean | null;
  /** Request field that carries the conversation id so a gateway can keep one
   * conversation on one replica (prefix caches are per replica); null sends
   * none, except prompt_cache_key on OpenAI's own endpoint. */
  session_param?: string | null;
  /** Header that carries the same id, e.g. x-session-affinity. */
  session_header?: string | null;
  /** Name of the output-cap parameter; null lets the server decide per base_url. */
  max_tokens_param?: "max_tokens" | "max_completion_tokens" | null;
  supports_thinking?: boolean;
  extra?: Record<string, unknown>;
};
export type RoleSpec = {
  agent?: string | null;
  path?: string | null;
  methodology?: string | null;
  name?: string;
  description: string;
  model?: string | null;
  system_prompt?: string;
  tools?: string[] | null;
  enabled?: boolean;
  max_turns?: number | null;
  timeout_seconds?: number;
};
export type ProviderSpec = {
  id: string;
  type: string;
  enabled?: boolean;
  api_key?: string | null;
  base_url?: string | null;
  options?: Record<string, unknown>;
  method?: "GET" | "POST";
  url?: string | null;
  headers?: Record<string, string>;
  params?: Record<string, unknown>;
  body?: Record<string, unknown> | null;
  server?: string | null;
  tool?: string | null;
  arguments?: Record<string, unknown>;
  /** Seconds one call may take; null takes the default for the kind (an MCP
   * provider follows its server's call timeout, the rest get 30 s). */
  timeout_seconds?: number | null;
  allow_private_network?: boolean;
};
export type SourceSpec = {
  name: string;
  origin: Origin;
  kind?: "channel" | "mcp" | "native";
  /** A disabled source stays configured but is never offered to research. */
  enabled?: boolean;
  tool: string;
  role: "search" | "read" | "data";
  level?: "L1" | "L2" | "L3" | "L4";
  priority?: number;
  publisher?: string;
  description?: string;
  providers: ProviderSpec[];
  server?: string | null;
  mcp_tool?: string | null;
  [key: string]: unknown;
};
export type McpServerSpec = {
  transport: "stdio" | "http" | "sse" | "streamable_http";
  command?: string | null;
  args?: string[];
  env?: Record<string, string>;
  url?: string | null;
  headers?: Record<string, string>;
  /** Connecting and listing tools; a server that is down should fail fast. */
  timeout_seconds?: number;
  /** One tool call's answer — the deadline an internal service actually needs. */
  call_timeout_seconds?: number;
  enabled?: boolean;
  description?: string;
  /** Tools research may call on this server; null allows any tool a source names. */
  allowed_tools?: string[] | null;
};
export type McpToolsResult = {
  ok: boolean;
  error?: string;
  kind?: string;
  message?: string;
  tools: {
    name: string;
    description: string;
    arguments: Record<string, unknown>;
    allowed?: boolean;
  }[];
};
export type NodeName =
  | "rewrite"
  | "plan"
  | "research"
  | "conversion"
  | "outline"
  | "section"
  | "summary"
  | "revision"
  | "follow_up";
/** Model and call parameters of one workflow node; unset fields inherit. */
export type NodeSpec = {
  enabled: boolean;
  model: string | null;
  temperature: number | null;
  top_p: number | null;
  max_tokens: number | null;
  timeout_seconds: number | null;
  output_retries: number | null;
  /** Whether this node's model reasons before answering; null inherits (off).
   * Only a model with supports_thinking can be switched on. */
  thinking: boolean | null;
  json_mode: boolean;
  extra_body: Record<string, unknown>;
};
export type CompactionSpec = {
  enabled: boolean;
  trigger_fraction: number;
  fallback_trigger_tokens: number;
  keep_fraction: number;
  max_summary_input_tokens: number;
  model: string | null;
};
/** The editable part of the research configuration (settings page). */
export type EditableSettings = {
  compaction: CompactionSpec;
  skills: Record<string, RoleSpec>;
  prompts: Record<string, string>;
  models: ModelSpec[];
  default_model: string | null;
  rewrite_model: string | null;
  extraction_model: string | null;
  mcp_servers: Record<string, McpServerSpec>;
  engine_tools: string[];
  sources: SourceSpec[];
  source_fallback: string[];
  require_dual_source: boolean;
  max_concurrency: number;
  plan_countdown_seconds: number;
  max_output_tokens: number;
  max_searches_per_unit: number | null;
  max_seconds_per_unit: number | null;
  max_findings_per_unit: number;
  report_time_reserve_seconds: number | null;
  plan_min_units: number;
  plan_max_units: number;
  supplement_gap_codes: string[];
  writer_concurrency: number | null;
  /** Only nodes that differ from full inheritance have a key. */
  nodes: Partial<Record<NodeName, NodeSpec>>;
  output_retries: number;
  allow_limited_report: boolean;
  cite_search_results: boolean;
  max_synthesis_repairs: number;
  max_report_sections: number;
  /** Multiplies the per-section and summary length targets (0.2–3.0).
   * Optional: a gateway from before the field does not send it. */
  report_length_scale?: number;
  trace_capture_content: boolean;
  llm_audit: boolean;
  pricing: Record<
    string,
    {
      input_per_million: number;
      output_per_million: number;
      cached_input_per_million?: number | null;
      currency: string;
    }
  >;
};
export type ProviderHealth = {
  key: string;
  source: string;
  provider: string;
  type: string;
  successes: number;
  failures: number;
  cooling: boolean;
  cooldown_seconds: number;
  last_error_kind?: string;
  last_error?: string;
  last_latency_ms?: number;
};
export type SettingsView = {
  version: number;
  editable: boolean;
  error: string | null;
  settings: EditableSettings;
  defaults: EditableSettings;
  overridden: string[];
  updated_at: string | null;
  operator: {
    runner: string;
    max_active_runs: number;
    favicons: boolean;
    budget_ceiling: Budget;
    native_tools: string[] | null;
  };
  catalog: {
    model_providers: {
      id: string;
      label: string;
      hint: string;
      base_url: string | null;
      use: string | null;
    }[];
    source_providers: {
      type: string;
      label: string;
      role: "search" | "read" | "data" | null;
      requires_key: boolean;
      key_env: string | null;
      base_url: string | null;
      docs: string | null;
    }[];
    engine_tools: { name: string; description: string }[];
    fixed_roles: string[];
    /** Workflow nodes in the order a research passes through them. */
    nodes: {
      name: NodeName;
      label: string;
      description: string;
      model_fallback: string;
      advice: string;
      optional: boolean;
    }[];
    gap_codes: { code: string; description: string }[];
    prompts: {
      key: string;
      stage: string;
      label: string;
      description: string;
      default: string;
    }[];
  };
  secrets: { saved: string[]; references: Record<string, SecretStatus> };
  health: ProviderHealth[];
};
export type ModelProbe = {
  model: string;
  ok: boolean;
  error?: string;
  use?: string;
  max_tokens?: number | null;
  context_window?: number | null;
  plain_reply?: { seconds: number; text: string };
  tool_call?: { seconds: number; tools: string[] };
  usage?: Record<string, number | null>;
  warnings?: string[];
};
export type ProviderProbe = {
  ok: boolean;
  ms: number;
  kind?: string;
  message?: string;
  count?: number;
  sample?: { title?: string | null; url?: string | null; snippet?: string }[];
};
