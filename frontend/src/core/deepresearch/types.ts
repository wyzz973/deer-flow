export type Origin = "internal" | "external";
export type Budget = {
  max_iterations: number;
  max_units: number;
  max_tool_calls: number;
  max_elapsed_seconds: number;
  max_model_tokens: number;
};
export type Unit = {
  id: string;
  skill: string;
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
  research_units: Unit[];
  constraints: string[];
  expected_output: string;
  plan_version: number;
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
};
export type Report = {
  version: number;
  report: {
    title: string;
    executive_summary: Segment[];
    sections: { heading: string; unit_ids: string[]; segments: Segment[] }[];
    conclusion: Segment[];
  };
  markdown: string;
  citations: (Evidence & { number: number })[];
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
};
export type Capabilities = {
  mode: string;
  ready: boolean;
  skills: Record<string, { description: string; agent: string }>;
  sources: { name: string; origin: Origin; level: string }[];
  budget_ceiling: Budget;
};
export type ResearchEvent = {
  seq: number;
  type: string;
  run_id: string;
  at: string;
  data: Record<string, unknown>;
};
export const terminal = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
export function mergeEvents(
  old: ResearchEvent[],
  incoming: ResearchEvent,
): ResearchEvent[] {
  if (old.some((e) => e.seq === incoming.seq)) return old;
  return [...old, incoming].sort((a, b) => a.seq - b.seq).slice(-100);
}
