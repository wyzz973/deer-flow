import { afterEach, beforeEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { useResearchConversation } from "@/core/deepresearch/hooks";

import { activity, capabilities, makeRun } from "./fixtures";

const base = "http://127.0.0.1:8022";
const root = `${base}/api/deepresearch`;
const realFetch = globalThis.fetch;
const realEventSource = globalThis.EventSource;
const clients: QueryClient[] = [];
const streams: TestStream[] = [];
class TestStream {
  closed = false;
  // EventSource.OPEN; a test sets 2 (CLOSED) for a stream the browser gave up on.
  readyState = 1;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    streams.push(this);
  }
  close() {
    this.closed = true;
  }
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));
const frame = (seq: number, runId = "run") =>
  ({
    data: JSON.stringify({
      seq,
      type: "activity.tool.completed",
      run_id: runId,
      at: "2026-09-16T00:00:00Z",
      data: {},
    }),
  }) as MessageEvent<string>;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function setup(id?: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    ...renderHook(() => useResearchConversation(id, base), { wrapper }),
    client,
  };
}
beforeEach(() => {
  streams.length = 0;
  globalThis.EventSource = TestStream as unknown as typeof EventSource;
  window.history.replaceState(null, "", "/deepresearch-demo");
});
afterEach(() => {
  rs.useRealTimers();
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  globalThis.fetch = realFetch;
  globalThis.EventSource = realEventSource;
});

it("does not navigate back when an earlier create acknowledgement arrives late", async () => {
  const first = deferred<Response>(),
    second = deferred<Response>();
  let creates = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (init?.method === "POST")
      return ++creates === 1 ? first.promise : second.promise;
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    return json(makeRun(url.split("/").at(-1)));
  };
  const view = setup();
  await waitFor(() => expect(view.result.current.cap?.ready).toBe(true));
  let old!: Promise<void>, newer!: Promise<void>;
  act(() => {
    old = view.result.current.send("first");
  });
  act(() => {
    view.result.current.newResearch();
  });
  act(() => {
    newer = view.result.current.send("second");
  });
  await act(async () => {
    second.resolve(json(makeRun("second")));
    await newer;
  });
  await act(async () => {
    first.resolve(json(makeRun("first")));
    await old;
  });
  expect(creates).toBe(2);
  expect(view.result.current.runId).toBe("second");
  expect(new URL(window.location.href).searchParams.get("run")).toBe("second");
  expect(view.result.current.busy).toBe(false);
});

it("retries an uncertain message with the original id and plan version", async () => {
  const requests: Array<{ client_message_id: string; plan_version: number }> =
    [];
  let current = makeRun();
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/messages")) {
      requests.push(
        JSON.parse(String(init?.body)) as (typeof requests)[number],
      );
      current = {
        ...current,
        updated_at: "2026-09-16T00:00:02Z",
        plan: { ...current.plan!, plan_version: 2 },
      };
      if (requests.length === 1)
        throw new Error("Response lost after server acceptance");
    }
    return json(current);
  };
  const view = setup("run");
  await waitFor(() =>
    expect(view.result.current.run?.plan?.plan_version).toBe(1),
  );
  await act(async () => {
    await view.result.current.send("revise").catch(() => undefined);
  });
  await waitFor(() =>
    expect(view.result.current.run?.plan?.plan_version).toBe(2),
  );
  await act(async () => {
    await view.result.current.send("revise");
  });
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
  expect(requests[1]?.plan_version).toBe(1);
});

it("reconciles a reconnect and refreshes terminal history without a new SSE event", async () => {
  let current = makeRun("run", "RESEARCHING");
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    return json(current);
  };
  const view = setup("run");
  view.client.setQueryData(["research-history", root], [current]);
  // Panels stop polling when the run ends; their last numbers must be re-read.
  view.client.setQueryData(["research-metrics", root, "run"], { stale: true });
  view.client.setQueryData(["research-llm-calls", root, "run"], { items: [] });
  await waitFor(() =>
    expect(view.result.current.run?.status).toBe("RESEARCHING"),
  );
  current = {
    ...current,
    status: "FAILED",
    updated_at: "2026-09-16T00:00:03Z",
  };
  act(() => {
    streams
      .filter((stream) => !stream.closed)
      .at(-1)
      ?.onopen?.();
  });
  await waitFor(() => expect(view.result.current.run?.status).toBe("FAILED"));
  expect(streams.every((stream) => stream.closed)).toBe(true);
  for (const key of [
    ["research-history", root],
    ["research-metrics", root, "run"],
    ["research-llm-calls", root, "run"],
  ])
    expect(view.client.getQueryState(key)?.isInvalidated).toBe(true);
});

it("a delayed GET cannot undo a newer command acknowledgement", async () => {
  const old = makeRun();
  const fresh = {
    ...old,
    status: "EDITING_PLAN",
    updated_at: "2026-09-16T00:00:05Z",
  };
  const delayed = deferred<Response>();
  let gets = 0;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity());
    if (init?.method === "POST") return json(fresh);
    return ++gets === 2 ? delayed.promise : json(old);
  };
  const view = setup("run");
  await waitFor(() => expect(view.result.current.run?.status).toBe(old.status));
  let refresh!: Promise<void>;
  act(() => {
    refresh = view.client.refetchQueries({
      queryKey: ["research-run", root, "run"],
    });
  });
  await waitFor(() => expect(gets).toBe(2));
  await act(async () => {
    await view.result.current.action("plan/pause", { plan_version: 1 });
  });
  await act(async () => {
    delayed.resolve(json(old));
    await refresh;
  });
  expect(view.result.current.run?.status).toBe("EDITING_PLAN");
  expect(
    view.client.getQueryData<{ status: string }>(["research-run", root, "run"])
      ?.status,
  ).toBe("EDITING_PLAN");
});

it("does not submit when capabilities say the service is not ready", async () => {
  let posts = 0;
  globalThis.fetch = async (_input, init) => {
    if (init?.method === "POST") {
      posts++;
      return json(makeRun("unexpected"));
    }
    return json({ ...capabilities(), ready: false });
  };
  const view = setup();
  await waitFor(() => expect(view.result.current.cap?.ready).toBe(false));
  await act(async () => {
    await view.result.current.send("topic").catch(() => undefined);
  });
  expect(posts).toBe(0);
});

it("opens no event stream for a finished run, not even while it loads", async () => {
  const snapshot = deferred<Response>();
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity("COMPLETED"));
    return snapshot.promise;
  };
  const view = setup("done");
  await waitFor(() => expect(view.result.current.cap?.ready).toBe(true));
  // An unknown status must not replay the history of what may be finished.
  expect(streams).toHaveLength(0);
  await act(async () => {
    snapshot.resolve(json(makeRun("done", "COMPLETED")));
    await sleep(20);
  });
  expect(view.result.current.run?.status).toBe("COMPLETED");
  expect(streams).toHaveLength(0);
  expect(view.result.current).not.toHaveProperty("events");
});

it("keeps the view moving while events outpace the snapshot request", async () => {
  let version = 0;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity("RESEARCHING"));
    // Slower than the 150 ms event throttle: cancelling it per event starves.
    await sleep(250);
    version++;
    return json({
      ...makeRun("run", "RESEARCHING"),
      evidence_count: version,
      updated_at: new Date(Date.UTC(2026, 8, 16, 0, 0, version)).toISOString(),
    });
  };
  const view = setup("run");
  await waitFor(() =>
    expect(view.result.current.run?.status).toBe("RESEARCHING"),
  );
  const before = view.result.current.run!.evidence_count;
  const live = streams.at(-1)!;
  for (let seq = 1; seq <= 12; seq++) {
    await act(async () => {
      live.onmessage?.(frame(seq));
      await sleep(100);
    });
  }
  // Still inside the burst: progress is already visible.
  expect(view.result.current.run!.evidence_count).toBeGreaterThan(before + 1);
});

it("continues a running research from its snapshot instead of replaying its events", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity("RESEARCHING"));
    return json({ ...makeRun("run", "RESEARCHING"), last_event_seq: 4200 });
  };
  const view = setup("run");
  await waitFor(() =>
    expect(view.result.current.run?.status).toBe("RESEARCHING"),
  );
  expect(streams).toHaveLength(1);
  expect(streams[0]!.url).toContain("after=4200");
});

it("rebuilds a stream the browser gave up on, polls meanwhile, and stops at the end", async () => {
  let status = "RESEARCHING";
  let runGets = 0;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity(status));
    runGets++;
    return json({
      ...makeRun("run", status),
      updated_at: new Date(Date.UTC(2026, 8, 16, 0, 0, runGets)).toISOString(),
    });
  };
  const view = setup("run");
  await waitFor(() =>
    expect(view.result.current.run?.status).toBe("RESEARCHING"),
  );
  expect(streams).toHaveLength(1);
  await act(async () => {
    streams[0]!.onopen?.();
    streams[0]!.onmessage?.(frame(7));
    await sleep(200);
  });
  rs.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
  });
  const advance = (ms: number) =>
    act(async () => {
      await rs.advanceTimersByTimeAsync(ms);
    });
  const fail = (stream: TestStream) =>
    act(async () => {
      // A non-200 answer (gateway restarting behind nginx): CLOSED, one error
      // event, and the browser never reconnects by itself.
      stream.readyState = 2;
      stream.onerror?.();
    });

  await fail(streams[0]!);
  expect(streams[0]!.closed).toBe(true);
  await advance(999);
  expect(streams).toHaveLength(1);
  await advance(1);
  expect(streams).toHaveLength(2);
  // The rebuilt stream resumes after the last event it saw.
  expect(streams[1]!.url).toContain("after=7");

  // Consecutive failures back off: 2 s, then 4 s.
  await fail(streams[1]!);
  await advance(1999);
  expect(streams).toHaveLength(2);
  await advance(1);
  expect(streams).toHaveLength(3);
  await fail(streams[2]!);
  await advance(3999);
  expect(streams).toHaveLength(3);
  await advance(1);
  expect(streams).toHaveLength(4);

  // An event on the new stream resets the backoff to 1 s.
  await act(async () => {
    streams[3]!.onopen?.();
    streams[3]!.onmessage?.(frame(8));
  });
  await advance(200);
  await fail(streams[3]!);
  await advance(1000);
  expect(streams).toHaveLength(5);

  // The run ended while the stream was down, and the rebuilt stream never
  // opens. Polling the snapshot finds out; after that nothing reconnects and
  // nothing polls.
  status = "FAILED";
  const polled = runGets;
  await advance(5000);
  expect(runGets).toBeGreaterThan(polled);
  expect(view.result.current.run?.status).toBe("FAILED");
  expect(streams.every((stream) => stream.closed)).toBe(true);
  const opened = streams.length;
  const settled = runGets;
  await advance(60_000);
  expect(streams).toHaveLength(opened);
  expect(runGets).toBe(settled);
});

it("leaves reconnecting to the browser while the stream is only interrupted", async () => {
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity("RESEARCHING"));
    return json(makeRun("run", "RESEARCHING"));
  };
  const view = setup("run");
  await waitFor(() =>
    expect(view.result.current.run?.status).toBe("RESEARCHING"),
  );
  await act(async () => {
    // CONNECTING: the browser retries this one itself.
    streams[0]!.readyState = 0;
    streams[0]!.onerror?.();
    await sleep(1300);
  });
  expect(streams).toHaveLength(1);
  expect(streams[0]!.closed).toBe(false);
});

it("drops the idempotency key of a refused message, keeping it only for uncertain outcomes", async () => {
  const keys: string[] = [];
  let refuse = true;
  const current = makeRun("run", "COMPLETED");
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity("COMPLETED"));
    if (url.endsWith("/messages")) {
      const body = JSON.parse(String(init?.body)) as {
        client_message_id: string;
      };
      keys.push(body.client_message_id);
      if (refuse)
        return json(
          { detail: { code: "IDEMPOTENCY_CONFLICT", message: "冲突" } },
          409,
        );
    }
    return json(current);
  };
  const view = setup("run");
  await waitFor(() =>
    expect(view.result.current.run?.status).toBe("COMPLETED"),
  );
  await act(async () => {
    await view.result.current.send("继续").catch(() => undefined);
  });
  expect(view.result.current.error).toBe("冲突");
  refuse = false;
  await act(async () => {
    await view.result.current.send("继续");
  });
  expect(keys).toHaveLength(2);
  // Same text, new identity: the refused key can never be accepted.
  expect(keys[1]).not.toBe(keys[0]);
});
