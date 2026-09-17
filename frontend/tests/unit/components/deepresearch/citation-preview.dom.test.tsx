import { expect, it, rs } from "@rstest/core";
import { act, fireEvent, render, screen } from "@testing-library/react";

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

it("previews the excerpt behind the hovered citation", () => {
  const view = render(
    <CitationPreview citation={citation} evidenceId="E002" />,
  );
  expect(screen.getByText("原文片段")).toBeTruthy();
  expect(
    screen.getByText("Meilisearch normalizes Chinese character variants."),
  ).toBeTruthy();
  expect(screen.getByText("本页另有 1 段引用片段")).toBeTruthy();
  view.rerender(
    <CitationPreview
      citation={{ ...citation, provenance: "tool_output", title: "Untitled" }}
    />,
  );
  expect(screen.getByText("引用关联片段")).toBeTruthy();
  expect(screen.getByText("meilisearch.com/docs/language")).toBeTruthy();
  expect(
    screen.getByText(
      "Chinese (CMN) · jieba-based dictionary segmentation · Japanese · Unidic",
    ),
  ).toBeTruthy();
  view.unmount();
});

it("floats the original excerpt when hovering a cited source", async () => {
  const report = {
    version: 1,
    format: "markdown-v2",
    report: { title: "Report" },
    citations: [citation],
    citation_map: { E001: 1, E002: 1 },
  } as unknown as Report;
  const view = render(
    <ResearchSourcesPanel report={report} onReference={() => undefined} />,
  );
  // The list itself shows a short summary and the URL without its scheme.
  expect(
    screen.getByText(
      "Chinese (CMN) · jieba-based dictionary segmentation · Japanese · Unidic",
    ),
  ).toBeTruthy();
  expect(
    screen.getByText("2026-09-10 · meilisearch.com/docs/language"),
  ).toBeTruthy();
  expect(screen.queryByText("原文片段")).toBeNull();
  fireEvent.pointerEnter(
    screen.getByText("Language - Meilisearch Documentation"),
    { pointerType: "mouse" },
  );
  expect(await screen.findByText("原文片段")).toBeTruthy();
  const card = document.querySelector("[data-slot=hover-card-content]");
  expect(card?.textContent).toContain(
    "Chinese (CMN) · jieba-based dictionary segmentation · Japanese · Unidic",
  );
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
