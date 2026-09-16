import type { ResearchEvent } from "./types";

export type TraceSpan = {
  id: string;
  parent?: string;
  name: string;
  kind: string;
  start: number;
  duration?: number;
  status: string;
  input?: unknown;
  output?: unknown;
};

export function traceSpans(
  events: ResearchEvent[],
  active: boolean,
): TraceSpan[] {
  const spans = new Map<string, TraceSpan>();
  for (const event of events) {
    if (
      !event.type.startsWith("trace.") ||
      typeof event.data.span_id !== "string"
    )
      continue;
    const data = event.data;
    const id = data.span_id as string;
    const duration =
      typeof data.duration_ms === "number" ? data.duration_ms : undefined;
    const span: TraceSpan = spans.get(id) ?? {
      id,
      parent:
        typeof data.parent_span_id === "string"
          ? data.parent_span_id
          : undefined,
      name: typeof data.name === "string" ? data.name : "event",
      kind: typeof data.kind === "string" ? data.kind : "node",
      start:
        Date.parse(event.at) -
        (event.type === "trace.ended" ? (duration ?? 0) : 0),
      status: active ? "running" : "interrupted",
    };
    if (event.type === "trace.started") span.input = data.payload;
    else {
      span.duration = duration;
      span.status = typeof data.status === "string" ? data.status : "unknown";
      span.output = data.payload;
    }
    spans.set(id, span);
  }
  return [...spans.values()].sort((a, b) => a.start - b.start);
}

export function spanDepth(
  span: TraceSpan,
  spans: Map<string, TraceSpan>,
): number {
  const seen = new Set([span.id]);
  let parent = span.parent,
    depth = 0;
  while (parent && !seen.has(parent) && spans.has(parent)) {
    seen.add(parent);
    depth++;
    parent = spans.get(parent)?.parent;
  }
  return Math.min(depth, 8);
}
