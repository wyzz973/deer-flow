"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { activeHeadingIndex } from "@/core/deepresearch/presentation";
import type { Report } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import { ResearchReportContent } from "./report-view";

type Heading = { level: number; text: string; node: HTMLElement };

// A heading counts as "being read" once it passes this line below the top of
// the scroller. It sits just under the headings' `scroll-mt-20` (80px), so a
// jump from the table of contents lands on the heading it asked for.
const READING_LINE = 96;

/** Section headings as rendered. Reading them from the DOM (not from the
 * Markdown source) keeps every entry bound to its own element, whatever
 * syntax produced it: ATX or setext, with or without citations. A heading
 * quoted inside a blockquote is cited text, not a section of the report. */
function renderedHeadings(article: HTMLElement | null): Heading[] {
  const found: Heading[] = [];
  // The title (h1) has a tick of its own, like ChatGPT's rail.
  for (const node of article?.querySelectorAll<HTMLElement>("h1, h2, h3") ??
    []) {
    if (node.closest("blockquote")) continue;
    const clone = node.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("button").forEach((button) => button.remove());
    const text = clone.textContent?.replace(/\s+/g, " ").trim();
    if (text) found.push({ level: Number(node.tagName.slice(1)), text, node });
  }
  return found;
}

/** Full-page report reading, with a table of contents that follows the
 * reader's position in both directions. */
export function ResearchReportReader({
  report,
  selectedId,
  onCitation,
  onScrolledChange,
}: {
  report: Report;
  /** Evidence ID of the citation shown in the sources panel. */
  selectedId?: string;
  onCitation: (id: string) => void;
  /** The article left its top: the page header draws its hairline. */
  onScrolledChange?: (scrolled: boolean) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const article = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  // A jump pins its target until the reader scrolls again: the last sections
  // of a report may be too short to ever reach the reading line.
  const pinned = useRef(false);

  // Markdown renders after the first commit and may re-render in blocks.
  useEffect(() => {
    const root = article.current;
    if (!root) return;
    const read = () => {
      const next = renderedHeadings(root);
      setHeadings((old) =>
        old.length === next.length &&
        old.every(
          (item, index) =>
            item.node === next[index]!.node && item.text === next[index]!.text,
        )
          ? old
          : next,
      );
    };
    read();
    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(read);
    observer.observe(root, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    return () => observer.disconnect();
  }, [report]);

  const follow = useCallback(() => {
    const root = scroller.current;
    if (!root || pinned.current || !headings.length) return;
    const line = root.getBoundingClientRect().top + READING_LINE;
    setActive(
      activeHeadingIndex(
        headings.map((heading) => heading.node.getBoundingClientRect().top),
        line,
      ),
    );
  }, [headings]);

  const scrolledChange = useRef(onScrolledChange);
  scrolledChange.current = onScrolledChange;
  useEffect(() => () => scrolledChange.current?.(false), []);

  useEffect(() => {
    const root = scroller.current;
    if (!root) return;
    let frame = 0;
    const onScroll = () => {
      scrolledChange.current?.(root.scrollTop > 0);
      frame ||= requestAnimationFrame(() => {
        frame = 0;
        follow();
      });
    };
    const release = () => {
      pinned.current = false;
    };
    follow();
    root.addEventListener("scroll", onScroll, { passive: true });
    root.addEventListener("wheel", release, { passive: true });
    root.addEventListener("touchmove", release, { passive: true });
    root.addEventListener("keydown", release);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      root.removeEventListener("scroll", onScroll);
      root.removeEventListener("wheel", release);
      root.removeEventListener("touchmove", release);
      root.removeEventListener("keydown", release);
    };
  }, [follow]);

  const jump = (index: number) => {
    pinned.current = true;
    setActive(index);
    headings[index]?.node.scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  };
  const outlined = headings.length > 1;
  return (
    // The rail lives in the left gutter of this container, never over the
    // article: it shows only while the container (not the viewport — the side
    // panel narrows it) is wide enough for a 64px gutter beside the column.
    <div className="@container relative size-full">
      {outlined && (
        <nav
          aria-label="报告目录"
          className="absolute top-[106px] left-3 z-20 hidden @3xl:block"
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={(event) => {
            // Keyboard focus inside keeps the list open after the pointer
            // left; focus left behind by a click does not.
            const focused = document.activeElement;
            if (
              !event.currentTarget.contains(focused) ||
              !focused?.matches(":focus-visible")
            )
              setOpen(false);
          }}
          onFocus={() => setOpen(true)}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget))
              setOpen(false);
          }}
          onKeyDown={(event) => {
            if (event.key !== "Escape" || !open) return;
            event.stopPropagation();
            setOpen(false);
            trigger.current?.blur();
          }}
        >
          <button
            ref={trigger}
            type="button"
            aria-label="目录"
            aria-expanded={open}
            aria-controls={listId}
            // The open list takes the rail's place; the button stays focusable
            // underneath so the keyboard keeps its anchor.
            className={cn(
              "focus-visible:ring-ring flex w-10 flex-col gap-[13px] rounded-md py-2 transition-opacity duration-150 focus-visible:ring-2 focus-visible:outline-none motion-reduce:transition-none",
              open && "opacity-0",
            )}
            onClick={() => setOpen(true)}
          >
            {headings.map((heading, index) => (
              <span
                key={index}
                aria-hidden
                data-toc-tick={heading.level}
                className={cn(
                  "block h-0.5 rounded-full transition-[width,background-color] duration-150 motion-reduce:transition-none",
                  index === active
                    ? "bg-foreground w-6"
                    : heading.level === 3
                      ? "w-3 bg-(--dr-dash)"
                      : "w-[18px] bg-(--dr-dash)",
                )}
              />
            ))}
          </button>
          {open && (
            <div
              id={listId}
              className="bg-popover text-popover-foreground animate-in fade-in-0 absolute -top-[5px] -left-[5px] max-h-[70vh] w-[286px] overflow-y-auto rounded-[16px] border border-(--dr-line) p-5 shadow-lg duration-150 motion-reduce:animate-none"
            >
              <p className="mb-3 text-xs text-(--dr-text-tertiary)">目录</p>
              <ol className="space-y-3">
                {headings.map((heading, index) => (
                  <li key={index}>
                    <button
                      type="button"
                      aria-current={index === active ? "location" : undefined}
                      onClick={() => jump(index)}
                      className={cn(
                        "hover:text-foreground focus-visible:ring-ring block w-full rounded-sm text-left text-base leading-6 transition-colors focus-visible:ring-2 focus-visible:outline-none motion-reduce:transition-none",
                        heading.level === 3 && "pl-4",
                        index === active
                          ? "text-foreground font-semibold"
                          : "font-normal text-(--dr-text-tertiary)",
                      )}
                    >
                      {heading.text}
                    </button>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </nav>
      )}
      <div
        ref={scroller}
        data-research-report-reader
        className="size-full overflow-y-auto px-6 pt-28 pb-16 @3xl:px-16"
      >
        <article ref={article} className="mx-auto max-w-[624px]">
          <ResearchReportContent
            report={report}
            selectedId={selectedId}
            onCitation={onCitation}
          />
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
