"use client";

import { useEffect, useState } from "react";

import { faviconUrl } from "@/core/deepresearch/api";
import { excerptPreview, sourceTitle } from "@/core/deepresearch/presentation";
import type { Citation } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

function hostOf(url?: string | null) {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, "") : "";
  } catch {
    return "";
  }
}

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
          className="size-3.5 object-contain"
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
      className={cn(
        "bg-muted text-muted-foreground flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-medium uppercase",
        className,
      )}
    >
      {host.charAt(0) || "·"}
    </span>
  );
}

/** Hover-card body for a citation: the source and the excerpt it was cited for.
 * Several excerpts of one page share a number; the hovered evidence wins. */
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
  const chosen =
    excerpts.find((item) => item.evidence_id === evidenceId) ?? excerpts[0];
  const text = excerptPreview(chosen?.text);
  const domain = citation.domain ?? hostOf(citation.url);
  return (
    <div className="space-y-2 text-xs">
      <div className="text-muted-foreground flex items-center gap-2">
        <SiteIcon domain={domain} />
        <span className="min-w-0 truncate">{domain || "工具执行记录"}</span>
        <span className="ml-auto tabular-nums">[{citation.number}]</span>
      </div>
      <p className="line-clamp-2 text-sm leading-5 font-medium">
        {sourceTitle(citation.title, citation.url)}
      </p>
      <div>
        <p className="text-muted-foreground mb-1 font-medium">
          {citation.provenance === "fetched_document"
            ? "原文片段"
            : "引用关联片段"}
        </p>
        {text ? (
          <blockquote className="border-border text-foreground/80 line-clamp-6 border-l-2 pl-3 leading-5">
            {text}
          </blockquote>
        ) : (
          <p className="text-muted-foreground">没有可显示的片段。</p>
        )}
      </div>
      {excerpts.length > 1 && (
        <p className="text-muted-foreground">
          本页另有 {excerpts.length - 1} 段引用片段
        </p>
      )}
      {citation.url && (
        <p className="text-muted-foreground truncate">
          {citation.published_at
            ? `${citation.published_at.slice(0, 10)} · `
            : ""}
          {citation.url}
        </p>
      )}
    </div>
  );
}
