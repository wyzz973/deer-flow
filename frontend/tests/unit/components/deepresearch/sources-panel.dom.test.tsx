import { afterEach, expect, it } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";

import { ResearchSourcesPanel } from "@/components/deepresearch/sources-panel";
import type {
  Citation,
  DiscoveredSource,
  Report,
} from "@/core/deepresearch/types";

afterEach(cleanup);

const fetched = [
  "Source: https://docs.example.com/guide",
  "Title: Guide",
  "",
  "## Install",
  "",
  "```bash",
  "pip install example",
  "```",
  "",
  "See [the **full** guide](https://docs.example.com/guide/full) and ![logo](https://docs.example.com/logo.png).",
  "",
  "| Option | Default |",
  "|---|---|",
  "| workers | 4 |",
  "",
  "字".repeat(400),
].join("\n");

function citation(overrides: Partial<Citation>): Citation {
  return {
    number: 1,
    evidence_id: "E001",
    title: "Guide",
    url: "https://docs.example.com/guide",
    canonical_url: "https://docs.example.com/guide",
    source_uri: null,
    domain: "docs.example.com",
    snippet: fetched,
    origin: "external",
    source_level: "L4",
    publisher: "docs",
    published_at: null,
    unit_ids: [],
    provenance: "fetched_document",
    ...overrides,
  };
}

function report(citations: Citation[]): Report {
  return {
    version: 1,
    format: "markdown-v2",
    title: "T",
    display_markdown: "# T",
    report: { title: "T" },
    markdown: "# T",
    citations,
    citation_map: Object.fromEntries(
      citations.map((item) => [item.evidence_id, item.number]),
    ),
    limitations: [],
    demo: false,
  };
}

it("highlights the selected citation without unfolding the fetched page", () => {
  const view = render(
    <ResearchSourcesPanel
      report={report([
        citation({}),
        citation({ number: 2, evidence_id: "E009" }),
      ])}
      selectedId="E001"
      onReference={() => undefined}
    />,
  );
  const selected =
    view.container.querySelector<HTMLElement>("[data-selected]")!;
  expect(selected.getAttribute("data-source-id")).toBe("E001");
  // A rounded, tinted block is all that marks it …
  expect(selected.className).toContain("rounded-[16px]");
  expect(selected.className).toContain("bg-(--dr-chip)");
  expect(view.container.querySelectorAll("[data-selected]")).toHaveLength(1);
  // … the entry keeps the same shape as every other: one title line and a
  // two-line excerpt. No label, no expander, no raw page text.
  const excerpts = view.container.querySelectorAll<HTMLElement>(
    "[data-source-excerpt]",
  );
  expect(excerpts).toHaveLength(2);
  expect(excerpts[0]!.textContent).toBe(excerpts[1]!.textContent);
  for (const excerpt of excerpts) {
    expect(excerpt.className).toContain("line-clamp-2");
    // Words, not the fetched page's syntax or transport header.
    expect(excerpt.textContent).toContain("Install pip install example");
    for (const syntax of ["```", "](", "**", "##", "|", "Source:", "Title:"])
      expect(excerpt.textContent).not.toContain(syntax);
    expect(excerpt.textContent.length).toBeLessThanOrEqual(141);
  }
  expect(screen.queryByText("已读取的原文片段")).toBeNull();
  expect(screen.queryByText("引用关联片段")).toBeNull();
  expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
  const title = screen.getAllByRole("link", { name: "Guide" })[0]!;
  expect(title.className).toContain("truncate");
  expect(title.getAttribute("href")).toBe("https://docs.example.com/guide");
});

it("shows the URL only where there is no excerpt to show", () => {
  render(
    <ResearchSourcesPanel
      report={report([
        citation({ snippet: "Short **excerpt**.", provenance: "tool_output" }),
        citation({
          number: 2,
          evidence_id: "E002",
          title: "Bare",
          snippet: "",
          url: "https://www.docs.example.com/bare",
        }),
      ])}
      onReference={() => undefined}
    />,
  );
  expect(
    document.querySelector<HTMLElement>("[data-source-excerpt]")!.textContent,
  ).toBe("Short excerpt.");
  expect(screen.queryByText("docs.example.com/guide")).toBeNull();
  expect(screen.getByText("docs.example.com/bare")).toBeTruthy();
});

it("says which citations rest on a search excerpt", () => {
  render(
    <ResearchSourcesPanel
      report={report([
        citation({ basis: "search excerpt" }),
        citation({ number: 2, evidence_id: "E002", basis: "page" }),
        citation({ number: 3, evidence_id: "E003" }),
      ])}
      onReference={() => undefined}
    />,
  );
  const notes = screen.getAllByText("摘录");
  expect(notes).toHaveLength(1);
  expect(notes[0]!.getAttribute("title")).toBe("检索摘录，未读取原文");
  expect(notes[0]!.getAttribute("aria-label")).toBe("检索摘录，未读取原文");
});

it("uses the excerpt recorded for the selected evidence alias", () => {
  render(
    <ResearchSourcesPanel
      report={report([
        citation({
          evidence_ids: ["E001", "E002"],
          excerpts: [
            { evidence_id: "E001", text: "first window" },
            { evidence_id: "E002", text: "second window" },
          ],
        }),
      ])}
      selectedId="E002"
      onReference={() => undefined}
    />,
  );
  // (A lower-case start reads as mid-sentence, hence the ellipsis.)
  expect(document.querySelector("[data-source-excerpt]")!.textContent).toBe(
    "…second window",
  );
});

function discovered(
  id: string,
  url: string,
  status: DiscoveredSource["status"] = "discovered",
): DiscoveredSource {
  return {
    id,
    url,
    domain: new URL(url).hostname.replace(/^www\./, ""),
    title: id,
    title_observed: true,
    connector: null,
    origin: "external",
    status,
    call_ids: [],
    excerpt: "",
  };
}

it("lists other pages by page identity, not by URL spelling", () => {
  render(
    <ResearchSourcesPanel
      report={report([
        citation({}),
        citation({
          number: 2,
          evidence_id: "E003",
          url: "https://blog.example.com/post",
          canonical_url: "https://blog.example.com/post?ref=canonical",
          domain: "blog.example.com",
        }),
      ])}
      data={{
        calls: [],
        sources: [
          // The cited page again, spelled four ways.
          discovered("cited-http", "http://docs.example.com/guide"),
          discovered("cited-www", "https://www.docs.example.com/guide/"),
          discovered("cited-anchor", "https://docs.example.com/guide#install"),
          discovered(
            "cited-canonical",
            "https://blog.example.com/post?ref=canonical",
          ),
          // A query is another page.
          discovered("other-query", "https://docs.example.com/guide?page=2"),
          // One uncited page found twice: the read outranks the search hit.
          discovered("dup-found", "http://www.news.example.com/a/"),
          discovered("dup-read", "https://news.example.com/a", "read"),
          discovered("plain", "https://plain.example.com/"),
        ],
      }}
      onReference={() => undefined}
    />,
  );
  // Listed in the open, grouped by site like the citations, without numbers.
  const scanned = screen.getByRole("region", { name: "已扫描的来源" });
  expect(within(scanned).getByText("已扫描的来源 · 3")).toBeTruthy();
  expect(scanned.closest("details")).toBeNull();
  expect(scanned.querySelector("details")).toBeNull();
  expect(
    within(scanned)
      .getAllByRole("heading")
      .map((heading) => heading.textContent),
  ).toEqual([
    "已扫描的来源 · 3",
    "docs.example.com",
    "news.example.com",
    "plain.example.com",
  ]);
  expect(within(scanned).queryAllByRole("button")).toHaveLength(0);
  expect(within(scanned).getByText("已读取")).toBeTruthy();
  for (const kept of ["other-query", "dup-read", "plain"])
    expect(screen.getByText(kept)).toBeTruthy();
  for (const dropped of [
    "cited-http",
    "cited-www",
    "cited-anchor",
    "cited-canonical",
    "dup-found",
  ])
    expect(screen.queryByText(dropped)).toBeNull();
});

it("lays out a long scanned list a page at a time", () => {
  render(
    <ResearchSourcesPanel
      report={report([citation({})])}
      data={{
        calls: [],
        sources: Array.from({ length: 450 }, (_, index) =>
          discovered(
            `page-${index}`,
            `https://s${index % 9}.example.com/${index}`,
          ),
        ),
      }}
      onReference={() => undefined}
    />,
  );
  const scanned = screen.getByRole("region", { name: "已扫描的来源" });
  // The count is the whole study's; the DOM holds one page of it.
  expect(within(scanned).getByText("已扫描的来源 · 450")).toBeTruthy();
  expect(within(scanned).getAllByRole("link")).toHaveLength(200);
  fireEvent.click(
    within(scanned).getByRole("button", { name: "再显示 200 个" }),
  );
  expect(within(scanned).getAllByRole("link")).toHaveLength(400);
  fireEvent.click(
    within(scanned).getByRole("button", { name: "再显示 50 个" }),
  );
  expect(within(scanned).getAllByRole("link")).toHaveLength(450);
  expect(within(scanned).queryByRole("button")).toBeNull();
});
