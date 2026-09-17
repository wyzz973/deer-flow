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
  /** Client-only monotonic receipt time; never sent back or persisted. */
  client_received_at?: number;
  cycle?: number;
  conversation?: ResearchMessage[];
  steering?: { id: string; text: string; at: string }[];
};
export type ResearchMessage = {
  id: string;
  role: "user" | "assistant";
  kind: "text" | "plan" | "report" | "clarification";
  text: string;
  at: string;
  plan?: Plan;
  report?: Report;
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
export function mergeEvents(
  old: ResearchEvent[],
  incoming: ResearchEvent,
): ResearchEvent[] {
  if (old.some((e) => e.seq === incoming.seq)) return old;
  return [...old, incoming].sort((a, b) => a.seq - b.seq).slice(-100);
}
