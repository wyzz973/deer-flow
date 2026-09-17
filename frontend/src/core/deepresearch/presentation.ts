import type { Report, Segment } from "./types";

/** First non-blank text. Historical records use empty strings, which `??`
 * would keep, so blank labels fall back explicitly. */
export function firstText(...values: (string | null | undefined)[]) {
  return values.find((value) => value?.trim()) ?? "";
}

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

/** Compact research duration, like "6m" or "45s". */
export function formatElapsed(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const value = Math.max(0, Math.round(seconds));
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m`;
  return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
}

/** Exact duration for the finished timeline, like "7m 55s". */
export function formatDuration(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const value = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(value / 60);
  return minutes ? `${minutes}m ${value % 60}s` : `${value}s`;
}

/** Headings of a Markdown report, outside fenced code, for the reader TOC. */
export function reportHeadings(markdown: string) {
  const headings: { level: number; text: string }[] = [];
  let fence: string | null = null;
  for (const line of markdown.split("\n")) {
    const marker = /^\s{0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (line.trim().startsWith(fence)) fence = null;
      continue;
    }
    if (fence) continue;
    const match = /^(#{2,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (match)
      headings.push({
        level: match[1]!.length,
        text: match[2]!
          .replace(/\[(\d+)\]\(#[^)]*\)/g, "")
          .replace(/[*_`]/g, "")
          .trim(),
      });
  }
  return headings;
}

type LiveState = {
  kind: string;
  text?: string;
  title?: string;
  query?: string;
  domain?: string;
  url?: string;
};

/** One-line live status shown on the progress card. */
export function liveStatus(
  current: LiveState | null | undefined,
  status: string,
) {
  if (!current) {
    if (status === "PLANNING") return "正在调整研究计划…";
    if (status === "RENDERING" || status === "FINAL_VALIDATING")
      return "正在核对引用并排版报告…";
    return "正在研究…";
  }
  switch (current.kind) {
    case "planning":
      return "正在调整研究计划…";
    case "responding":
      return "正在思考你的问题…";
    case "writing":
      return current.title ? `正在撰写：${current.title}` : "正在撰写报告…";
    case "note":
      return current.text ?? "正在研究…";
    case "search":
      return current.query ? `正在搜索：${current.query}` : "正在搜索…";
    case "read":
      return `正在阅读：${firstText(current.domain, current.url, "网页")}`;
    default:
      return "正在研究…";
  }
}

/** Monotonic progress across research, supplementation and writing. */
export function researchProgress(
  status: string,
  counts?: { steps: number; steps_done: number },
) {
  if (status === "COMPLETED") return 100;
  if (["RENDERING", "FINAL_VALIDATING", "CITATION_BINDING"].includes(status))
    return 96;
  if (status === "SYNTHESIZING") return 88;
  if (["VALIDATING", "GAP_FOUND", "RESEARCH_COMPLETE"].includes(status))
    return 80;
  const steps = Math.max(1, counts?.steps ?? 1);
  return Math.min(78, 6 + Math.round((72 * (counts?.steps_done ?? 0)) / steps));
}

/** Historical AST reports are projected into the same Markdown reader. */
function legacyReportMarkdown(report: Report) {
  const render = (segments: Segment[] = []) =>
    segments
      .map(
        (segment) =>
          escapeReportText(segment.text) +
          uniqueCitationIds(segment.evidence_ids, report.citation_map)
            .map((id) => `[${report.citation_map[id]}](#citation-${id})`)
            .join(""),
      )
      .join("\n\n");
  const table = report.report.comparison_table;
  const cell = (value: string) =>
    value.replace(/\|/g, "\\|").replace(/\n/g, " ");
  const comparison = table
    ? `## 快速对比\n\n| 维度 | ${table.headers.map(cell).join(" | ")} |\n| ${table.headers
        .map(() => "---")
        .concat("---")
        .join(" | ")} |\n` +
      table.rows
        .map(
          (row) =>
            `| ${cell(row.label)} | ${row.cells.map((item) => cell(render([item]))).join(" | ")} |`,
        )
        .join("\n") +
      "\n\n"
    : "";
  return (
    `# ${escapeReportText(report.report.title)}\n\n## 执行摘要\n\n${render(report.report.executive_summary)}\n\n` +
    comparison +
    (report.report.sections ?? [])
      .map(
        (section) =>
          `## ${escapeReportText(section.heading)}\n\n${render(section.segments)}`,
      )
      .join("\n\n") +
    `\n\n## 结论\n\n${render(report.report.conclusion)}`
  );
}

export function reportMarkdown(report: Report) {
  // The server bound citations and escaped nothing it did not own; the native
  // Markdown pipeline still sanitizes raw HTML.
  return report.display_markdown ?? legacyReportMarkdown(report);
}

export function reportTitle(report: Report) {
  return report.title ?? report.report.title;
}

/** "研究完成情况：6m · 41 次引用 · 381 个搜索" */
export function reportSummaryLine(report: Report) {
  const stats = report.stats;
  if (!stats) return `研究完成 · ${report.citations.length} 次引用`;
  const parts = [
    formatElapsed(stats.elapsed_seconds),
    `${stats.citations} 次引用`,
    stats.searches ? `${stats.searches} 个搜索` : "",
  ].filter(Boolean);
  return `研究完成情况：${parts.join(" · ")}`;
}

/** Retry body: only an explicit limited-report choice is ever sent. */
export function retryRequest(allowLimitedReport: boolean) {
  return allowLimitedReport ? { allow_limited_report: true } : {};
}

/** Plain text for a citation hover card. Fetched pages arrive as Markdown
 * (tables, links, emphasis); the preview keeps the words, not the syntax. */
export function excerptPreview(text: string | null | undefined, limit = 360) {
  const plain = readableExcerpt(text)
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s*\|?(?:\s*:?-{3,}:?\s*\|?)+\s*$/gm, "")
    .replace(/^\s*#{1,6}\s+/gm, "")
    .replace(/\*\*|__|`/g, "")
    .replace(/\s*\|\s*/g, " · ")
    .replace(/\s+/g, " ")
    .replace(/(?:\s*·\s*){2,}/g, " · ")
    .replace(/^\s*·\s*|\s*·\s*$/g, "")
    .trim();
  return plain.length > limit ? `${plain.slice(0, limit).trimEnd()}…` : plain;
}

/** A short summary under a source title. Pages usually repeat their title at
 * the top, and excerpt windows can start mid-word. */
export function sourceSummary(
  text: string | null | undefined,
  title: string | null | undefined,
  limit = 140,
) {
  const name = (title ?? "").trim();
  let value = excerptPreview(text, 4000);
  if (name && value.toLowerCase().startsWith(name.toLowerCase())) {
    value = value.slice(name.length).replace(/^[\s·:：|—–-]+/, "");
  }
  if (!value || value === name) return "";
  if (/^[a-z]/.test(value)) value = `…${value}`;
  return value.length > limit ? `${value.slice(0, limit).trimEnd()}…` : value;
}

/** A readable source label. Pages read from raw files or through a browser can
 * lack a title ("Untitled", or the URL itself); show the URL without scheme. */
export function sourceTitle(
  title: string | null | undefined,
  url?: string | null,
) {
  const value = (title ?? "").trim();
  const locator = (url ?? "").replace(/\/+$/, "");
  const placeholder = /^(?:untitled|untitled document|no title)$/i.test(value);
  if (value && !placeholder && value.replace(/\/+$/, "") !== locator) {
    return value;
  }
  return firstText(
    locator.replace(/^https?:\/\/(?:www\.)?/, ""),
    placeholder ? "" : value,
    "未命名来源",
  );
}

/** An activity row for an opened page: "title · site", or the bare locator. */
export function readLabel(
  title: string | null | undefined,
  url: string | null | undefined,
  domain: string | null | undefined,
) {
  const label = sourceTitle(title, url);
  const site = firstText(domain);
  if (label !== sourceTitle("", url))
    return site ? `${label} · ${site}` : label;
  return url ? label : firstText(site, label);
}

/** Page text for a citation excerpt. Reports published before server-side
 * cleanup may still carry a host synopsis envelope or the fetch header. */
export function readableExcerpt(text: string | null | undefined) {
  let value = text ?? "";
  if (value.startsWith("[Full ") && value.includes(" output saved to ")) {
    const sample = value.indexOf("Raw sample");
    value = sample >= 0 ? value.slice(value.indexOf("\n", sample) + 1) : "";
  }
  return value
    .replace(
      /^(?:Source|Title|Excerpt|Literal matches|No literal match found)[^\n]*\n/gm,
      "",
    )
    .replace(/\n\[(?:More content|End of extracted page)[^\]]*\]\s*$/, "")
    .trim();
}
