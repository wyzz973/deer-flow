import { afterEach, expect, it, rs } from "@rstest/core";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { ResearchPlanCard } from "@/components/deepresearch/plan-card";
import { planCardFolded } from "@/core/deepresearch/presentation";
import type { ResearchMessage } from "@/core/deepresearch/types";

import { makeRun } from "../../core/deepresearch/fixtures";

afterEach(() => {
  rs.useRealTimers();
});

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
  // The arc is sent to where it will be when this second ends (24/45 left)
  // and travels there linearly for one second, so it never jumps.
  const arc = first.container.querySelector("[data-countdown-arc]")!;
  const length = Number(arc.getAttribute("stroke-dasharray"));
  expect(Number(arc.getAttribute("stroke-dashoffset")) / length).toBeCloseTo(
    1 - 24 / 45,
    5,
  );
  expect(arc.getAttribute("class")).toContain("duration-1000");
  expect(arc.getAttribute("class")).toContain("ease-linear");
  expect(arc.getAttribute("class")).toContain("motion-reduce:transition-none");
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
  // The live status shimmers, and a new text is a new element so it fades in.
  const live = screen.getByRole("status");
  expect(live.className).toContain("loading-shimmer");
  expect(live.className).toContain("fade-in");
  // A running step turns the ring spinner; a waiting one is not greyed out.
  const step = screen.getByText("比较并发写入").closest("li")!;
  expect(step.getAttribute("data-step-state")).toBe("running");
  expect(step.querySelector("[data-research-spinner]")).toBeTruthy();
  expect(step.className).toContain("text-base");
  for (const item of view.container.querySelectorAll(
    "li[data-step-state=pending]",
  ))
    expect(item.innerHTML).not.toContain("text-muted-foreground");
  fireEvent.click(screen.getByRole("button", { name: "更新研究要求" }));
  fireEvent.click(screen.getByRole("status"));
  expect(updated && details).toBe(true);
  expect(screen.getByRole("button", { name: "停止研究" })).toBeTruthy();
  view.rerender(
    <ResearchPlanCard
      {...handlers}
      message={planMessage(run)}
      run={run}
      activity={{
        status: "RESEARCHING",
        started_at: run.created_at,
        finished_at: null,
        elapsed_seconds: 31,
        counts: { searches: 13, pages_read: 3, steps: 1, steps_done: 0 },
        current: { kind: "read", domain: "sqlite.org" },
        items: [],
      }}
    />,
  );
  expect(screen.getByRole("status").textContent).toBe("正在阅读：sqlite.org");
  expect(screen.getByRole("status")).not.toBe(live);
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

it("folds the finished plan so it can be reopened, and folds a superseded one", () => {
  const run = makeRun("done", "COMPLETED");
  const message = planMessage(run);
  const view = render(
    <ResearchPlanCard {...handlers} message={message} run={run} />,
  );
  // The plan stays, folded under the report, and its steps are one click away.
  expect(planCardFolded(message, run)).toBe(true);
  const summary = screen.getByText(/研究计划 · Compare databases/);
  const folded = summary.closest("details")!;
  // Closed by default, and the steps it holds open to are its own.
  expect(folded.open).toBe(false);
  expect(folded.contains(screen.getByText("Compare concurrency"))).toBe(true);
  fireEvent.click(summary);
  expect(folded.open).toBe(true);
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
  expect(planCardFolded(message, { ...run, status: "CANCELLED" })).toBe(false);
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

/** A finished report followed by a follow-up question in the same task. */
function followedUp(status: string) {
  const run = makeRun("followed", status);
  const message = { ...planMessage(run), cycle: 0 };
  run.conversation = [
    {
      id: "initial",
      role: "user",
      kind: "text",
      text: "q",
      at: run.created_at,
    },
    message,
    {
      id: "report-1",
      role: "assistant",
      kind: "report",
      text: "报告",
      at: run.created_at,
      cycle: 0,
    },
    { id: "u2", role: "user", kind: "text", text: "追问", at: run.created_at },
  ];
  return { run, message };
}

it("keeps a failed follow-up visible and retryable under the folded plan", () => {
  const { run, message } = followedUp("FAILED");
  run.error = { code: "MODEL_TIMEOUT", message: "模型超时", recoverable: true };
  const retries: boolean[] = [];
  const view = render(
    <ResearchPlanCard
      {...handlers}
      message={message}
      run={run}
      onRetry={(limited = false) => retries.push(limited)}
    />,
  );
  // A failed follow-up shows its failure, not a folded plan …
  expect(screen.queryByText(/研究计划 · Compare databases/)).toBeNull();
  expect(view.container.querySelector("details")).toBeNull();
  // … and the way out is not folded away with it.
  expect(planCardFolded(message, run)).toBe(false);
  expect(screen.getByRole("alert").textContent).toBe("模型超时");
  fireEvent.click(screen.getByRole("button", { name: "从检查点恢复" }));
  expect(retries).toEqual([false]);

  view.rerender(
    <ResearchPlanCard
      {...handlers}
      message={message}
      run={{ ...run, error: { ...run.error, recoverable: false } }}
    />,
  );
  expect(
    screen
      .getByRole("button", { name: "从检查点恢复" })
      .hasAttribute("disabled"),
  ).toBe(true);

  // A superseded plan card never claims the failure of a newer plan.
  view.rerender(
    <ResearchPlanCard
      {...handlers}
      message={message}
      run={{ ...run, plan: { ...run.plan!, plan_version: 2 } }}
    />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: "从检查点恢复" })).toBeNull();
  // Replaced by a newer plan, so it stays "updated" even though its cycle reported.
  expect(screen.getByText(/计划已更新 · Compare databases/)).toBeTruthy();
  view.unmount();
});

it("says a stopped follow-up was stopped instead of looking finished", () => {
  const { run, message } = followedUp("CANCELLED");
  const view = render(
    <ResearchPlanCard {...handlers} message={message} run={run} />,
  );
  // Stopped says so; it does not fold away into something that looks finished.
  expect(screen.queryByText(/研究计划 · Compare databases/)).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("研究已停止");
  expect(screen.getByRole("status").textContent).toContain(
    "继续就这份报告提问",
  );
  view.unmount();
  // The finished task itself shows neither notice.
  const done = followedUp("COMPLETED");
  const finished = render(
    <ResearchPlanCard {...handlers} message={done.message} run={done.run} />,
  );
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText(/研究计划 · Compare databases/)).toBeTruthy();
  finished.unmount();
});

it("hands the start button back when the deadline passes and nothing starts", () => {
  rs.useFakeTimers({
    toFake: [
      "setTimeout",
      "clearTimeout",
      "setInterval",
      "clearInterval",
      "performance",
    ],
  });
  // Two seconds before the server-owned deadline.
  const run = { ...makeRun(), client_received_at: performance.now() - 43_000 };
  let refreshes = 0;
  let started = 0;
  const view = render(
    <ResearchPlanCard
      {...handlers}
      message={planMessage(run)}
      run={run}
      onStart={() => started++}
      onExpire={() => refreshes++}
    />,
  );
  expect(
    screen.getByRole("button", { name: /开始研究 倒计时 2 秒/ }),
  ).toBeTruthy();
  expect(refreshes).toBe(0);

  act(() => {
    rs.advanceTimersByTime(2250);
  });
  const waiting = screen.getByRole("button", { name: "开始研究" });
  expect(waiting.textContent).toBe("即将开始");
  expect(waiting.hasAttribute("disabled")).toBe(true);
  // The server owns the start: ask it what happened instead of guessing.
  expect(refreshes).toBe(1);

  act(() => {
    rs.advanceTimersByTime(5000);
  });
  const recovered = screen.getByRole("button", { name: "开始研究" });
  expect(recovered.textContent).toBe("开始");
  expect(recovered.hasAttribute("disabled")).toBe(false);
  expect(refreshes).toBe(2);
  fireEvent.click(recovered);
  expect(started).toBe(1);
  view.unmount();
});
