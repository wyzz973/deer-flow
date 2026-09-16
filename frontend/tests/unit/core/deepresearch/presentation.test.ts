import { describe, expect, it } from "@rstest/core";

import {
  citationId,
  countdownSeconds,
  escapeReportText,
  uniqueCitationIds,
} from "@/core/deepresearch/presentation";

describe("report-owned citation anchors", () => {
  const citations = { E001: 1 };
  it("renders technical angle brackets as text, not markup", () => {
    expect(escapeReportText("<name>_docsize & <script>")).toBe(
      "&lt;name&gt;_docsize &amp; &lt;script&gt;",
    );
  });
  it("accepts both raw and native-sanitized anchors", () => {
    expect(citationId("#citation-E001", citations)).toBe("E001");
    expect(citationId("#user-content-citation-E001", citations)).toBe("E001");
  });
  it("shows one citation for multiple excerpts from a page", () => {
    expect(
      uniqueCitationIds(["E001", "E002", "E003", "E999"], {
        E001: 1,
        E002: 1,
        E003: 2,
      }),
    ).toEqual(["E001", "E003"]);
  });
  it("does not trust external URLs, missing IDs or inherited keys", () => {
    for (const href of [
      undefined,
      "https://example.org/#citation-E001",
      "#citation-E999",
      "#citation-__proto__",
      "#citation-E001/other",
    ]) {
      expect(citationId(href, citations)).toBeUndefined();
    }
    expect(citationId("#citation-E001", { E001: 0 })).toBeUndefined();
  });
});

describe("server-owned countdown display", () => {
  const start = "2026-09-15T00:00:00.000Z";
  const deadline = "2026-09-15T00:00:45.000Z";
  it("starts at 45, even before the first client timer tick", () => {
    expect(countdownSeconds(deadline, start, -250)).toBe(45);
    expect(countdownSeconds(deadline, start, 0)).toBe(45);
  });
  it("decreases with elapsed monotonic time and stays at zero after expiry", () => {
    expect(countdownSeconds(deadline, start, 1001)).toBe(44);
    expect(countdownSeconds(deadline, start, 45000)).toBe(0);
    expect(countdownSeconds(deadline, start, 90000)).toBe(0);
  });
  it("restores the remaining interval from a new server snapshot", () => {
    expect(countdownSeconds(deadline, "2026-09-15T00:00:30.000Z", 0)).toBe(15);
  });
  it("does not invent a deadline for paused or malformed state", () => {
    expect(countdownSeconds(null, start, 0)).toBeNull();
    expect(countdownSeconds("invalid", start, 0)).toBeNull();
    expect(countdownSeconds(deadline, "invalid", 0)).toBeNull();
  });
});
