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

/** The server answered and refused (4xx). Transport failures and gateway 5xx
 * leave the outcome unknown: the request may still have been accepted. */
export function isRejection(cause: unknown): boolean {
  const status = (cause as { status?: unknown } | null)?.status;
  return typeof status === "number" && status >= 400 && status < 500;
}

/** Backoff before rebuilding a stream the browser gave up on: 1s, 2s, 4s …
 * capped at 15s. `failures` counts consecutive failures before this one. */
export function streamRetryDelay(failures: number) {
  return Math.min(15_000, 1000 * 2 ** Math.max(0, Math.min(failures, 10)));
}

const RESEARCH_PATH = "/workspace/deepresearch";

/** The run a workspace address selects: an id, `undefined` for a new
 * research, `null` for a path that is not a research conversation. */
export function runIdFromPath(pathname: string): string | undefined | null {
  const path = pathname.replace(/\/+$/, "");
  if (path === RESEARCH_PATH) return undefined;
  if (!path.startsWith(`${RESEARCH_PATH}/`)) return null;
  const segment = path.slice(RESEARCH_PATH.length + 1);
  if (!segment || segment.includes("/") || segment === "settings") return null;
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}
