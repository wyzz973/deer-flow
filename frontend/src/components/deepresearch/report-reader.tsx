"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  reportHeadings,
  reportMarkdown,
} from "@/core/deepresearch/presentation";
import type { Report } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import { ResearchReportContent } from "./report-view";

/** Full-page report reading, with a hover table of contents that follows the
 * reader's position. Headings map to rendered elements by order, so the same
 * sanitized Markdown pipeline needs no injected anchors. */
export function ResearchReportReader({
  report,
  onCitation,
}: {
  report: Report;
  onCitation: (id: string) => void;
}) {
  const content = useMemo(() => reportMarkdown(report), [report]);
  const headings = useMemo(() => reportHeadings(content), [content]);
  const scroller = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const [active, setActive] = useState(0);
  const nodes = useCallback(
    () =>
      [...(article.current?.querySelectorAll("h2, h3") ?? [])] as HTMLElement[],
    [],
  );
  useEffect(() => {
    const root = scroller.current;
    if (!root || typeof IntersectionObserver === "undefined") return;
    const targets = nodes();
    const visible = new Set<number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const index = targets.indexOf(entry.target as HTMLElement);
          if (entry.isIntersecting) visible.add(index);
          else visible.delete(index);
        }
        if (visible.size) setActive(Math.min(...visible));
      },
      { root, rootMargin: "0px 0px -65% 0px" },
    );
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, [content, nodes]);
  const jump = (index: number) => {
    setActive(index);
    nodes()[index]?.scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  };
  return (
    <div className="relative size-full">
      {headings.length > 1 && (
        <nav
          aria-label="报告目录"
          className="group absolute top-24 left-3 z-20 hidden md:block"
        >
          <div className="flex flex-col gap-2 py-2 pr-4">
            {headings.map((heading, index) => (
              <span
                key={`${heading.text}-${index}`}
                className={cn(
                  "block h-0.5 rounded-full transition-colors",
                  heading.level === 2 ? "w-5" : "ml-1.5 w-3.5",
                  index === active ? "bg-foreground" : "bg-muted-foreground/40",
                )}
              />
            ))}
          </div>
          <div className="bg-popover text-popover-foreground border-border/60 invisible absolute top-0 left-0 max-h-[70vh] w-64 overflow-y-auto rounded-xl border p-3 opacity-0 shadow-lg transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100">
            <p className="text-muted-foreground mb-2 text-xs">目录</p>
            <ol className="space-y-1">
              {headings.map((heading, index) => (
                <li key={`${heading.text}-${index}`}>
                  <button
                    type="button"
                    onClick={() => jump(index)}
                    className={cn(
                      "hover:bg-muted w-full rounded-md px-2 py-1 text-left text-xs leading-5",
                      heading.level === 3 && "pl-5",
                      index === active
                        ? "text-foreground font-medium"
                        : "text-muted-foreground",
                    )}
                  >
                    {heading.text}
                  </button>
                </li>
              ))}
            </ol>
          </div>
        </nav>
      )}
      <div
        ref={scroller}
        data-research-report-reader
        className="size-full overflow-y-auto px-6 pt-20 pb-16"
      >
        <article ref={article} className="mx-auto max-w-(--container-width-md)">
          <ResearchReportContent report={report} onCitation={onCitation} />
          {report.format !== "markdown-v2" && report.limitations.length > 0 && (
            <details className="text-muted-foreground mt-8 border-t pt-4 text-xs">
              <summary className="cursor-pointer">研究范围与限制</summary>
              {report.limitations.map((text) => (
                <p className="mt-2 leading-5" key={text}>
                  {text}
                </p>
              ))}
            </details>
          )}
        </article>
      </div>
    </div>
  );
}
