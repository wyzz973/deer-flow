/** Resolve only report-owned citations, including the native sanitizer prefix.
 * External links and unregistered IDs must never become trusted citations. */
export function citationId(
  href: string | undefined,
  citationMap: Record<string, number>,
): string | undefined {
  const id = href?.match(/^#(?:user-content-)?citation-([A-Za-z0-9_-]+)$/)?.[1];
  if (!id || !Object.hasOwn(citationMap, id)) return undefined;
  const number = citationMap[id];
  return Number.isInteger(number) && number! > 0 ? id : undefined;
}

/** Report fields are text; angle-bracket placeholders must not become HTML. */
export function escapeReportText(text: string) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Several recorded excerpts from one page share one visible citation number. */
export function uniqueCitationIds(
  ids: string[],
  citationMap: Record<string, number>,
) {
  const numbers = new Set<number>();
  return ids.filter((id) => {
    const number = citationMap[id];
    if (number === undefined || numbers.has(number)) return false;
    numbers.add(number);
    return true;
  });
}

/** Display a server-owned deadline using elapsed monotonic time, not the
 * browser wall clock. A render/reload does not create another start deadline. */
export function countdownSeconds(
  deadline: string | null | undefined,
  serverTime: string,
  elapsedMs: number,
): number | null {
  if (!deadline) return null;
  const remaining = Date.parse(deadline) - Date.parse(serverTime);
  if (!Number.isFinite(remaining) || !Number.isFinite(elapsedMs)) return null;
  return Math.max(0, Math.ceil((remaining - Math.max(0, elapsedMs)) / 1000));
}
