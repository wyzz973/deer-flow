import type {
  Capabilities,
  ResearchActivity,
  Run,
} from "@/core/deepresearch/types";

export function makeRun(
  runId = "run",
  status = "AWAITING_PLAN_CONFIRMATION",
): Run {
  const budget = {
    max_iterations: 2,
    max_units: 3,
    max_tool_calls: 60,
    max_elapsed_seconds: 900,
    max_model_tokens: 120000,
  };
  const plan: NonNullable<Run["plan"]> = {
    goal: "Compare databases",
    constraints: [],
    expected_output: "report",
    plan_version: 1,
    research_units: [
      {
        id: "U1",
        skill: "technical-route",
        objective: "Compare concurrency",
        priority: 1,
        depends_on: [],
        parent_gap_id: null,
        source_strategy: {
          source_names: [],
          required_origins: ["external"],
          max_results: 4,
          not_before: null,
        },
      },
    ],
  };
  return {
    run_id: runId,
    thread_id: `thread-${runId}`,
    query: "Compare databases",
    status,
    created_at: "2026-09-16T00:00:00Z",
    updated_at: "2026-09-16T00:00:00Z",
    plan,
    units: plan.research_units,
    unit_statuses: {},
    evidence_count: 0,
    iteration: 0,
    gaps: [],
    usage: {
      tool_calls: 0,
      model_tokens: 0,
      reported_model_tokens: 0,
      elapsed_seconds: 0,
    },
    report: null,
    error: null,
    demo: true,
    budget,
    conversation: [],
    server_time: "2026-09-16T00:00:00Z",
    auto_start_at: "2026-09-16T00:00:45Z",
  };
}

export function capabilities(): Capabilities {
  return {
    mode: "demo",
    ready: true,
    skills: {},
    sources: [],
    budget_ceiling: makeRun().budget,
    plan_countdown_seconds: 45,
  };
}

export function activity(
  status = "AWAITING_PLAN_CONFIRMATION",
): ResearchActivity {
  return {
    status,
    started_at: null,
    finished_at: null,
    elapsed_seconds: null,
    counts: { searches: 0, pages_read: 0, steps: 1, steps_done: 0 },
    current: null,
    items: [],
  };
}
