import { expect, it } from "@rstest/core";

import {
  isRejection,
  latestSnapshot,
  parseResearchEvent,
  runIdFromPath,
  streamRetryDelay,
} from "@/core/deepresearch/events";

import { makeRun } from "./fixtures";

it("rejects malformed replay frames and accepts valid server events", () => {
  for (const value of [
    "not-json",
    "null",
    "[]",
    '{"seq":1}',
    '{"seq":-1,"type":"event","run_id":"r","at":"now","data":{}}',
  ]) {
    expect(parseResearchEvent(value)).toBeUndefined();
  }
  expect(
    parseResearchEvent(
      JSON.stringify({
        seq: 1,
        type: "run.completed",
        run_id: "r",
        at: "now",
        data: {},
      }),
    )?.seq,
  ).toBe(1);
});

it("keeps a newer snapshot but never borrows data from another run", () => {
  const old = makeRun();
  const fresh = {
    ...old,
    updated_at: "2026-09-16T00:00:02Z",
    status: "EDITING_PLAN",
  };
  expect(latestSnapshot(fresh, old)).toBe(fresh);
  expect(latestSnapshot(fresh, makeRun("another"))).toHaveProperty(
    "run_id",
    "another",
  );
});

it("tells a refused request from an uncertain one", () => {
  const http = (status: number) => Object.assign(new Error("x"), { status });
  expect(isRejection(http(409))).toBe(true);
  expect(isRejection(http(422))).toBe(true);
  // A proxy error says nothing about what the gateway accepted.
  expect(isRejection(http(502))).toBe(false);
  expect(isRejection(http(504))).toBe(false);
  expect(isRejection(new TypeError("Failed to fetch"))).toBe(false);
  expect(isRejection(undefined)).toBe(false);
});

it("backs off stream rebuilds from 1s to a 15s ceiling", () => {
  expect([0, 1, 2, 3, 4, 5, 50].map(streamRetryDelay)).toEqual([
    1000, 2000, 4000, 8000, 15000, 15000, 15000,
  ]);
});

it("reads the selected conversation from a workspace address", () => {
  expect(runIdFromPath("/workspace/deepresearch")).toBeUndefined();
  expect(runIdFromPath("/workspace/deepresearch/")).toBeUndefined();
  expect(runIdFromPath("/workspace/deepresearch/run-1")).toBe("run-1");
  expect(runIdFromPath("/workspace/deepresearch/a%2Fb")).toBe("a/b");
  // Not a conversation: the hook must leave its selection alone.
  expect(runIdFromPath("/workspace/deepresearch/settings")).toBeNull();
  expect(runIdFromPath("/workspace/chats/new")).toBeNull();
  expect(runIdFromPath("/workspace/deepresearch/x/y")).toBeNull();
  expect(runIdFromPath("/workspace/deepresearch/%E0%A4%A")).toBeNull();
});
