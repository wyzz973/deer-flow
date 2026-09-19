import { expect, it, rs } from "@rstest/core";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import {
  CitationPreview,
  SiteIcon,
} from "@/components/deepresearch/citation-preview";
import { ResearchSourcesPanel } from "@/components/deepresearch/sources-panel";
import type { Citation, Report } from "@/core/deepresearch/types";

const citation: Citation = {
  number: 1,
  evidence_id: "E001",
  evidence_ids: ["E001", "E002"],
  excerpts: [
    {
      evidence_id: "E001",
      text: "Source: https://www.meilisearch.com/docs/language\nTitle: Language\n\n| **Chinese (CMN)** | jieba-based dictionary segmentation |\n|---|---|\n| Japanese | Unidic |",
    },
    {
      evidence_id: "E002",
      text: "Meilisearch normalizes Chinese character variants.",
    },
  ],
  url: "https://www.meilisearch.com/docs/language",
  canonical_url: "https://www.meilisearch.com/docs/language",
  source_uri: null,
  title: "Language - Meilisearch Documentation",
  origin: "external",
  source_level: "L4",
  publisher: "web",
  published_at: "2026-09-10",
  snippet: "unused fallback",
  unit_ids: ["U1"],
  provenance: "fetched_document",
  domain: "meilisearch.com",
};

const TABLE_WORDS =
  "Chinese (CMN) · jieba-based dictionary segmentation · Japanese · Unidic";

it("previews the hovered excerpt: site, two-line title, two-line excerpt, nothing else", () => {
  const view = render(
    <CitationPreview citation={citation} evidenceId="E002" />,
  );
  expect(screen.getByText("meilisearch.com")).toBeTruthy();
  const title = document.querySelector<HTMLElement>("[data-citation-title]")!;
  const excerpt = document.querySelector<HTMLElement>(
    "[data-citation-excerpt]",
  )!;
  expect(title.textContent).toBe("Language - Meilisearch Documentation");
  expect(excerpt.textContent).toBe(
    "Meilisearch normalizes Chinese character variants.",
  );
  // Both are clamped to two lines, like ChatGPT's card.
  expect(title.className).toContain("line-clamp-2");
  expect(excerpt.className).toContain("line-clamp-2");
  // No URL, no date, no citation number, no “原文片段” label.
  const card = document.querySelector("[data-citation-preview]")!.textContent;
  for (const absent of ["https://", "2026-09-10", "[1]", "原文片段", "另有"])
    expect(card).not.toContain(absent);
  view.rerender(
    <CitationPreview
      citation={{ ...citation, provenance: "tool_output", title: "Untitled" }}
    />,
  );
  // An untitled page is named by its URL without the scheme.
  expect(screen.getByText("meilisearch.com/docs/language")).toBeTruthy();
  expect(screen.getByText(TABLE_WORDS)).toBeTruthy();
  view.unmount();
});

it("switches between the excerpts that share a citation number", () => {
  const view = render(
    <CitationPreview citation={citation} evidenceId="E002" />,
  );
  const excerpt = () =>
    document.querySelector("[data-citation-excerpt]")!.textContent;
  // The hovered evidence opens first.
  expect(excerpt()).toBe("Meilisearch normalizes Chinese character variants.");
  expect(screen.getByText("2 / 2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "下一段摘录" }));
  expect(excerpt()).toBe(TABLE_WORDS);
  expect(screen.getByText("1 / 2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "上一段摘录" }));
  expect(excerpt()).toBe("Meilisearch normalizes Chinese character variants.");
  // Hovering another marker of the same page starts from that evidence.
  view.rerender(<CitationPreview citation={citation} evidenceId="E001" />);
  expect(excerpt()).toBe(TABLE_WORDS);
  // One excerpt: no switcher at all.
  view.rerender(
    <CitationPreview citation={{ ...citation, excerpts: undefined }} />,
  );
  expect(screen.queryByRole("button", { name: "下一段摘录" })).toBeNull();
  expect(excerpt()).toBe("…unused fallback");
  view.unmount();
});

it("marks a citation that rests on a search excerpt, and only that", () => {
  const view = render(
    <CitationPreview citation={{ ...citation, basis: "search excerpt" }} />,
  );
  const note = screen.getByText("摘录");
  expect(note.getAttribute("title")).toBe("检索摘录，未读取原文");
  expect(note.getAttribute("aria-label")).toBe("检索摘录，未读取原文");
  for (const basis of ["page", "record", undefined] as const) {
    view.rerender(<CitationPreview citation={{ ...citation, basis }} />);
    expect(document.querySelector("[data-citation-basis]")).toBeNull();
  }
  view.unmount();
});

it("floats the excerpt when hovering a cited source", async () => {
  const report = {
    version: 1,
    format: "markdown-v2",
    report: { title: "Report" },
    citations: [{ ...citation, basis: "search excerpt" }],
    citation_map: { E001: 1, E002: 1 },
  } as unknown as Report;
  const view = render(
    <ResearchSourcesPanel report={report} onReference={() => undefined} />,
  );
  // The list shows the title and a two-line summary; the URL only stands in
  // for a missing summary.
  expect(screen.getByText(TABLE_WORDS)).toBeTruthy();
  expect(screen.queryByText(/meilisearch\.com\/docs\/language/)).toBeNull();
  // The entry says when the page itself was never read.
  expect(screen.getByText("摘录").getAttribute("title")).toBe(
    "检索摘录，未读取原文",
  );
  expect(document.querySelector("[data-citation-preview]")).toBeNull();
  fireEvent.pointerEnter(
    screen.getByText("Language - Meilisearch Documentation"),
    { pointerType: "mouse" },
  );
  await waitFor(() =>
    expect(
      document.querySelector("[data-slot=hover-card-content]"),
    ).toBeTruthy(),
  );
  const card = document.querySelector("[data-slot=hover-card-content]");
  expect(card?.textContent).toContain(TABLE_WORDS);
  // Fade only: the card opts out of the zoom and slide of the shared primitive.
  expect(card?.className).toContain("research-fade");
  expect(card?.className).toContain("w-[340px]");
  view.unmount();
});

it("shows the site icon through the gateway, retries once, then keeps a letter", () => {
  rs.useFakeTimers();
  try {
    const view = render(<SiteIcon domain="www.meilisearch.com" />);
    const icon = () => view.container.querySelector("img");
    expect(icon()?.getAttribute("src")).toBe(
      "/api/deepresearch/favicon?domain=meilisearch.com",
    );
    // The gateway may still be fetching a cold icon: show a letter, ask again.
    fireEvent.error(icon()!);
    expect(icon()).toBeNull();
    expect(view.container.textContent).toBe("m");
    act(() => {
      rs.advanceTimersByTime(4000);
    });
    expect(icon()?.getAttribute("src")).toBe(
      "/api/deepresearch/favicon?domain=meilisearch.com&retry=1",
    );
    fireEvent.error(icon()!);
    act(() => {
      rs.advanceTimersByTime(10000);
    });
    expect(icon()).toBeNull();
    view.rerender(<SiteIcon domain="工具执行记录" />);
    expect(icon()).toBeNull();
    view.unmount();
  } finally {
    rs.useRealTimers();
  }
});
