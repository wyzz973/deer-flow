import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

import { LlmCallDialog } from "@/components/deepresearch/llm-call-dialog";
import { ResearchLlmCalls } from "@/components/deepresearch/llm-calls-panel";
import { ResearchRequestCard } from "@/components/deepresearch/request-card";
import type {
  LlmCallDetail,
  LlmCallSummary,
  ResearchMessage,
} from "@/core/deepresearch/types";

import { makeRun } from "../../core/deepresearch/fixtures";

const summaries: LlmCallSummary[] = [
  {
    id: "call-rewrite",
    purpose: "rewrite",
    phase: "rewrite",
    group: "g0",
    status: "ok",
    model: "flash",
    started_at: "2026-09-18T00:00:00Z",
    duration_ms: 3700,
    input_tokens: 800,
    output_tokens: 320,
    audited: true,
    preview: "把对话改写成研究请求",
  },
  {
    id: "call-turn",
    phase: "dispatch",
    skill: "technical-route",
    unit_id: "U1",
    group: "g2",
    status: "ok",
    model: "flash",
    started_at: "2026-09-18T00:01:00Z",
    duration_ms: 5200,
    input_tokens: 3352,
    output_tokens: 162,
    tool_calls: ["web_search", "web_search"],
    audited: true,
    preview: "我先并行检索官方文档",
  },
  {
    id: "call-compaction",
    phase: "dispatch",
    skill: "technical-route",
    unit_id: "U1",
    group: "g2",
    node: "DeerFlowSummarizationMiddleware.before_model",
    status: "ok",
    model: "flash",
    started_at: "2026-09-18T00:02:00Z",
    duration_ms: 9100,
    input_tokens: 15000,
    output_tokens: 900,
    audited: true,
    preview: "## Findings so far",
  },
] as LlmCallSummary[];

const detail: LlmCallDetail = {
  ...summaries[1]!,
  tools: [
    { name: "web_search", description: "搜索", parameters: { type: "object" } },
  ],
  messages: [
    { role: "system", content: "你是技术路线研究员。" },
    {
      role: "user",
      content:
        '--- BEGIN USER INPUT ---\n{"unit": {"id": "U1", "objective": "对比推理框架"}}\n--- END USER INPUT ---',
    },
    {
      role: "assistant",
      content: "我先并行检索官方文档",
      tool_calls: [
        { id: "t1", name: "web_search", args: { query: "vLLM 官方文档" } },
      ],
    },
    { role: "tool", content: 'Search results for "vLLM"', name: "web_search" },
  ],
  message_hashes: ["h0", "h1", "h2", "h3"],
  response: {
    generations: [
      {
        role: "assistant",
        content: "接下来打开 vLLM 官方文档",
        tool_calls: [
          {
            id: "t2",
            name: "web_fetch",
            args: { url: "https://docs.vllm.ai" },
          },
        ],
      },
    ],
  },
  previous_call_id: "call-prev",
  repeated_prefix: 2,
  new_message_indexes: [2, 3],
  openai_request: { model: "flash", messages: [] },
} as unknown as LlmCallDetail;

const api = {
  root: "/api/deepresearch",
  llmCalls: () => Promise.resolve({ items: summaries }),
  llmCall: () => Promise.resolve(detail),
  downloadLlmCalls: rs.fn(() => Promise.resolve()),
} as unknown as Parameters<typeof ResearchLlmCalls>[0]["api"];

function wrap(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

afterEach(cleanup);

describe("ResearchLlmCalls", () => {
  it("groups calls by stage and marks compaction as the node's own work", async () => {
    const opened: string[] = [];
    wrap(
      <ResearchLlmCalls
        api={api}
        runId="run"
        run={makeRun()}
        active={false}
        onOpen={(id) => opened.push(id)}
      />,
    );
    expect(await screen.findByText("请求改写")).toBeTruthy();
    expect(screen.getByText(/研究 ·/)).toBeTruthy();
    // The agent turn is numbered; the compaction call is labeled, not counted.
    expect(screen.getAllByText("第 1 轮")).toHaveLength(2); // rewrite call + first research turn
    expect(screen.queryByText("第 2 轮")).toBeNull();
    expect(screen.getByText("上下文压缩")).toBeTruthy();
    expect(screen.getByText("web_search ×2")).toBeTruthy();
    fireEvent.click(screen.getByText("我先并行检索官方文档"));
    expect(opened).toEqual(["call-turn"]);
  });

  it("filters by stage and by search text", async () => {
    wrap(
      <ResearchLlmCalls
        api={api}
        runId="run"
        active={false}
        onOpen={() => undefined}
      />,
    );
    await screen.findByText("请求改写");
    fireEvent.change(screen.getByLabelText("搜索模型调用"), {
      target: { value: "改写成研究请求" },
    });
    await waitFor(() => expect(screen.queryByText(/研究 ·/)).toBeNull());
    fireEvent.change(screen.getByLabelText("搜索模型调用"), {
      target: { value: "" },
    });
    expect(await screen.findByText(/研究 ·/)).toBeTruthy();
  });
});

describe("LlmCallDialog", () => {
  it("shows the full prompt, the response and only this turn's new messages", async () => {
    wrap(
      <LlmCallDialog
        api={api}
        runId="run"
        run={makeRun()}
        calls={summaries}
        callId="call-turn"
        onSelect={() => undefined}
        onClose={() => undefined}
      />,
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText("你是技术路线研究员。"),
    ).toBeTruthy();
    // The task payload is rendered field by field, not as a raw boundary block.
    expect(within(dialog).queryByText(/BEGIN USER INPUT/)).toBeNull();
    expect(within(dialog).getAllByText(/unit/).length).toBeGreaterThan(0);
    fireEvent.click(
      within(dialog).getByRole("button", { name: /只看本轮新增/ }),
    );
    await waitFor(() =>
      expect(within(dialog).queryByText("你是技术路线研究员。")).toBeNull(),
    );
    // The other views of the same call are offered with their real counts.
    expect(
      within(dialog).getByRole("tab", { name: "请求 · 4 条消息" }),
    ).toBeTruthy();
    expect(
      within(dialog).getByRole("tab", { name: "工具定义 · 1" }),
    ).toBeTruthy();
    expect(within(dialog).getByRole("tab", { name: "原始 JSON" })).toBeTruthy();
  });

  it("says a legacy run was not audited instead of showing an empty prompt", async () => {
    const legacy = [{ ...summaries[1]!, audited: false }];
    wrap(
      <LlmCallDialog
        api={api}
        runId="run"
        calls={legacy}
        callId={legacy[0]!.id}
        onSelect={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(await screen.findByText(/完整审计记录启用之前/)).toBeTruthy();
  });
});

describe("ResearchRequestCard", () => {
  const message: ResearchMessage = {
    id: "request-1",
    role: "assistant",
    kind: "rewrite",
    text: "我们四个人的小团队要自建推理服务……",
    rewrite: {
      user_query:
        "我们四个人的小团队要自建推理服务……请比较 vLLM、SGLang、TensorRT-LLM。",
      clarification_questions: [],
      revision: false,
    },
    at: "2026-09-18T00:00:00Z",
  } as ResearchMessage;

  it("stays collapsed until asked, then shows the rewritten request", () => {
    render(<ResearchRequestCard message={message} />);
    expect(screen.queryByText(/请比较 vLLM/)).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "查看改写后的研究请求" }),
    );
    expect(screen.getByText(/请比较 vLLM/)).toBeTruthy();
  });

  it("labels a revision differently from the first request", () => {
    render(
      <ResearchRequestCard
        message={{
          ...message,
          rewrite: { ...message.rewrite!, revision: true },
        }}
      />,
    );
    expect(screen.getByText("已更新研究请求")).toBeTruthy();
  });
});
