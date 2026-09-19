import { expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { ResearchMetricsPanel } from "@/components/deepresearch/metrics-panel";
import type { NodeMetric, ResearchMetrics } from "@/core/deepresearch/types";

const group = (key: string, total: number, cost: number | null) => ({
  key,
  model_calls: 1,
  model_errors: 0,
  input_tokens: total - 100,
  output_tokens: 100,
  cache_read_tokens: 0,
  reasoning_tokens: 0,
  total_tokens: total,
  model_ms: 1000,
  cost,
  priced_calls: cost == null ? 0 : 1,
  tool_calls: 0,
  tool_errors: 0,
  tool_ms: 0,
});
const latency = { count: 4, avg: 3500, p50: 3000, p95: 5000, max: 5000 };

const metrics: ResearchMetrics = {
  run_id: "r",
  status: "COMPLETED",
  generated_at: "2026-09-17T00:20:00+00:00",
  metered: true,
  time: {
    wall_seconds: 900,
    active_seconds: 810,
    waiting_seconds: 90,
    model_seconds: 74,
    tool_seconds: 3.7,
    queue_seconds: 1.5,
    in_progress: false,
    phases: [
      { phase: "planner", seconds: 25, runs: 1, errors: 0 },
      { phase: "dispatch", seconds: 600, runs: 1, errors: 0 },
      { phase: "citation_binder", seconds: 0.3, runs: 1, errors: 0 },
    ],
  },
  tokens: {
    input: 14500,
    output: 2500,
    cache_read: 8000,
    reasoning: 0,
    total: 17000,
    unreported_calls: 1,
    estimated_unreported: 5000,
    cache_read_ratio: 0.552,
  },
  cost: {
    currency: "USD",
    total: 0.0088,
    by_currency: { USD: 0.0088 },
    priced_calls: 3,
    unpriced_models: ["other"],
  },
  model_calls: {
    count: 5,
    running: 0,
    errors: 1,
    error_codes: { MODEL_TIMEOUT: 1 },
    finish_reasons: { stop: 1 },
    latency_ms: latency,
    max_input_tokens: 10000,
  },
  tools: {
    count: 4,
    errors: 1,
    error_rate: 0.25,
    error_types: { "HTTP 429": 1 },
    latency_ms: latency,
    output_chars: 12050,
    searches: 1,
    reads: 3,
    read_errors: 1,
    pages_read: 1,
    repeat_calls: 1,
    repeat_calls_same_agent: 0,
    failing_domains: [
      { domain: "b.com", reads: 1, errors: 1, error_types: { "HTTP 403": 1 } },
    ],
    by_tool: [
      {
        tool: "web_fetch",
        role: "read",
        count: 3,
        errors: 1,
        error_types: { "HTTP 429": 1 },
        repeats: 1,
        latency_ms: latency,
        output_chars: 8050,
      },
    ],
  },
  agents: {
    count: 3,
    completed: 2,
    failed: 1,
    cancelled: 0,
    failure_codes: { NATIVE_AGENT_TIMEOUT: 1 },
    max_parallel: 2,
    by_skill: [],
    runs: [
      {
        id: "e1",
        skill: "technical-route",
        agent_name: "researcher",
        unit_id: "R1",
        phase: "dispatch",
        cycle: 0,
        status: "failed",
        error_code: "NATIVE_AGENT_TIMEOUT",
        stop_reason: null,
        model_calls: 1,
        tool_calls: 3,
        tool_errors: 0,
        max_input_tokens: 10000,
        input_tokens: 10000,
        output_tokens: 500,
        cache_read_tokens: 8000,
        reasoning_tokens: 0,
        total_tokens: 10500,
        seconds: 240,
        cost: 0.0038,
      },
    ],
  },
  research: {
    planned_units: 2,
    supplement_units: 1,
    iterations: 1,
    failed_units: 1,
    raw_evidence: 3,
    evidence_pool: 12,
    trimmed_evidence: 35,
    pruned_references: 0,
    conversion_retries: 1,
    deferred_supplements: 0,
  },
  units: [
    {
      unit_id: "R1",
      title: "技术路线",
      skill: "technical-route",
      supplement: false,
      failed: false,
      seconds: 240,
      model_calls: 2,
      total_tokens: 13800,
      cost: 0.0074,
      tool_calls: 3,
      searches: 1,
      pages_read: 1,
      evidence: 3,
      citations: 3,
      tokens_per_citation: 4600,
    },
  ],
  budget: {
    max_model_tokens: 68000,
    model_tokens_used: 0.25,
    max_tool_calls: null,
    tool_calls_used: null,
    max_elapsed_seconds: null,
    elapsed_used: null,
  },
  report: {
    versions: 1,
    characters: 120,
    sections: 2,
    tables: 1,
    diagrams: 1,
    citations: 4,
    cited_domains: 3,
    draft_repairs: 1,
    dropped_statements: 2,
    validation_retries: 0,
  },
  cache: { hits: 2, by_kind: { "native-unit": 1, "report-section": 1 } },
  efficiency: {
    tokens_per_citation: 4250,
    cost_per_citation: 0.0022,
    active_seconds_per_citation: 202.5,
    pages_read_per_citation: 0.25,
    citations_per_page_read: 4,
    searches_per_unit: 0.3,
    repeat_tool_call_ratio: 0.25,
    tokens_per_report_char: 141.7,
    conversion_token_share: 0.194,
    failed_agent_token_share: 0,
    cache_read_ratio: 0.552,
  },
  breakdown: {
    by_phase: [
      group("planner", 1200, 0.0014),
      group("dispatch", 13800, 0.0074),
    ],
    by_purpose: [],
    by_skill: [],
    by_model: [group("flash", 15000, 0.0088), group("other", 2000, null)],
    by_unit: [],
    by_cycle: [],
  },
};

it("shows research cost, time, calls and subagents with an export", async () => {
  const downloadMetrics = rs.fn(async () => undefined);
  const api = {
    root: "/api/deepresearch",
    metrics: async () => metrics,
    downloadMetrics,
  };
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchMetricsPanel api={api} runId="r" active={false} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("13m 30s")).toBeTruthy();
  expect(screen.getByText("17.0k")).toBeTruthy();
  expect(screen.getByText("预估费用").parentElement?.textContent).toContain(
    "$0.0088",
  );
  expect(screen.getByText("研究")).toBeTruthy();
  expect(screen.getByText("technical-route")).toBeTruthy();
  expect(screen.getByText("未定价：other")).toBeTruthy();
  // Failure causes, repeated work and failing sites locate tool waste.
  expect(screen.getByText("HTTP 429 1")).toBeTruthy();
  expect(screen.getByText("b.com")).toBeTruthy();
  expect(screen.getByText("HTTP 403 1")).toBeTruthy();
  expect(screen.getByText("1 次（25%）· 同一 Agent 0")).toBeTruthy();
  expect(screen.getByText(/并行累计：模型 1m 14s/)).toBeTruthy();
  // A phase without model calls shows its time only.
  expect(screen.getByText("<1s")).toBeTruthy();
  // Each research unit's cost against what it contributed, and budget use.
  expect(screen.getByText("技术路线")).toBeTruthy();
  expect(screen.getByText("4.6k")).toBeTruthy();
  expect(screen.getByText("Token 25%")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "导出指标" }));
  expect(downloadMetrics).toHaveBeenCalledWith("r");
  view.unmount();
});

it("marks runs from before per-call metering as unmeasured instead of zero", async () => {
  const legacy: ResearchMetrics = {
    ...metrics,
    metered: false,
    tokens: {
      ...metrics.tokens,
      input: null,
      output: null,
      cache_read: null,
      cache_read_ratio: null,
    },
    time: { ...metrics.time, model_seconds: null, queue_seconds: null },
    model_calls: { ...metrics.model_calls, count: null, errors: null },
    agents: { ...metrics.agents, count: null, failed: null, runs: [] },
  };
  const api = {
    root: "/api/deepresearch",
    metrics: async () => legacy,
    downloadMetrics: rs.fn(async () => undefined),
  };
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchMetricsPanel api={api} runId="old" active={false} />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByText("早于逐次计量，仅有预算账本合计"),
  ).toBeTruthy();
  expect(screen.getByText("模型调用").parentElement?.textContent).toContain(
    "—",
  );
  expect(screen.queryByText(/null/)).toBeNull();
  expect(screen.getByText(/并行累计：模型 — · 工具/)).toBeTruthy();
  view.unmount();
});

const node = (
  name: string,
  overrides: Partial<NodeMetric> = {},
): NodeMetric => ({
  node: name,
  models: ["gateway-flash"],
  model_calls: 2,
  model_errors: 0,
  unreported_usage: 0,
  input_tokens: 2884,
  output_tokens: 1904,
  cache_read_tokens: 512,
  reasoning_tokens: 0,
  total_tokens: 4788,
  max_input_tokens: 2884,
  avg_output_tokens: 952,
  truncated: 0,
  retries: 0,
  model_ms: 8757,
  latency_ms: { count: 2, avg: 4378, p50: 2801, p95: 8757, max: 8757 },
  cost: 0.0063,
  ...overrides,
});

function mount(value: ResearchMetrics) {
  const api = {
    root: "/api/deepresearch",
    metrics: async () => value,
    downloadMetrics: rs.fn(async () => undefined),
  };
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ResearchMetricsPanel api={api} runId={value.run_id} active={false} />
    </QueryClientProvider>,
  );
}

it("breaks model work down by node and flags truncation and format retries", async () => {
  const view = mount({
    ...metrics,
    run_id: "nodes",
    breakdown: {
      ...metrics.breakdown,
      by_node: [
        // An agent loop whose requests only append: most of its prompt could
        // be served from a prefix cache, and the provider served part of it.
        node("plan", { cache_read_ratio: 0.178, prefix_reuse_ratio: 0.82 }),
        node("section", {
          models: ["gateway-pro", "gateway-flash"],
          truncated: 3,
          retries: 2,
          model_errors: 1,
          unreported_usage: 1,
        }),
        // Nothing measured yet: unknown stays unknown, never zero.
        node("compaction", {
          models: [],
          max_input_tokens: null,
          avg_output_tokens: null,
          cost: null,
          latency_ms: { count: 0, avg: null, p50: null, p95: null, max: null },
        }),
        node("brand_new_node"),
      ],
    },
  });
  const heading = await screen.findByRole("heading", { name: "按节点" });
  const section = within(heading.parentElement!);
  expect(
    section.getAllByRole("columnheader").map((cell) => cell.textContent),
  ).toEqual([
    "节点",
    "模型",
    "调用",
    "错误",
    "Token",
    "缓存命中 / 可复用",
    "平均输出",
    "被截断",
    "格式重试",
    "P50 / P95 延迟",
    "耗时",
    "费用",
  ]);
  const cells = (label: string) =>
    [...section.getByText(label).closest("tr")!.querySelectorAll("td")].map(
      (cell) => cell.textContent,
    );
  expect(cells("研究计划")).toEqual([
    "研究计划",
    "gateway-flash",
    "2",
    "0",
    "4.8k",
    "18% / 82%",
    "952",
    "0",
    "0",
    "2.8s / 8.8s",
    "8.8s",
    "$0.0063",
  ]);
  expect(cells("章节写作").slice(1, 5)).toEqual([
    "gateway-pro、gateway-flash",
    "2",
    "1",
    "4.8k1 次未上报用量",
  ]);
  expect(cells("上下文压缩")).toEqual([
    "上下文压缩",
    "—",
    "2",
    "0",
    "4.8k",
    // Records from before the measure existed: unknown, not 0%.
    "— / —",
    "—",
    "0",
    "0",
    "— / —",
    "8.8s",
    "—",
  ]);
  // An unknown node keeps its raw name instead of disappearing.
  expect(section.getByText("brand_new_node")).toBeTruthy();

  // Only the cells that call for tuning stand out.
  const flagged = (label: string) =>
    [
      ...section
        .getByText(label)
        .closest("tr")!
        .querySelectorAll("[data-flagged]"),
    ].map((cell) => cell.textContent);
  expect(flagged("章节写作")).toEqual(["1", "3", "2"]);
  expect(flagged("研究计划")).toEqual([]);
  expect(
    section.getByText(
      "被截断：输出碰到上限，调大该节点 max_tokens；格式重试：JSON/引用未通过校验，降低该节点温度或换模型",
    ),
  ).toBeTruthy();
  view.unmount();
});

it("omits the node table for a gateway without per-node metrics", async () => {
  const view = mount({ ...metrics, run_id: "no-nodes" });
  expect(await screen.findByText("13m 30s")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "按节点" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "此前任务用量" })).toBeNull();
  view.unmount();
});

it("lists the tasks that ended earlier in the conversation", async () => {
  const view = mount({
    ...metrics,
    run_id: "follow-up",
    budget: {
      ...metrics.budget,
      earlier_tasks: [
        {
          cycle: 0,
          closed_at: "2026-09-17T00:20:00+00:00",
          model_tokens: 348000,
          tool_calls: 40,
          elapsed_seconds: 615,
        },
        {
          cycle: null,
          closed_at: null,
          model_tokens: null,
          tool_calls: null,
          elapsed_seconds: null,
        },
      ],
    },
  });
  const heading = await screen.findByRole("heading", { name: "此前任务用量" });
  const rows = [...heading.parentElement!.querySelectorAll("tbody tr")].map(
    (row) => [...row.querySelectorAll("td")].map((cell) => cell.textContent),
  );
  expect(rows[0]![0]).toBe("第 1 个");
  expect(rows[0]!.slice(2)).toEqual(["348.0k", "40", "10m 15s"]);
  expect(rows[1]).toEqual(["—", "—", "—", "—", "—"]);
  expect(screen.getByText(/预算按任务计/)).toBeTruthy();
  view.unmount();
});

it("shows an unmetered subagent as unknown, not as zero calls", async () => {
  const view = mount({
    ...metrics,
    run_id: "unmetered-agent",
    agents: {
      ...metrics.agents,
      runs: [
        {
          ...metrics.agents.runs[0]!,
          model_calls: null,
          tool_calls: null,
          total_tokens: null,
          seconds: null,
          cost: null,
        },
      ],
    },
  });
  const heading = await screen.findByRole("heading", { name: "子 Agent" });
  const cells = [...heading.parentElement!.querySelectorAll("tbody tr td")].map(
    (cell) => cell.textContent,
  );
  expect(cells.slice(2)).toEqual(["—", "— / —", "—", "—"]);
  view.unmount();
});
