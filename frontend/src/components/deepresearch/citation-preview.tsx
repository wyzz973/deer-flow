"use client";

import { ArrowLeft, ArrowRight } from "lucide-react";
import { useEffect, useState } from "react";

import { faviconUrl } from "@/core/deepresearch/api";
import {
  excerptPreview,
  sourceSummary,
  sourceTitle,
} from "@/core/deepresearch/presentation";
import type { Citation } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

function hostOf(url?: string | null) {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, "") : "";
  } catch {
    return "";
  }
}

/** The hover card that previews a citation: 340px wide, fade only. It is
 * portalled out of the page, so it carries the research palette itself. */
export const CITATION_CARD_CLASS =
  "deepresearch-surface research-fade w-[340px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-[16px] border-(--dr-line) p-0 shadow-lg";

const HOSTNAME = /^(?:[a-z0-9-]+\.)+[a-z][a-z0-9-]+$/;

type IconPhase = "first" | "waiting" | "retry" | "failed";

/** The site's own icon, served through the gateway; a letter badge when the
 * site has none or the source is not a web page. A cold icon may still be
 * fetching when first requested, so one failure is retried after a pause. */
export function SiteIcon({
  domain,
  className,
}: {
  domain: string;
  className?: string;
}) {
  const host = domain
    .trim()
    .toLowerCase()
    .replace(/^www\./, "");
  const [state, setState] = useState<{ host: string; phase: IconPhase }>();
  const phase = state?.host === host ? state.phase : "first";
  useEffect(() => {
    if (phase !== "waiting") return;
    const timer = window.setTimeout(
      () => setState({ host, phase: "retry" }),
      4000,
    );
    return () => window.clearTimeout(timer);
  }, [host, phase]);
  if (HOSTNAME.test(host) && (phase === "first" || phase === "retry")) {
    return (
      <span
        aria-hidden
        className={cn(
          "flex size-5 shrink-0 items-center justify-center overflow-hidden rounded-full bg-white ring-1 ring-black/10 dark:ring-white/15",
          className,
        )}
      >
        <img
          key={phase}
          src={`${faviconUrl(host)}${phase === "retry" ? "&retry=1" : ""}`}
          alt=""
          loading="lazy"
          decoding="async"
          // Scales with the badge: 16px in lists, 12px inside a domain chip.
          className="size-[72%] object-contain"
          onError={() =>
            setState({
              host,
              phase: phase === "first" ? "waiting" : "failed",
            })
          }
        />
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className={cn(
        "bg-muted text-muted-foreground flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-medium uppercase",
        className,
      )}
    >
      {host.charAt(0) || "·"}
    </span>
  );
}

/** “摘录”: the citation rests on what a search tool showed, not on the page
 * itself (`basis: "search excerpt"`). Absent for pages and records, and for
 * reports written before the field existed. */
export function BasisNote({ citation }: { citation: Citation }) {
  if (citation.basis !== "search excerpt") return null;
  return (
    <span
      data-citation-basis
      title="检索摘录，未读取原文"
      aria-label="检索摘录，未读取原文"
      className="shrink-0 rounded-full border border-(--dr-line) px-1.5 text-[11px] leading-4 text-(--dr-text-tertiary)"
    >
      摘录
    </span>
  );
}

/** Hover-card body for a citation, like ChatGPT's: site, title and the excerpt
 * it was cited for, each clamped to two lines. Several excerpts of one page
 * share a number; the hovered evidence opens first and the arrows reach the
 * rest. No URL, date or number: the sources panel has them. */
export function CitationPreview({
  citation,
  evidenceId,
}: {
  citation: Citation;
  evidenceId?: string;
}) {
  const excerpts = citation.excerpts?.length
    ? citation.excerpts
    : [{ evidence_id: citation.evidence_id, text: citation.snippet }];
  const hovered = Math.max(
    0,
    excerpts.findIndex((item) => item.evidence_id === evidenceId),
  );
  // The reader's own choice lasts until another evidence is hovered.
  const [picked, setPicked] = useState<{ from: string; index: number }>();
  const key = `${citation.evidence_id}:${evidenceId ?? ""}`;
  const index =
    picked?.from === key
      ? Math.min(picked.index, excerpts.length - 1)
      : hovered;
  // Two lines are all there is: do not spend them repeating the title.
  const text =
    sourceSummary(excerpts[index]?.text, citation.title, 360) ||
    excerptPreview(excerpts[index]?.text);
  const domain = citation.domain ?? hostOf(citation.url);
  const step = (delta: number) =>
    setPicked({
      from: key,
      index: (index + delta + excerpts.length) % excerpts.length,
    });
  return (
    <div data-citation-preview>
      {excerpts.length > 1 && (
        <div className="bg-muted text-muted-foreground flex h-8 items-center gap-1 px-2">
          <button
            type="button"
            aria-label="上一段摘录"
            className="hover:text-foreground flex size-6 items-center justify-center rounded-md"
            onClick={() => step(-1)}
          >
            <ArrowLeft className="size-4" />
          </button>
          <button
            type="button"
            aria-label="下一段摘录"
            className="hover:text-foreground flex size-6 items-center justify-center rounded-md"
            onClick={() => step(1)}
          >
            <ArrowRight className="size-4" />
          </button>
          <span
            aria-live="polite"
            className="ml-auto pr-1 text-xs tabular-nums"
          >
            {index + 1} / {excerpts.length}
          </span>
        </div>
      )}
      <div className="space-y-1.5 p-3">
        <div className="text-muted-foreground flex items-center gap-2 text-sm leading-5">
          <SiteIcon domain={domain} className="size-4" />
          <span className="min-w-0 truncate">{domain || "工具执行记录"}</span>
          <span className="ml-auto flex">
            <BasisNote citation={citation} />
          </span>
        </div>
        <p
          data-citation-title
          className="line-clamp-2 text-[15px] leading-5 font-semibold"
        >
          {sourceTitle(citation.title, citation.url)}
        </p>
        <p
          data-citation-excerpt
          className="text-muted-foreground line-clamp-2 text-sm leading-5 break-words"
        >
          {text || "没有可显示的片段。"}
        </p>
      </div>
    </div>
  );
}
