import type { ResearchEvent, Run } from "./types";

/** Malformed transport frames trigger snapshot reconciliation, not a UI crash. */
export function parseResearchEvent(text: string): ResearchEvent | undefined {
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object") return;
    const event = value as Partial<ResearchEvent>;
    if (
      typeof event.run_id !== "string" ||
      !Number.isSafeInteger(event.seq) ||
      (event.seq ?? 0) < 1 ||
      typeof event.type !== "string" ||
      typeof event.at !== "string" ||
      !event.data ||
      typeof event.data !== "object" ||
      Array.isArray(event.data)
    )
      return;
    return event as ResearchEvent;
  } catch {
    return;
  }
}

/** A delayed GET/command acknowledgement must not replace a newer snapshot. */
export function latestSnapshot(previous: Run | undefined, incoming: Run): Run {
  return previous?.run_id === incoming.run_id &&
    previous.updated_at > incoming.updated_at
    ? previous
    : incoming;
}
