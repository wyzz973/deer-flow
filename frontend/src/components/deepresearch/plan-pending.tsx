"use client";

import { useEffect, useState } from "react";

/** How long “正在思考” stands alone before the skeleton joins it, when no
 * rewritten request arrives to say that thinking is over. */
const THINKING_MS = 6000;

/** The wait between sending a request and the plan card, like ChatGPT:
 * shimmering “正在思考”, then a rounded skeleton where the card will appear.
 * Presentation only: nothing here starts, polls or times research. */
export function ResearchPlanPending({
  phase,
  rewritten = false,
}: {
  phase: "thinking" | "planning";
  /** The rewritten request for this turn is already in the conversation. */
  rewritten?: boolean;
}) {
  const [waited, setWaited] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setWaited(true), THINKING_MS);
    return () => clearTimeout(timer);
  }, []);
  const skeleton = phase === "planning" && (rewritten || waited);
  return (
    <div
      role="status"
      aria-live="polite"
      data-research-pending={skeleton ? "skeleton" : "thinking"}
      className="space-y-3"
    >
      <p className="loading-shimmer-tertiary pb-0.5 text-base leading-6 select-none">
        {skeleton ? "正在制定研究计划" : "正在思考"}
      </p>
      {skeleton && (
        <div
          aria-hidden
          data-research-skeleton
          className="loading-results-shimmer animate-in fade-in h-[338px] w-full max-w-xl rounded-[24px] duration-300 ease-out"
        />
      )}
    </div>
  );
}
