import { afterEach, beforeEach, expect, it } from "@rstest/core";
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
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
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
  expect(
    view.client.getQueryState(["research-history", root])?.isInvalidated,
  ).toBe(true);
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
