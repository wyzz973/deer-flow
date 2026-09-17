import { describe, expect, it } from "@rstest/core";

import {
  citationId,
  countdownSeconds,
  escapeReportText,
  excerptPreview,
  formatCost,
  formatDuration,
  formatElapsed,
  formatPercent,
  formatTokens,
  liveStatus,
  phaseLabel,
  reportHeadings,
  reportMarkdown,
  readableExcerpt,
  reportSummaryLine,
  researchProgress,
  readLabel,
  retryRequest,
  sourceSummary,
  sourceTitle,
  uniqueCitationIds,
} from "@/core/deepresearch/presentation";
import type { Report } from "@/core/deepresearch/types";

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

  it("builds the reader outline from report headings outside code", () => {
    expect(reportHeadings(reportMarkdown(v2))).toEqual([
      { level: 2, text: "执行摘要" },
      { level: 3, text: "路线图 P0" },
    ]);
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
    expect(reportHeadings(reportMarkdown(legacy)).map((h) => h.text)).toEqual([
      "执行摘要",
      "章节",
      "结论",
    ]);
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

describe("research metrics formatting", () => {
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
    expect(phaseLabel("custom-phase")).toBe("custom-phase");
  });
});
