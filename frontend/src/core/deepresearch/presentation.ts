import {
  reviewStatuses,
  steerableStatuses,
  type Report,
  type ResearchMessage,
  type Run,
  type Segment,
} from "./types";

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

/** Compact token counts: 950, 17.0k, 1.23M. */
export function formatTokens(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 1000) return String(Math.round(value));
  if (value < 1_000_000) return `${(value / 1000).toFixed(1)}k`;
  return `${(value / 1_000_000).toFixed(2)}M`;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  CNY: "¥",
  RMB: "¥",
  EUR: "€",
};

/** Estimated cost; small amounts keep four decimals so they stay visible. */
export function formatCost(
  amount: number | null | undefined,
  currency?: string | null,
) {
  if (amount == null || !Number.isFinite(amount)) return "—";
  const symbol = currency ? (CURRENCY_SYMBOLS[currency] ?? `${currency} `) : "";
  const digits = amount !== 0 && Math.abs(amount) < 0.01 ? 4 : 2;
  return `${symbol}${amount.toFixed(digits)}`;
}

export function formatPercent(ratio: number | null | undefined) {
  if (ratio == null || !Number.isFinite(ratio)) return "—";
  return `${Math.round(ratio * 100)}%`;
}

const PHASE_LABELS: Record<string, string> = {
  rewrite: "请求改写",
  planner: "规划",
  plan_review: "计划确认",
  dispatch: "研究",
  evidence_merge: "合并证据",
  validator: "缺口检查",
  supplement: "补研",
  synthesis: "写报告",
  citation_binder: "绑定引用",
  final_validator: "终检",
  renderer: "发布",
  follow_up: "追问",
  rejected: "拒绝",
  unknown: "其他",
};

export function phaseLabel(phase: string) {
  return PHASE_LABELS[phase] ?? phase;
}

const NODE_LABELS: Record<string, string> = {
  rewrite: "请求改写",
  plan: "研究计划",
  research: "检索研究",
  conversion: "笔记整理",
  outline: "报告大纲",
  section: "章节写作",
  summary: "执行摘要",
  revision: "报告改写",
  follow_up: "追问分流",
  compaction: "上下文压缩",
  unknown: "未知",
};

/** Graph node of a metered model call (`breakdown.by_node`). */
export function nodeLabel(node: string) {
  return NODE_LABELS[node] ?? node;
}

/** Exact duration for the finished timeline, like "7m 55s". */
export function formatDuration(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  const value = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(value / 60);
  return minutes ? `${minutes}m ${value % 60}s` : `${value}s`;
}

/** The section being read: the last heading at or above the reading line.
 * `tops` are the headings' viewport offsets in document order. Before the
 * first heading (the title block), the first section is the current one. */
export function activeHeadingIndex(tops: number[], line: number) {
  let active = 0;
  tops.forEach((top, index) => {
    if (top <= line) active = index;
  });
  return active;
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

/** A plan whose report exists folds to one line: the stats line and the report
 * card lead, and the plan is a click away when someone wants to see what was
 * researched and how each step ended. It used to disappear, which left no way
 * back to it in the conversation. Not folded while it still has news: a
 * follow-up that failed or was stopped keeps its error, retry or notice. */
export function planCardFolded(message: ResearchMessage, run: Run) {
  const latest = message.plan?.plan_version === run.plan?.plan_version;
  if (latest && (run.status === "FAILED" || run.status === "CANCELLED"))
    return false;
  return (
    run.status === "COMPLETED" ||
    Boolean(
      run.conversation?.some(
        (item) =>
          item.kind === "report" && (item.cycle ?? 0) === (message.cycle ?? 0),
      ),
    )
  );
}

/** What the conversation shows between a sent message and the next card.
 * `thinking`: shimmer text only. `planning`: the plan is being written and no
 * card for it is on screen yet, so a skeleton holds its place. A plan that is
 * being revised keeps its own card (with a live status) and needs neither. */
export function waitingPhase(
  status: string,
  conversation: ResearchMessage[],
  planVersion?: number | null,
): "thinking" | "planning" | null {
  if (status === "RESPONDING") return "thinking";
  if (status !== "CREATED" && status !== "PLANNING") return null;
  const planShown = conversation.some(
    (item) =>
      item.kind === "plan" &&
      planVersion != null &&
      item.plan?.plan_version === planVersion &&
      !conversation.some(
        (other) =>
          other.kind === "report" && (other.cycle ?? 0) === (item.cycle ?? 0),
      ),
  );
  if (planShown) return null;
  return status === "PLANNING" ? "planning" : "thinking";
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

/** Whether the conversation takes a message now, mirroring the server: a new
 * research, a plan under review, a finished report (follow-up), or an update
 * the owner opened on running research. A failed or stopped run takes none. */
export function composerAccepts({
  welcome,
  status,
  updating,
  hasReport = false,
}: {
  welcome: boolean;
  status: string;
  updating: boolean;
  /** A report was already published in this conversation. */
  hasReport?: boolean;
}) {
  if (welcome) return true;
  if (reviewStatuses.has(status) || status === "COMPLETED") return true;
  // A follow-up that was stopped or failed leaves the report in place, and the
  // backend accepts further messages about it. Without a report it answers
  // RUN_STOPPED: that run is retried or replaced by a new research.
  if ((status === "CANCELLED" || status === "FAILED") && hasReport) return true;
  return updating && steerableStatuses.has(status);
}

/** Retry body: only an explicit limited-report choice is ever sent. */
export function retryRequest(allowLimitedReport: boolean) {
  return allowLimitedReport ? { allow_limited_report: true } : {};
}

/** Plain text for a citation hover card. Fetched pages arrive as Markdown
 * (tables, links, emphasis); the preview keeps the words, not the syntax. */
export function excerptPreview(text: string | null | undefined, limit = 360) {
  const plain = readableExcerpt(text)
    .replace(/^\s*(?:`{3,}|~{3,})[^\n]*$/gm, "")
    .replace(/^\s*(?:>\s*)+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
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

/** Page identity, matching the server's: scheme, "www." and a trailing slash
 * do not change the page; a query or a single-page-app fragment route does. */
export function pageKey(url: string | null | undefined) {
  if (!url) return "";
  try {
    const parts = new URL(url);
    const host = parts.hostname.toLowerCase().replace(/^www\./, "");
    if (!host) return url;
    const port = parts.port && !["80", "443"].includes(parts.port);
    const fragment = parts.hash.slice(1);
    return [
      host + (port ? `:${parts.port}` : ""),
      parts.pathname.replace(/\/+$/, "") || "/",
      parts.search.slice(1),
      /^[/!]/.test(fragment) ? fragment : "",
    ].join("\u0000");
  } catch {
    return url;
  }
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
