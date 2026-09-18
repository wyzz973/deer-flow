import type {
  AuditContentBlock,
  AuditMessage,
  LlmCallSummary,
  Run,
} from "./types";

/** Summary and detail records share these fields; ``tools`` differs between them. */
export type CallRecord = Omit<LlmCallSummary, "tools">;

export type CallStage =
  | "rewrite"
  | "plan"
  | "research"
  | "conversion"
  | "writing"
  | "follow_up"
  | "other";

export const STAGE_LABELS: Record<CallStage, string> = {
  rewrite: "请求改写",
  plan: "研究计划",
  research: "研究",
  conversion: "笔记整理",
  writing: "报告写作",
  follow_up: "追问",
  other: "其他",
};

/** Which part of the research a model call belongs to. */
export function callStage(call: CallRecord): CallStage {
  if (call.purpose === "rewrite" || call.phase === "rewrite") return "rewrite";
  if (call.purpose === "conversion") return "conversion";
  if (call.phase === "planner") return "plan";
  if (call.phase === "dispatch") return "research";
  if (call.phase === "synthesis") return "writing";
  if (call.phase === "follow_up") return "follow_up";
  if (call.skill === "report-synthesis") return "writing";
  if (call.skill === "deepresearch") return "plan";
  return "other";
}

function unitTitle(run: Run | undefined, unitId: string | undefined) {
  if (!unitId) return undefined;
  const unit = run?.units?.find((item) => item.id === unitId);
  return unit?.title ?? unit?.objective?.slice(0, 40);
}

/** A readable name for the task a call served. */
export function callTask(call: CallRecord, run?: Run): string {
  const unit = call.unit_id;
  if (unit === "report-outline") return "报告大纲";
  if (unit === "report-summary") return "执行摘要";
  if (unit === "report-revision") return "报告改写";
  const section = unit?.match(/^report-section-(\d+)$/);
  if (section) return `第 ${section[1]} 节正文`;
  const title = unitTitle(run, unit);
  if (title) return title;
  if (call.contract) return call.contract;
  return unit ?? call.skill ?? "";
}

export type CallGroup = {
  key: string;
  stage: CallStage;
  label: string;
  calls: (LlmCallSummary & { index: number; turn: number })[];
  inputTokens: number;
  outputTokens: number;
  durationMs: number;
  errors: number;
};

/** Calls in request order, grouped by stage and task, with each agent's turn number. */
export function groupCalls(calls: LlmCallSummary[], run?: Run): CallGroup[] {
  const turns = new Map<string, number>();
  const groups = new Map<string, CallGroup>();
  calls.forEach((call, position) => {
    const stage = callStage(call);
    const task = callTask(call, run);
    const loop = call.group ?? call.execution_id ?? call.id;
    // Context compression shares the agent's loop but is not one of its turns.
    const turn = callNodeLabel(call)
      ? (turns.get(loop) ?? 0)
      : (turns.get(loop) ?? 0) + 1;
    turns.set(loop, turn);
    const key = `${stage}:${stage === "research" || stage === "writing" || stage === "conversion" ? (call.unit_id ?? task) : ""}:${call.cycle ?? 0}`;
    const label =
      stage === "research" || stage === "writing" || stage === "conversion"
        ? `${STAGE_LABELS[stage]} · ${task}`
        : STAGE_LABELS[stage];
    const group = groups.get(key) ?? {
      key,
      stage,
      label:
        (call.cycle ?? 0) > 0
          ? `${label}（第 ${(call.cycle ?? 0) + 1} 轮）`
          : label,
      calls: [],
      inputTokens: 0,
      outputTokens: 0,
      durationMs: 0,
      errors: 0,
    };
    group.calls.push({ ...call, index: position + 1, turn });
    group.inputTokens += tokens(call, "input");
    group.outputTokens += tokens(call, "output");
    group.durationMs += call.duration_ms ?? 0;
    group.errors +=
      call.status === "error" || call.status === "interrupted" ? 1 : 0;
    groups.set(key, group);
  });
  return [...groups.values()];
}

export function tokens(
  call: CallRecord,
  kind: "input" | "output" | "total" | "cache_read" | "reasoning",
) {
  const direct = call[`${kind}_tokens` as const];
  if (typeof direct === "number") return direct;
  const usage = call.usage?.[`${kind}_tokens`];
  return typeof usage === "number" ? usage : 0;
}

export function toolCallNames(call: LlmCallSummary): string[] {
  return Array.isArray(call.tool_calls) ? call.tool_calls : [];
}

/** Tool names with repeat counts, e.g. "web_search ×2 · web_fetch". */
export function toolCallSummary(call: LlmCallSummary): string[] {
  const counts = new Map<string, number>();
  for (const name of toolCallNames(call))
    counts.set(name, (counts.get(name) ?? 0) + 1);
  return [...counts].map(([name, count]) =>
    count > 1 ? `${name} ×${count}` : name,
  );
}

/** Engine calls that are not the agent's own reasoning turn, such as context compression. */
export function callNodeLabel(call: CallRecord): string | undefined {
  const node = call.node ?? "";
  if (!node || node === "model" || node === "agent") return undefined;
  if (/summar/i.test(node)) return "上下文压缩";
  return node;
}

export function messageText(
  content: AuditMessage["content"] | undefined,
): string {
  if (typeof content === "string") return content;
  return (content ?? [])
    .map((block: AuditContentBlock) =>
      typeof block.text === "string"
        ? block.text
        : `[${block.type}${block.bytes ? ` ${block.bytes} bytes` : ""}${block.url ? ` ${block.url}` : ""}]`,
    )
    .join("\n");
}

const INPUT_BOUNDARY =
  /^\s*---\s*BEGIN USER INPUT\s*---\s*([\s\S]*?)\s*(?:---\s*END USER INPUT\s*---\s*)?$/;

/** Parse a task message that is a JSON payload, so it can be shown field by field.
 * The engine may wrap the task in user-input boundary markers first. */
export function jsonPayload(text: string): Record<string, unknown> | null {
  const trimmed = (INPUT_BOUNDARY.exec(text)?.[1] ?? text).trim();
  if (!trimmed.startsWith("{")) return null;
  try {
    const value: unknown = JSON.parse(trimmed);
    return value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export const ROLE_LABELS: Record<string, string> = {
  system: "系统提示",
  user: "任务输入",
  assistant: "模型回合",
  tool: "工具结果",
  developer: "开发者提示",
};

export function formatTokens(value: number | null | undefined) {
  if (value == null) return "—";
  if (value >= 10000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}

export function formatDuration(ms: number | null | undefined) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`;
}

/** Messages whose text contains the query (case-insensitive), by position. */
export function matchingMessages(
  messages: (AuditMessage | null)[],
  query: string,
): Set<number> {
  const needle = query.trim().toLowerCase();
  const found = new Set<number>();
  if (!needle) return found;
  messages.forEach((message, index) => {
    if (!message) return;
    const haystack = [
      messageText(message.content),
      message.reasoning ?? "",
      JSON.stringify(message.tool_calls ?? []),
    ]
      .join("\n")
      .toLowerCase();
    if (haystack.includes(needle)) found.add(index);
  });
  return found;
}
