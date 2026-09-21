import { describe, expect, it } from "@rstest/core";

import {
  activeHeadingIndex,
  citationId,
  composerAccepts,
  countdownSeconds,
  escapeReportText,
  excerptPreview,
  formatCost,
  formatDuration,
  formatElapsed,
  formatPercent,
  formatTokens,
  liveStatus,
  nodeLabel,
  pageKey,
  phaseLabel,
  planCardFolded,
  reportMarkdown,
  readableExcerpt,
  reportSummaryLine,
  researchProgress,
  readLabel,
  retryRequest,
  sourceSummary,
  sourceTitle,
  uniqueCitationIds,
  waitingPhase,
} from "@/core/deepresearch/presentation";
import type { Report, ResearchMessage } from "@/core/deepresearch/types";

import { makeRun } from "./fixtures";

describe("report-owned citation anchors", () => {
  const citations = { E001: 1 };
  it("renders technical angle brackets as text, not markup", () => {
    expect(escapeReportText("<name>_docsize & <script>")).toBe(
      "&lt;name&gt;_docsize &amp; &lt;script&gt;",
    );
  });
  it("accepts both raw and native-sanitized anchors", () => {
    expect(citationId("#citation-E001", citations)).toBe("E001");
    expect(citationId("#user-content-citation-E001", citations)).toBe("E001");
  });
  it("shows one citation for multiple excerpts from a page", () => {
    expect(
      uniqueCitationIds(["E001", "E002", "E003", "E999"], {
        E001: 1,
        E002: 1,
        E003: 2,
      }),
    ).toEqual(["E001", "E003"]);
  });
  it("does not trust external URLs, missing IDs or inherited keys", () => {
    for (const href of [
      undefined,
      "https://example.org/#citation-E001",
      "#citation-E999",
      "#citation-__proto__",
      "#citation-E001/other",
    ]) {
      expect(citationId(href, citations)).toBeUndefined();
    }
    expect(citationId("#citation-E001", { E001: 0 })).toBeUndefined();
  });
});

describe("server-owned countdown display", () => {
  const start = "2026-09-15T00:00:00.000Z";
  const deadline = "2026-09-15T00:00:45.000Z";
  it("starts at 45, even before the first client timer tick", () => {
    expect(countdownSeconds(deadline, start, -250)).toBe(45);
    expect(countdownSeconds(deadline, start, 0)).toBe(45);
  });
  it("decreases with elapsed monotonic time and stays at zero after expiry", () => {
    expect(countdownSeconds(deadline, start, 1001)).toBe(44);
    expect(countdownSeconds(deadline, start, 45000)).toBe(0);
    expect(countdownSeconds(deadline, start, 90000)).toBe(0);
  });
  it("restores the remaining interval from a new server snapshot", () => {
    expect(countdownSeconds(deadline, "2026-09-15T00:00:30.000Z", 0)).toBe(15);
  });
  it("does not invent a deadline for paused or malformed state", () => {
    expect(countdownSeconds(null, start, 0)).toBeNull();
    expect(countdownSeconds("invalid", start, 0)).toBeNull();
    expect(countdownSeconds(deadline, "invalid", 0)).toBeNull();
  });
});

describe("ChatGPT-style research presentation", () => {
  const v2 = {
    version: 1,
    format: "markdown-v2",
    title: "代码仓智能化研究",
    display_markdown:
      "# 代码仓智能化研究\n\n## 执行摘要\n\n结论。[1](#citation-E001)\n\n```mermaid\nflowchart LR\n## not a heading\n```\n\n### 路线图 **P0**\n",
    report: { title: "代码仓智能化研究" },
    markdown: "",
    citations: [],
    citation_map: { E001: 1 },
    limitations: [],
    demo: false,
    stats: {
      elapsed_seconds: 372,
      searches: 381,
      pages_read: 40,
      citations: 41,
    },
  } satisfies Report;

  it("formats research time like the ChatGPT completion line", () => {
    expect(formatElapsed(45)).toBe("45s");
    expect(formatElapsed(372)).toBe("6m");
    expect(formatElapsed(3900)).toBe("1h 5m");
    expect(formatElapsed(null)).toBe("");
    expect(formatDuration(475)).toBe("7m 55s");
    expect(reportSummaryLine(v2)).toBe(
      "研究完成情况：6m · 41 次引用 · 381 个搜索",
    );
  });

  it("projects a historical report into the same Markdown outline", () => {
    const legacy: Report = {
      ...v2,
      format: undefined,
      display_markdown: undefined,
      report: {
        title: "旧报告",
        executive_summary: [
          { text: "摘要", evidence_ids: ["E001"], segment_type: "fact" },
        ],
        sections: [{ heading: "章节", unit_ids: ["R1"], segments: [] }],
        conclusion: [],
      },
    };
    expect(reportMarkdown(legacy)).toContain("摘要[1](#citation-E001)");
    expect(
      reportMarkdown(legacy)
        .split("\n")
        .filter((line) => line.startsWith("## ")),
    ).toEqual(["## 执行摘要", "## 章节", "## 结论"]);
  });

  it("follows the reading position in both directions", () => {
    const line = 96;
    // Before the first section (title block) the first entry is current.
    expect(activeHeadingIndex([300, 900, 1500], line)).toBe(0);
    expect(activeHeadingIndex([-400, 90, 700], line)).toBe(1);
    expect(activeHeadingIndex([-900, -300, 40], line)).toBe(2);
    // Scrolling back up: the third heading is below the line again, so the
    // second section — the one on screen — is current, not the third.
    expect(activeHeadingIndex([-700, -100, 240], line)).toBe(1);
    expect(activeHeadingIndex([], line)).toBe(0);
  });

  it("accepts a message only when the server would", () => {
    const accepts = (status: string, updating = false, welcome = false) =>
      composerAccepts({ status, updating, welcome });
    expect(accepts("", false, true)).toBe(true);
    for (const status of [
      "AWAITING_PLAN_CONFIRMATION",
      "EDITING_PLAN",
      "AWAITING_CLARIFICATION",
      "COMPLETED",
    ])
      expect(accepts(status)).toBe(true);
    // Running research takes a message only as an update the owner opened.
    expect(accepts("RESEARCHING")).toBe(false);
    expect(accepts("RESEARCHING", true)).toBe(true);
    // Too late to steer: the report is being bound and rendered.
    expect(accepts("RENDERING", true)).toBe(false);
    expect(accepts("PLANNING", true)).toBe(false);
    // Without a report the server refuses both (RUN_STOPPED); with one, a
    // stopped or failed follow-up leaves a conversation that can go on.
    for (const status of ["FAILED", "CANCELLED"]) {
      expect(composerAccepts({ status, updating: false, welcome: false })).toBe(
        false,
      );
      expect(
        composerAccepts({
          status,
          updating: false,
          welcome: false,
          hasReport: true,
        }),
      ).toBe(true);
    }
    expect(accepts("FAILED")).toBe(false);
    expect(accepts("CANCELLED")).toBe(false);
    // A selected conversation that has not loaded yet.
    expect(accepts("")).toBe(false);
  });

  it("describes live progress and never regresses it", () => {
    expect(
      liveStatus({ kind: "read", domain: "docs.gitlab.com" }, "RESEARCHING"),
    ).toBe("正在阅读：docs.gitlab.com");
    expect(
      liveStatus({ kind: "writing", title: "功能蓝图" }, "SYNTHESIZING"),
    ).toBe("正在撰写：功能蓝图");
    expect(liveStatus(null, "PLANNING")).toBe("正在调整研究计划…");
    const values = [
      researchProgress("RESEARCHING", { steps: 4, steps_done: 0 }),
      researchProgress("RESEARCHING", { steps: 4, steps_done: 4 }),
      researchProgress("VALIDATING"),
      researchProgress("SYNTHESIZING"),
      researchProgress("RENDERING"),
      researchProgress("COMPLETED"),
    ];
    expect([...values].sort((a, b) => a - b)).toEqual(values);
  });

  it("never sends a limited-report refusal the owner did not express", () => {
    expect(retryRequest(false)).toEqual({});
    expect(retryRequest(true)).toEqual({ allow_limited_report: true });
  });
});

describe("citation excerpts", () => {
  it("shows page text instead of tool envelopes", () => {
    const envelope =
      "[Full web_fetch output saved to /mnt/user-data/outputs/.tool-results/web_fetch-1.log (15352 chars).]\n[Preview kind: text.]\n\nText output:\n- stats\n\nRaw sample (head + tail):\nSource: https://docs.gitlab.com/duo\nTitle: GitLab Duo\nExcerpt: characters 0-12000 of 14172.\n\n# GitLab Duo\n\nSelf-hosted models.";
    expect(readableExcerpt(envelope)).toBe(
      "# GitLab Duo\n\nSelf-hosted models.",
    );
    expect(
      readableExcerpt(
        "Source: https://a.example\nTitle: A\n\nBody\n\n[End of extracted page.]",
      ),
    ).toBe("Body");
    expect(readableExcerpt(undefined)).toBe("");
  });
});

describe("source titles", () => {
  it("keeps real titles and replaces missing ones with the page URL", () => {
    const url = "https://www.meilisearch.com/docs/setup.md";
    expect(sourceTitle("Language - Meilisearch Documentation", url)).toBe(
      "Language - Meilisearch Documentation",
    );
    expect(sourceTitle("Untitled", url)).toBe("meilisearch.com/docs/setup.md");
    expect(sourceTitle(`${url}/`, url)).toBe("meilisearch.com/docs/setup.md");
    expect(sourceTitle("", null)).toBe("未命名来源");
    expect(sourceTitle("Untitled", null)).toBe("未命名来源");
  });
  it("labels opened pages in the activity timeline", () => {
    const url = "https://www.meilisearch.com/docs/setup.md";
    expect(readLabel("Setup guide", url, "meilisearch.com")).toBe(
      "Setup guide · meilisearch.com",
    );
    expect(readLabel("Untitled", url, "meilisearch.com")).toBe(
      "meilisearch.com/docs/setup.md",
    );
    expect(readLabel("Untitled", null, "meilisearch.com")).toBe(
      "meilisearch.com",
    );
  });
});

describe("citation excerpt previews", () => {
  it("turns fetched Markdown into readable plain text", () => {
    const page =
      "Source: https://www.meilisearch.com/docs/language\nTitle: Language\n\n| **Chinese (CMN)** | jieba-based dictionary segmentation |\n|---|---|\n| Japanese | Unidic |";
    expect(excerptPreview(page)).toBe(
      "Chinese (CMN) · jieba-based dictionary segmentation · Japanese · Unidic",
    );
    expect(
      excerptPreview(
        "## Setup\nSee [the guide](https://x.test/guide) and ![logo](a.png) for `smartcn`.",
      ),
    ).toBe("Setup See the guide and for smartcn.");
  });
  it("drops code fences, quote marks and bullets, keeping the words", () => {
    expect(
      excerptPreview(
        "```python\nprint('x')\n```\n> quoted **text**\n- first\n* second\n\n[docs](https://x.test/a)",
      ),
    ).toBe("print('x') quoted text first second docs");
  });
  it("shortens long excerpts", () => {
    const preview = excerptPreview("字".repeat(400), 360);
    expect(preview).toHaveLength(361);
    expect(preview.endsWith("…")).toBe(true);
    expect(excerptPreview(null)).toBe("");
  });
});

describe("source list summaries", () => {
  it("drops a repeated page title and marks a cut-off start", () => {
    expect(
      sourceSummary(
        "Changelog - Meilisearch Documentation 2026-08-10 New Features",
        "Changelog - Meilisearch Documentation",
      ),
    ).toBe("2026-08-10 New Features");
    expect(sourceSummary("Meilisearch 1.15", "Meilisearch 1.15")).toBe("");
    expect(sourceSummary("g unknown words stay intact", "Changelog")).toBe(
      "…g unknown words stay intact",
    );
    expect(sourceSummary("字".repeat(200), "", 140)).toHaveLength(141);
  });
});

describe("page identity", () => {
  it("ignores scheme, www and a trailing slash, like the server", () => {
    const key = pageKey("https://www.example.com/docs/");
    expect(pageKey("http://example.com/docs")).toBe(key);
    expect(pageKey("https://EXAMPLE.com:443/docs#intro")).toBe(key);
    expect(pageKey("https://example.com")).toBe(
      pageKey("http://www.example.com/"),
    );
  });
  it("keeps the query, a custom port and a single-page-app route", () => {
    const key = pageKey("https://example.com/docs");
    expect(pageKey("https://example.com/docs?page=2")).not.toBe(key);
    expect(pageKey("https://example.com:8443/docs")).not.toBe(key);
    expect(pageKey("https://example.com/docs#/doc/5")).not.toBe(key);
    expect(pageKey("https://example.com/docs#!/doc/5")).not.toBe(key);
  });
  it("leaves unparseable locators as they are", () => {
    expect(pageKey("not a url")).toBe("not a url");
    expect(pageKey(null)).toBe("");
  });
});

describe("research metrics formatting", () => {
  it("names graph nodes and leaves unknown ones alone", () => {
    expect(nodeLabel("rewrite")).toBe("请求改写");
    expect(nodeLabel("section")).toBe("章节写作");
    expect(nodeLabel("follow_up")).toBe("追问分流");
    expect(nodeLabel("compaction")).toBe("上下文压缩");
    expect(nodeLabel("unknown")).toBe("未知");
    expect(nodeLabel("custom_node")).toBe("custom_node");
  });
  it("formats tokens, costs, ratios and phase names", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(17000)).toBe("17.0k");
    expect(formatTokens(1234567)).toBe("1.23M");
    expect(formatTokens(null)).toBe("—");
    expect(formatCost(0.0088, "USD")).toBe("$0.0088");
    expect(formatCost(1.2, "CNY")).toBe("¥1.20");
    expect(formatCost(3, "GBP")).toBe("GBP 3.00");
    expect(formatCost(null, "USD")).toBe("—");
    expect(formatPercent(0.552)).toBe("55%");
    expect(formatPercent(null)).toBe("—");
    expect(phaseLabel("dispatch")).toBe("研究");
    expect(phaseLabel("rewrite")).toBe("请求改写");
    expect(phaseLabel("custom-phase")).toBe("custom-phase");
  });
});

describe("what the conversation shows while it waits", () => {
  const user: ResearchMessage = {
    id: "u1",
    role: "user",
    kind: "text",
    text: "q",
    at: "2026-09-19T00:00:00+00:00",
  };
  const run = makeRun("waiting", "PLANNING");
  const plan: ResearchMessage = {
    id: "p1",
    role: "assistant",
    kind: "plan",
    text: "plan",
    plan: run.plan!,
    at: user.at,
    cycle: 0,
  };
  const report: ResearchMessage = {
    id: "r1",
    role: "assistant",
    kind: "report",
    text: "报告",
    at: user.at,
    cycle: 0,
  };
  const version = run.plan!.plan_version;

  it("thinks on creation, holds a skeleton while the first plan is written", () => {
    expect(waitingPhase("CREATED", [user], null)).toBe("thinking");
    expect(waitingPhase("PLANNING", [user], null)).toBe("planning");
    expect(waitingPhase("RESPONDING", [user, plan], version)).toBe("thinking");
  });

  it("disappears with the plan card, and never shows beside a plan under revision", () => {
    // A revised plan keeps its own card, with a live status.
    expect(waitingPhase("PLANNING", [user, plan, user], version)).toBeNull();
    for (const status of [
      "AWAITING_PLAN_CONFIRMATION",
      "RESEARCHING",
      "COMPLETED",
      "FAILED",
      "",
    ])
      expect(waitingPhase(status, [user, plan], version)).toBeNull();
  });

  it("comes back for a follow-up whose previous plan already has its report", () => {
    expect(waitingPhase("PLANNING", [user, plan, report, user], version)).toBe(
      "planning",
    );
  });

  it("folds a plan once its report exists, unless it still has news", () => {
    const reported = { ...run, conversation: [user, plan, report] };
    expect(planCardFolded(plan, { ...reported, status: "COMPLETED" })).toBe(
      true,
    );
    // A legacy run has no conversation records, only a finished status.
    expect(planCardFolded(plan, { ...run, status: "COMPLETED" })).toBe(true);
    expect(planCardFolded(plan, { ...run, status: "RESEARCHING" })).toBe(false);
    // A follow-up that failed or was stopped keeps its error and retry.
    for (const status of ["FAILED", "CANCELLED"])
      expect(planCardFolded(plan, { ...reported, status })).toBe(false);
    // Already folded while the follow-up is planned or answered.
    expect(planCardFolded(plan, { ...reported, status: "PLANNING" })).toBe(
      true,
    );
  });
});
