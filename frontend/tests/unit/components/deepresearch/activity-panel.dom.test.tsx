import { afterEach, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { ResearchActivityPanel } from "@/components/deepresearch/sources-panel";
import type { ActivityItem } from "@/core/deepresearch/types";

import { activity } from "../../core/deepresearch/fixtures";

class ObserverMock {
  static last?: ObserverMock;
  observe = rs.fn();
  disconnect = rs.fn();
  constructor(private callback: IntersectionObserverCallback) {
    ObserverMock.last = this;
  }
  emit(isIntersecting: boolean) {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this as never,
    );
  }
}

const original = globalThis.IntersectionObserver;
const scrollDescriptor = Object.getOwnPropertyDescriptor(
  Element.prototype,
  "scrollIntoView",
);
afterEach(() => {
  globalThis.IntersectionObserver = original;
  if (scrollDescriptor) {
    Object.defineProperty(
      Element.prototype,
      "scrollIntoView",
      scrollDescriptor,
    );
  } else {
    Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  }
});

function note(text: string, second: number): ActivityItem {
  return {
    kind: "note",
    at: `2026-09-16T00:00:${String(second).padStart(2, "0")}+00:00`,
    unit_id: "R1",
    text,
  };
}

it("follows live activity only while the end of the timeline is visible", () => {
  globalThis.IntersectionObserver = ObserverMock as never;
  const scroll = rs.fn();
  Element.prototype.scrollIntoView = scroll;
  const running = {
    ...activity("RESEARCHING"),
    items: [note("先核对官方文档", 1)],
  };
  const view = render(
    <ResearchActivityPanel activity={running} onInspect={() => undefined} />,
  );

  ObserverMock.last!.emit(false);
  view.rerender(
    <ResearchActivityPanel
      activity={{
        ...running,
        items: [...running.items, note("再核对价格页", 2)],
      }}
      onInspect={() => undefined}
    />,
  );
  expect(scroll).not.toHaveBeenCalled();

  ObserverMock.last!.emit(true);
  view.rerender(
    <ResearchActivityPanel
      activity={{
        ...running,
        items: [...running.items, note("再核对价格页", 2), note("整理结论", 3)],
      }}
      onInspect={() => undefined}
    />,
  );
  expect(scroll).toHaveBeenCalledTimes(1);

  const finished = {
    ...running,
    status: "COMPLETED",
    finished_at: "2026-09-16T00:01:00+00:00",
    items: [
      ...running.items,
      note("完成", 4),
      note("收尾", 5),
      note("结束", 6),
    ],
  };
  view.rerender(
    <ResearchActivityPanel activity={finished} onInspect={() => undefined} />,
  );
  expect(scroll).toHaveBeenCalledTimes(1);
});

function read(
  title: string,
  domain: string,
  second: number,
  status?: string,
): ActivityItem {
  return {
    kind: "read",
    at: `2026-09-16T00:00:${String(second).padStart(2, "0")}+00:00`,
    url: `https://${domain}/${second}`,
    domain,
    title,
    status,
  };
}

it("speaks two dialects only: a dot for progress, a globe with site pills for the web", () => {
  const domains = ["a.example", "b.example", "c.example", "d.example"];
  const finished = {
    ...activity("COMPLETED"),
    finished_at: "2026-09-16T00:08:00+00:00",
    elapsed_seconds: 475,
    items: [
      note("先核对官方文档", 1),
      {
        kind: "search",
        at: "2026-09-16T00:00:02+00:00",
        count: 3,
        queries: ["vector db benchmark"],
        domains: [...domains, "e.example", "f.example"],
      },
      // Consecutive page reads fold into one entry.
      read("Docs", "a.example", 3),
      read("Blog", "b.example", 4),
      read("Down", "c.example", 5, "error"),
      { kind: "done", at: "2026-09-16T00:08:00+00:00", title: "报告" },
    ] satisfies ActivityItem[],
  };
  const view = render(
    <ResearchActivityPanel
      activity={finished}
      title="开源向量数据库选型对比"
      onInspect={() => undefined}
    />,
  );
  // The plan title heads the panel.
  expect(
    screen.getByRole("heading", { name: "开源向量数据库选型对比" }).className,
  ).toContain("text-lg");
  const rows = [...view.container.querySelectorAll("li[data-activity-row]")];
  expect(rows.map((row) => row.getAttribute("data-activity-row"))).toEqual([
    "note",
    "web",
    "web",
    "web",
    "note",
  ]);
  const search = within(rows[1] as HTMLElement);
  expect(search.getByText("已搜索 6 个网站")).toBeTruthy();
  // Four pills, the rest on request, in place.
  for (const domain of domains) expect(search.getByText(domain)).toBeTruthy();
  expect(search.queryByText("e.example")).toBeNull();
  const pill = search.getByText("a.example").parentElement!;
  expect(pill.className).toContain("h-5");
  expect(pill.className).toContain("rounded-full");
  expect(pill.className).toContain("text-[13px]");
  fireEvent.click(search.getByRole("button", { name: "再显示 2 个" }));
  expect(search.getByText("f.example")).toBeTruthy();
  expect(search.queryByRole("button", { name: /再显示/ })).toBeNull();

  expect(
    within(rows[2] as HTMLElement).getByText("已阅读 2 个网页"),
  ).toBeTruthy();
  expect(
    within(rows[2] as HTMLElement)
      .getByText("Docs · a.example")
      .closest("a")!
      .getAttribute("href"),
  ).toBe("https://a.example/3");
  expect(
    within(rows[3] as HTMLElement).getByText("读取失败 · 1 个网页"),
  ).toBeTruthy();
  // Entries are 14px/20px; the last progress entry draws no connecting line.
  expect(rows[0]!.querySelector(".text-sm.leading-5")).toBeTruthy();
  expect(rows[0]!.querySelector("span[aria-hidden].w-px")).toBeTruthy();
  expect(rows[4]!.querySelector("span[aria-hidden].w-px")).toBeNull();
  expect(view.container.textContent).toContain("用时 7m 55s · 已完成");
});

it("says 正在搜索 only for the newest entry of research that is still running", () => {
  const item: ActivityItem = {
    kind: "search",
    at: "2026-09-16T00:00:02+00:00",
    count: 1,
    queries: [],
    domains: ["a.example"],
  };
  const view = render(
    <ResearchActivityPanel
      activity={{ ...activity("RESEARCHING"), items: [item, item] }}
      onInspect={() => undefined}
    />,
  );
  expect(screen.getAllByText("正在搜索")).toHaveLength(1);
  expect(screen.getAllByText("已搜索 1 个网站")).toHaveLength(1);
  view.unmount();
});
