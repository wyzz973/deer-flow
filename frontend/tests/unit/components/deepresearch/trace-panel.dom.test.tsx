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
