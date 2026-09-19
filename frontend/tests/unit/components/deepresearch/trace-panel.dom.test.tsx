import { expect, it } from "@rstest/core";
import { render, screen, waitFor } from "@testing-library/react";

import { ResearchTraceInspector } from "@/components/deepresearch/trace-panel";
import type { researchApi } from "@/core/deepresearch/api";
import type { ResearchEvent } from "@/core/deepresearch/types";

it("loads final trace events when a fast failure skips the active render", async () => {
  const cursors: number[] = [];
  const event = (
    seq: number,
    type: string,
    status?: string,
  ): ResearchEvent => ({
    seq,
    type,
    run_id: "run",
    at: "2026-09-15T00:00:00Z",
    data: {
      span_id: "span",
      kind: "model",
      name: "local-model",
      status,
      duration_ms: 2,
    },
  });
  const api = {
    trace: async (_run: string, cursor: number) => {
      cursors.push(cursor);
      return cursor === 0
        ? { items: [event(1, "trace.started")], next_cursor: 1 }
        : { items: [event(2, "trace.ended", "error")], next_cursor: 2 };
    },
  } as unknown as ReturnType<typeof researchApi>;
  const view = render(
    <ResearchTraceInspector
      api={api}
      runId="run"
      active={false}
      revision="waiting"
    />,
  );
  await waitFor(() => expect(cursors).toEqual([0]));
  view.rerender(
    <ResearchTraceInspector
      api={api}
      runId="run"
      active={false}
      revision="failed"
    />,
  );
  await waitFor(() =>
    expect(screen.getByText("error", { exact: true })).toBeTruthy(),
  );
  expect(cursors).toEqual([0, 1]);
  view.unmount();
});

function span(seq: number): ResearchEvent {
  return {
    seq,
    type: "trace.ended",
    run_id: "run",
    at: "2026-09-15T00:00:00Z",
    data: {
      span_id: `span-${seq}`,
      kind: "tool",
      name: `tool-${seq}`,
      status: "ok",
      duration_ms: 1,
    },
  };
}

it("reads a finished run's trace to the end, not just its first page", async () => {
  const total = 450;
  const requests: { cursor: number; limit: number }[] = [];
  const api = {
    trace: async (_run: string, cursor: number, limit: number) => {
      requests.push({ cursor, limit });
      const items = Array.from(
        { length: Math.min(limit, total - cursor) },
        (_, index) => span(cursor + index + 1),
      );
      return { items, next_cursor: cursor + items.length };
    },
  } as unknown as ReturnType<typeof researchApi>;
  const view = render(
    <ResearchTraceInspector api={api} runId="run" active={false} />,
  );
  await waitFor(() => expect(screen.getByText(/^450 个事件/)).toBeTruthy());
  // A short page is the end of the trace: no further request, nothing pending.
  expect(requests).toEqual([
    { cursor: 0, limit: 200 },
    { cursor: 200, limit: 200 },
    { cursor: 400, limit: 200 },
  ]);
  expect(screen.queryByText(/还有更多/)).toBeNull();
  // Hundreds of timeline marks stay out of the tab order; rows remain reachable.
  const marks = screen.getAllByRole("button", { name: /^定位 / });
  expect(marks).toHaveLength(total);
  expect(marks.every((mark) => mark.tabIndex === -1)).toBe(true);
  expect(screen.getByRole("button", { name: "tool-450" }).tabIndex).toBe(0);
  view.unmount();
});

it("stops one load at the cap and says more remains", async () => {
  const requests: number[] = [];
  const api = {
    // An endless trace: every page is full.
    trace: async (_run: string, cursor: number, limit: number) => {
      requests.push(cursor);
      return {
        items: [span(cursor + 1)].concat(
          Array(limit - 1).fill(span(cursor + 1)),
        ),
        next_cursor: cursor + limit,
      };
    },
  } as unknown as ReturnType<typeof researchApi>;
  const view = render(
    <ResearchTraceInspector api={api} runId="run" active={false} />,
  );
  await waitFor(() => expect(screen.getByText(/还有更多$/)).toBeTruthy());
  expect(requests).toHaveLength(25);
  const next = screen.getByRole("button", { name: "还有更多事件，继续加载" });
  await waitFor(() => expect(next.hasAttribute("disabled")).toBe(false));
  // Continuing resumes from the cursor instead of starting over.
  next.click();
  await waitFor(() => expect(requests).toHaveLength(50));
  expect(requests[25]).toBe(5000);
  view.unmount();
});
