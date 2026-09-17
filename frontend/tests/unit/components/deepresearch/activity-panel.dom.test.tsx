import { afterEach, expect, it, rs } from "@rstest/core";
import { render } from "@testing-library/react";

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
