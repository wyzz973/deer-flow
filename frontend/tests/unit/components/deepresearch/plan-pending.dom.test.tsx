import { afterEach, expect, it, rs } from "@rstest/core";
import { act, cleanup, render, screen } from "@testing-library/react";

import { ResearchPlanPending } from "@/components/deepresearch/plan-pending";

afterEach(() => {
  cleanup();
  rs.useRealTimers();
});

const skeleton = () => document.querySelector("[data-research-skeleton]");

it("shimmers 正在思考 first, then holds the plan card's place with a skeleton", () => {
  rs.useFakeTimers();
  const view = render(<ResearchPlanPending phase="planning" />);
  const text = screen.getByText("正在思考");
  expect(text.className).toContain("loading-shimmer-tertiary");
  expect(skeleton()).toBeNull();
  act(() => {
    rs.advanceTimersByTime(6000);
  });
  expect(screen.getByText("正在制定研究计划").className).toContain(
    "loading-shimmer",
  );
  const block = skeleton()!;
  expect(block.className).toContain("loading-results-shimmer");
  expect(block.className).toContain("rounded-[24px]");
  expect(block.getAttribute("aria-hidden")).toBe("true");
  // The wait is announced once, as a status, not as a stream of changes.
  expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
  // The plan card replaces the whole placeholder.
  view.unmount();
  expect(skeleton()).toBeNull();
  expect(screen.queryByText("正在制定研究计划")).toBeNull();
});

it("shows the skeleton at once when the rewritten request is already there", () => {
  render(<ResearchPlanPending phase="planning" rewritten />);
  expect(skeleton()).toBeTruthy();
  expect(screen.getByText("正在制定研究计划")).toBeTruthy();
});

it("never shows a skeleton while the assistant only answers a question", () => {
  rs.useFakeTimers();
  render(<ResearchPlanPending phase="thinking" rewritten />);
  act(() => {
    rs.advanceTimersByTime(60_000);
  });
  expect(screen.getByText("正在思考")).toBeTruthy();
  expect(skeleton()).toBeNull();
});
