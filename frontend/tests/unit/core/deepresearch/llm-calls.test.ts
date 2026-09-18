import { describe, expect, it } from "@rstest/core";

import {
  callNodeLabel,
  callStage,
  callTask,
  formatDuration,
  formatTokens,
  groupCalls,
  jsonPayload,
  matchingMessages,
  messageText,
  tokens,
  toolCallSummary,
} from "@/core/deepresearch/llm-calls";
import type {
  AuditMessage,
  LlmCallSummary,
  Run,
} from "@/core/deepresearch/types";

import { makeRun } from "./fixtures";

function call(body: Partial<LlmCallSummary> = {}): LlmCallSummary {
  return {
    id: body.id ?? "c1",
    status: "ok",
    started_at: "2026-09-18T00:00:00Z",
    audited: true,
    ...body,
  } as LlmCallSummary;
}

describe("callStage", () => {
  it("names the part of the research a call served", () => {
    expect(callStage(call({ purpose: "rewrite" }))).toBe("rewrite");
    expect(callStage(call({ phase: "planner" }))).toBe("plan");
    expect(
      callStage(call({ phase: "dispatch", skill: "technical-route" })),
    ).toBe("research");
    expect(callStage(call({ purpose: "conversion", phase: "dispatch" }))).toBe(
      "conversion",
    );
    expect(callStage(call({ phase: "synthesis" }))).toBe("writing");
    expect(callStage(call({ skill: "report-synthesis" }))).toBe("writing");
    expect(callStage(call({}))).toBe("other");
  });
});

describe("callTask", () => {
  it("prefers the plan's own unit title, then the writing part", () => {
    const run = makeRun();
    const unit = run.units[0]!;
    expect(callTask(call({ unit_id: unit.id }), run)).toBe(
      unit.title ?? unit.objective.slice(0, 40),
    );
    expect(callTask(call({ unit_id: "report-section-3" }))).toBe("第 3 节正文");
    expect(callTask(call({ unit_id: "report-outline" }))).toBe("报告大纲");
    expect(callTask(call({ contract: "ResearchPlan" }))).toBe("ResearchPlan");
  });
});

describe("groupCalls", () => {
  const run = {
    ...makeRun(),
    units: [
      {
        id: "u1",
        title: "查显存占用",
        objective: "o",
        skill: "s",
        origins: [],
      },
    ],
  } as unknown as Run;
  const calls = [
    call({
      id: "r1",
      purpose: "rewrite",
      phase: "rewrite",
      group: "g0",
      input_tokens: 500,
      output_tokens: 100,
      duration_ms: 3700,
    }),
    call({
      id: "p1",
      phase: "planner",
      group: "g1",
      input_tokens: 1000,
      output_tokens: 300,
      duration_ms: 12900,
    }),
    call({
      id: "a1",
      phase: "dispatch",
      unit_id: "u1",
      group: "g2",
      input_tokens: 3000,
      output_tokens: 200,
    }),
    call({
      id: "s1",
      phase: "dispatch",
      unit_id: "u1",
      group: "g2",
      node: "DeerFlowSummarizationMiddleware.before_model",
      input_tokens: 9000,
      output_tokens: 900,
    }),
    call({
      id: "a2",
      phase: "dispatch",
      unit_id: "u1",
      group: "g2",
      input_tokens: 4000,
      output_tokens: 250,
      status: "error",
    }),
  ];

  it("keeps request order, numbers agent turns and skips node calls", () => {
    const groups = groupCalls(calls, run);
    expect(groups.map((group) => group.stage)).toEqual([
      "rewrite",
      "plan",
      "research",
    ]);
    expect(groups[2]!.label).toContain("查显存占用");
    // The compaction call shares the loop but is not one of the agent's turns.
    expect(groups[2]!.calls.map((item) => [item.id, item.turn])).toEqual([
      ["a1", 1],
      ["s1", 1],
      ["a2", 2],
    ]);
    expect(groups[2]!.calls.map((item) => item.index)).toEqual([3, 4, 5]);
    expect(groups[2]!.inputTokens).toBe(16000);
    expect(groups[2]!.errors).toBe(1);
  });

  it("separates supplement cycles from the first round", () => {
    const groups = groupCalls(
      [
        ...calls,
        call({
          id: "a3",
          phase: "dispatch",
          unit_id: "u1",
          cycle: 1,
          group: "g3",
        }),
      ],
      run,
    );
    expect(groups).toHaveLength(4);
    expect(groups[3]!.label).toContain("第 2 轮");
  });
});

describe("call details", () => {
  it("labels engine calls that are not the agent's own turn", () => {
    expect(
      callNodeLabel(
        call({ node: "DeerFlowSummarizationMiddleware.before_model" }),
      ),
    ).toBe("上下文压缩");
    expect(callNodeLabel(call({ node: "model" }))).toBeUndefined();
    expect(callNodeLabel(call({}))).toBeUndefined();
    expect(callNodeLabel(call({ node: "rewrite" }))).toBe("rewrite");
  });

  it("counts repeated tool calls and reads usage when the column is missing", () => {
    expect(
      toolCallSummary(
        call({ tool_calls: ["web_search", "web_search", "web_fetch"] }),
      ),
    ).toEqual(["web_search ×2", "web_fetch"]);
    expect(tokens(call({ usage: { input_tokens: 42 } }), "input")).toBe(42);
    expect(
      tokens(call({ input_tokens: 7, usage: { input_tokens: 42 } }), "input"),
    ).toBe(7);
    expect(tokens(call({}), "output")).toBe(0);
  });

  it("formats tokens and durations for the panel", () => {
    expect(formatTokens(undefined)).toBe("—");
    expect(formatTokens(900)).toBe("900");
    expect(formatTokens(12_400)).toBe("12.4k");
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(870)).toBe("870ms");
    expect(formatDuration(3700)).toBe("3.7s");
    expect(formatDuration(125_000)).toBe("2m5s");
  });
});

describe("message payloads", () => {
  it("unwraps the engine's user-input boundary around a JSON task", () => {
    const payload = jsonPayload(
      '--- BEGIN USER INPUT ---\n{"unit": {"id": "u1"}}\n--- END USER INPUT ---',
    );
    expect(payload).toEqual({ unit: { id: "u1" } });
    expect(jsonPayload("plain progress note")).toBeNull();
    expect(jsonPayload("{not json")).toBeNull();
  });

  it("renders non-text blocks as a placeholder instead of dropping them", () => {
    expect(messageText("hello")).toBe("hello");
    expect(
      messageText([
        { type: "text", text: "开始检索" },
        { type: "image", bytes: 2048 },
      ]),
    ).toBe("开始检索\n[image 2048 bytes]");
  });

  it("finds matches in content, reasoning and tool calls", () => {
    const messages: (AuditMessage | null)[] = [
      { role: "system", content: "你是研究员" },
      {
        role: "assistant",
        content: "",
        reasoning: "先查 vLLM 官方文档",
        tool_calls: [{ id: "t", name: "web_search", args: { query: "vLLM" } }],
      },
      null,
      { role: "tool", content: "结果" },
    ];
    expect([...matchingMessages(messages, "vllm")]).toEqual([1]);
    expect([...matchingMessages(messages, "研究员")]).toEqual([0]);
    expect(matchingMessages(messages, "  ").size).toBe(0);
  });
});
