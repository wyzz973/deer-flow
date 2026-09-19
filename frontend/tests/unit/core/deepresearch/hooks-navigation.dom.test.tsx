import { afterEach, beforeEach, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { useResearchConversation } from "@/core/deepresearch/hooks";

import { activity, capabilities, makeRun } from "./fixtures";

// What Next's router reports. It trails `window.location` after a native
// history update, so tests move the two separately.
const router = rs.hoisted(() => ({ pathname: "/workspace/deepresearch" }));
rs.mock("next/navigation", () => ({
  usePathname: () => router.pathname,
}));

const home = "/workspace/deepresearch";
const realFetch = globalThis.fetch;
const realEventSource = globalThis.EventSource;
const clients: QueryClient[] = [];
class TestStream {
  onmessage = null;
  onopen = null;
  onerror = null;
  close() {
    return undefined;
  }
}
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
function setup(id?: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useResearchConversation(id), { wrapper });
}
/** A route change: the browser address and the router agree. */
function navigate(view: { rerender: () => void }, path: string) {
  window.history.pushState(null, "", path);
  router.pathname = path;
  view.rerender();
}
let created = 0;
beforeEach(() => {
  created = 0;
  router.pathname = home;
  window.history.replaceState(null, "", home);
  globalThis.EventSource = TestStream as unknown as typeof EventSource;
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/capabilities")) return json(capabilities());
    if (url.endsWith("/sources")) return json({ sources: [], calls: [] });
    if (url.endsWith("/activity")) return json(activity());
    if (init?.method === "POST") return json(makeRun(`created-${++created}`));
    return json(makeRun(decodeURIComponent(url.split("/").at(-1)!)));
  };
});
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  globalThis.fetch = realFetch;
  globalThis.EventSource = realEventSource;
});

it("returns to a new research when the sidebar link leads back to the root path", async () => {
  const view = setup();
  await waitFor(() => expect(view.result.current.cap?.ready).toBe(true));
  await act(async () => {
    await view.result.current.send("topic");
  });
  // Created in place: the address is rewritten, the route (and this hook)
  // stays mounted.
  expect(window.location.pathname).toBe(`${home}/created-1`);
  expect(view.result.current.runId).toBe("created-1");
  // The router catches up with our own rewrite: nothing to do.
  act(() => {
    router.pathname = `${home}/created-1`;
    view.rerender();
  });
  expect(view.result.current.runId).toBe("created-1");
  await waitFor(() =>
    expect(view.result.current.run?.run_id).toBe("created-1"),
  );

  act(() => navigate(view, home));
  expect(view.result.current.runId).toBeUndefined();
  expect(view.result.current.run).toBeUndefined();
  expect(view.result.current.busy).toBe(false);
  expect(window.location.pathname).toBe(home);
});

it("follows a history link to the run this route was opened with", async () => {
  router.pathname = `${home}/first`;
  window.history.replaceState(null, "", `${home}/first`);
  const view = setup("first");
  await waitFor(() => expect(view.result.current.run?.run_id).toBe("first"));
  act(() => view.result.current.newResearch());
  act(() => {
    router.pathname = home;
    view.rerender();
  });
  await act(async () => {
    await view.result.current.send("another topic");
  });
  act(() => {
    router.pathname = `${home}/created-1`;
    view.rerender();
  });
  expect(view.result.current.runId).toBe("created-1");
  // Same route, same params: Next does not remount the page for this link.
  act(() => navigate(view, `${home}/first`));
  expect(view.result.current.runId).toBe("first");
  await waitFor(() => expect(view.result.current.run?.run_id).toBe("first"));
});

it("ignores a router pathname that trails the address the browser shows", async () => {
  const view = setup();
  await waitFor(() => expect(view.result.current.cap?.ready).toBe(true));
  await act(async () => {
    await view.result.current.send("topic");
  });
  act(() => {
    router.pathname = `${home}/created-1`;
    view.rerender();
  });
  act(() => view.result.current.newResearch());
  await act(async () => {
    await view.result.current.send("second topic");
  });
  expect(window.location.pathname).toBe(`${home}/created-2`);
  // The router only now reports the intermediate root address.
  act(() => {
    router.pathname = home;
    view.rerender();
  });
  expect(view.result.current.runId).toBe("created-2");
});

it("leaves the demo page, which selects by query string, alone", async () => {
  router.pathname = "/deepresearch-demo";
  window.history.replaceState(null, "", "/deepresearch-demo?run=demo-run");
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  clients.push(client);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(
    () => useResearchConversation(undefined, "http://127.0.0.1:8022"),
    { wrapper },
  );
  await waitFor(() => expect(view.result.current.runId).toBe("demo-run"));
  act(() => {
    router.pathname = home;
    view.rerender();
  });
  expect(view.result.current.runId).toBe("demo-run");
});
