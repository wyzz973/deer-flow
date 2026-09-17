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

function planMessage(run: ReturnType<typeof makeRun>): ResearchMessage {
  return {
    id: "plan-1",
    role: "assistant",
    kind: "plan",
    text: run.plan!.goal,
    plan: run.plan!,
    at: run.created_at,
  };
}

const handlers = {
  busy: false,
  onEdit: () => undefined,
  onStart: () => undefined,
  onCancel: () => undefined,
  onDetails: () => undefined,
  onRetry: () => undefined,
};

it("shows short step titles and live progress while research runs", () => {
  const run = makeRun("running", "RESEARCHING");
  run.plan!.title = "数据库选型调研";
  run.plan!.research_units[0]!.title = "比较并发写入";
  run.unit_statuses = { U1: "RESEARCHING" };
  let updated = false;
  let details = false;
  const view = render(
    <ResearchPlanCard
      {...handlers}
      message={planMessage(run)}
      run={run}
      activity={{
        status: "RESEARCHING",
        started_at: run.created_at,
        finished_at: null,
        elapsed_seconds: 30,
        counts: { searches: 12, pages_read: 3, steps: 1, steps_done: 0 },
        current: { kind: "search", query: "SQLite WAL concurrency" },
        items: [],
      }}
      onDetails={() => {
        details = true;
      }}
      onUpdate={() => {
        updated = true;
      }}
    />,
  );
  expect(screen.getByRole("heading", { name: "数据库选型调研" })).toBeTruthy();
  expect(screen.getByText("比较并发写入")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe(
    "正在搜索：SQLite WAL concurrency",
  );
  expect(screen.getByText("12 次搜索")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "更新研究要求" }));
  fireEvent.click(screen.getByRole("status"));
  expect(updated && details).toBe(true);
  expect(screen.getByRole("button", { name: "停止研究" })).toBeTruthy();
  // Once research only formats the report, updates are no longer offered.
  view.rerender(
    <ResearchPlanCard
      {...handlers}
      message={planMessage(run)}
      run={{ ...run, status: "RENDERING" }}
      onUpdate={() => undefined}
    />,
  );
  expect(screen.queryByRole("button", { name: "更新研究要求" })).toBeNull();
  view.unmount();
});

it("collapses superseded, finished and stopped plans like ChatGPT", () => {
  const run = makeRun("done", "COMPLETED");
  const message = planMessage(run);
  const view = render(
    <ResearchPlanCard {...handlers} message={message} run={run} />,
  );
  expect(screen.getByText(/研究计划 · Compare databases/)).toBeTruthy();
  view.rerender(
    <ResearchPlanCard
      {...handlers}
      message={message}
      run={{
        ...run,
        status: "RESEARCHING",
        plan: { ...run.plan!, plan_version: 2 },
      }}
    />,
  );
  expect(screen.getByText(/计划已更新 · Compare databases/)).toBeTruthy();
  view.rerender(
    <ResearchPlanCard
      {...handlers}
      message={message}
      run={{ ...run, status: "CANCELLED" }}
    />,
  );
  expect(screen.getByText("研究已停止")).toBeTruthy();
  view.unmount();
});

it("lets an edited plan start as-is without a countdown", () => {
  const run = { ...makeRun("editing", "EDITING_PLAN"), auto_start_at: null };
  let started = false;
  const view = render(
    <ResearchPlanCard
      {...handlers}
      message={planMessage(run)}
      run={run}
      onStart={() => {
        started = true;
      }}
    />,
  );
  expect(
    screen.getByRole("button", { name: "编辑" }).getAttribute("aria-pressed"),
  ).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "开始研究" }));
  expect(started).toBe(true);
  view.unmount();
});
