import { expect, it } from "@rstest/core";

import { latestSnapshot, parseResearchEvent } from "@/core/deepresearch/events";

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
