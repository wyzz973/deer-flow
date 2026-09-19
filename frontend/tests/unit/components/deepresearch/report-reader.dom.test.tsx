import { afterEach, expect, it } from "@rstest/core";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

import { ResearchReportReader } from "@/components/deepresearch/report-reader";
import type { Report } from "@/core/deepresearch/types";

afterEach(cleanup);

const markdown = [
  "# 标题",
  "",
  "## 一、背景",
  "",
  "第一段结论[1](#citation-E001)。",
  "",
  // A setext heading: a text line underlined with dashes renders as <h2>.
  "小结：以下为要点",
  "---",
  "",
  "## 二、对比[1](#citation-E001)",
  "",
  "正文",
  "",
  // Quoted material, not a section of this report.
  "> ## 引用里的标题",
  "",
  "### 二点一 细节",
  "",
  "正文",
  "",
  "## 三、结论",
  "",
  "完",
].join("\n");

const report = {
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
} satisfies Report;

// The title (h1) has a tick and an entry of its own, like ChatGPT's rail.
const expected = [
  "标题",
  "一、背景",
  "小结：以下为要点",
  "二、对比",
  "二点一 细节",
  "三、结论",
];

async function open() {
  const view = render(
    <ResearchReportReader report={report} onCitation={() => undefined} />,
  );
  const trigger = await screen.findByRole("button", { name: "目录" });
  await waitFor(() => expect(trigger.children).toHaveLength(expected.length));
  return { view, trigger };
}

function entries() {
  return within(screen.getByRole("navigation", { name: "报告目录" }))
    .getAllByRole("button")
    .filter((button) => button.getAttribute("aria-label") !== "目录");
}

it("binds every table-of-contents entry to the heading it names", async () => {
  const { view, trigger } = await open();
  fireEvent.focus(trigger);
  expect(entries().map((entry) => entry.textContent)).toEqual(expected);

  const scrolled: string[] = [];
  for (const heading of view.container.querySelectorAll<HTMLElement>(
    "article h1, article h2, article h3",
  ))
    heading.scrollIntoView = () => {
      scrolled.push(heading.textContent ?? "");
    };
  // Counting Markdown `##` lines put this entry on the setext heading above it.
  fireEvent.click(entries()[3]!);
  fireEvent.click(entries()[5]!);
  expect(scrolled.map((text) => text.replace(/\d+$/, ""))).toEqual([
    "二、对比",
    "三、结论",
  ]);
  expect(entries()[5]!.getAttribute("aria-current")).toBe("location");
});

it("draws one tick per heading, the title included, and swaps the rail for the list", async () => {
  const { trigger } = await open();
  const ticks = [...trigger.children] as HTMLElement[];
  expect(ticks.map((tick) => tick.getAttribute("data-toc-tick"))).toEqual([
    "1",
    "2",
    "2",
    "2",
    "3",
    "2",
  ]);
  // 2px ticks, 15px apart; the current one is the wide, dark one.
  expect(trigger.className).toContain("gap-[13px]");
  expect(ticks[0]!.className).toContain("h-0.5");
  const current = ticks.filter((tick) =>
    tick.className.includes("bg-foreground"),
  );
  expect(current).toHaveLength(1);
  expect(current[0]!.className).toContain("w-6");
  expect(ticks[1]!.className).toContain("w-[18px]");
  expect(ticks[4]!.className).toContain("w-3");
  // Open: the list takes the rail's place; the button stays for the keyboard.
  expect(trigger.className).not.toContain("opacity-0");
  fireEvent.focus(trigger);
  expect(trigger.className).toContain("opacity-0");
  const list = entries()[0]!.closest("[id]")!;
  expect(list.className).toContain("w-[286px]");
  expect(
    entries().filter((entry) => entry.className.includes("font-semibold")),
  ).toHaveLength(1);
  expect(entries()[4]!.className).toContain("pl-4");
});

it("keeps the rail in a gutter of its own and tells the page when the article scrolls", async () => {
  const scrolled: boolean[] = [];
  const view = render(
    <ResearchReportReader
      report={report}
      onCitation={() => undefined}
      onScrolledChange={(value) => scrolled.push(value)}
    />,
  );
  const scroller = view.container.querySelector<HTMLElement>(
    "[data-research-report-reader]",
  )!;
  const nav = await screen.findByRole("navigation", { name: "报告目录" });
  // The rail exists only where the container leaves a 64px gutter for it, so
  // it can never sit on the 624px column (the side panel narrows the
  // container, not the viewport).
  expect(nav.className).toContain("hidden");
  expect(nav.className).toContain("@3xl:block");
  expect(scroller.className).toContain("@3xl:px-16");
  expect(scroller.parentElement!.className).toContain("@container");
  expect(scroller.querySelector("article")!.className).toContain(
    "max-w-[624px]",
  );
  Object.defineProperty(scroller, "scrollTop", {
    configurable: true,
    value: 120,
  });
  fireEvent.scroll(scroller);
  Object.defineProperty(scroller, "scrollTop", {
    configurable: true,
    value: 0,
  });
  fireEvent.scroll(scroller);
  expect(scrolled).toEqual([true, false]);
});

it("opens the table of contents from the keyboard and closes it on Escape or blur", async () => {
  const { trigger } = await open();
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(entries()).toHaveLength(0);

  fireEvent.focus(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(trigger.getAttribute("aria-controls")).toBe(
    entries()[0]!.closest("[id]")!.id,
  );
  // Focus moving into the list keeps it open …
  fireEvent.blur(trigger, { relatedTarget: entries()[0]! });
  expect(entries()).toHaveLength(expected.length);
  // … leaving the navigation closes it.
  fireEvent.blur(entries()[0]!, { relatedTarget: document.body });
  expect(entries()).toHaveLength(0);

  fireEvent.focus(trigger);
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
});

it("highlights the section on screen when scrolling back up", async () => {
  const { view, trigger } = await open();
  const scroller = view.container.querySelector<HTMLElement>(
    "[data-research-report-reader]",
  )!;
  const headings = [
    ...view.container.querySelectorAll<HTMLElement>(
      "article h1, article h2, article h3",
    ),
  ].filter((node) => !node.closest("blockquote"));
  const place = (tops: number[]) => {
    headings.forEach((node, index) => {
      node.getBoundingClientRect = () => ({ top: tops[index]! }) as DOMRect;
    });
    scroller.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
  };
  const active = () =>
    [...trigger.children].findIndex((tick) =>
      tick.className.includes("bg-foreground"),
    );
  const scroll = async () => {
    await act(async () => {
      fireEvent.scroll(scroller);
      await new Promise((done) => requestAnimationFrame(() => done(null)));
    });
  };

  place([-1200, -900, -500, 40, 600, 1200]);
  await scroll();
  expect(active()).toBe(3);
  // Back up: that heading dropped below the reading line again, so the
  // section before it is the one being read.
  place([-1000, -700, -300, 240, 800, 1400]);
  await scroll();
  expect(active()).toBe(2);
});
