import { expect, it } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";

import { ResearchPlanCard } from "@/components/deepresearch/plan-card";
import type { ResearchMessage } from "@/core/deepresearch/types";

import { makeRun } from "../../core/deepresearch/fixtures";

it("does not restart a cached plan countdown when the card remounts", () => {
  const run = { ...makeRun(), client_received_at: performance.now() - 20_100 };
  const message: ResearchMessage = {
    id: "plan-1",
    role: "assistant",
    kind: "plan",
    text: run.plan!.goal,
    plan: run.plan!,
    at: run.created_at,
  };
  const noop = () => undefined;
  const card = (
    <ResearchPlanCard
      message={message}
      run={run}
      busy={false}
      onEdit={noop}
      onStart={noop}
      onCancel={noop}
      onResume={noop}
      onDetails={noop}
      onRetry={noop}
    />
  );
  const first = render(card);
  expect(
    screen.getByRole("button", { name: /开始研究 倒计时 25 秒/ }),
  ).toBeTruthy();
  first.unmount();
  const second = render(card);
  expect(
    screen.getByRole("button", { name: /开始研究 倒计时 25 秒/ }),
  ).toBeTruthy();
  second.unmount();
});

it("offers an explicit limited report for evidence gaps, never for other failures", () => {
  const run = makeRun("gapped", "FAILED");
  run.evidence_count = 4;
  run.error = {
    code: "RESEARCH_GAPS",
    message: "Evidence gaps remain",
    recoverable: false,
  };
  const message: ResearchMessage = {
    id: "plan-1",
    role: "assistant",
    kind: "plan",
    text: run.plan!.goal,
    plan: run.plan!,
    at: run.created_at,
  };
  let approved = false;
  const noop = () => undefined;
  const props = {
    message,
    run,
    busy: false,
    onEdit: noop,
    onStart: noop,
    onCancel: noop,
    onResume: noop,
    onDetails: noop,
    onRetry: (limited = false) => {
      approved = limited;
    },
  };
  const view = render(<ResearchPlanCard {...props} />);
  const button = screen.getByRole("button", { name: "生成带限制的报告" });
  expect(button.hasAttribute("disabled")).toBe(false);
  fireEvent.click(button);
  expect(approved).toBe(true);
  view.rerender(<ResearchPlanCard {...props} busy />);
  expect(
    screen
      .getByRole("button", { name: "生成带限制的报告" })
      .hasAttribute("disabled"),
  ).toBe(true);
  view.rerender(
    <ResearchPlanCard
      {...props}
      run={{ ...run, error: { ...run.error, code: "EVIDENCE_REFERENCE" } }}
    />,
  );
  expect(screen.queryByRole("button", { name: "生成带限制的报告" })).toBeNull();
  expect(
    screen
      .getByRole("button", { name: "从检查点恢复" })
      .hasAttribute("disabled"),
  ).toBe(true);
  view.rerender(
    <ResearchPlanCard {...props} run={{ ...run, evidence_count: 0 }} />,
  );
  expect(screen.queryByRole("button", { name: "生成带限制的报告" })).toBeNull();
  view.unmount();
});
