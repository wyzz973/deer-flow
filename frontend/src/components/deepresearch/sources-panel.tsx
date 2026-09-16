"use client";

import { ExternalLink, Globe, ScrollText } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import { Button } from "@/components/ui/button";
import type { Report, ResearchSources } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

function domainOf(url: string | null | undefined, fallback: string) {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, "") : fallback;
  } catch {
    return fallback;
  }
}

export function ResearchSourcesPanel({
  report,
  data,
  selectedId,
  onReference,
}: {
  report?: Report | null;
  data?: ResearchSources;
  selectedId?: string;
  onReference: (id: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const groups = useMemo(() => {
    const map = new Map<string, NonNullable<Report["citations"]>>();
    for (const citation of report?.citations ?? []) {
      const domain = domainOf(
        citation.url,
        citation.origin === "runtime" ? "原生工具" : citation.publisher,
      );
      map.set(domain, [...(map.get(domain) ?? []), citation]);
    }
    return [...map];
  }, [report]);
  useEffect(() => {
    if (selectedId)
      root.current
        ?.querySelector(`[data-source-id="${CSS.escape(selectedId)}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  return (
    <div ref={root} className="space-y-6 p-4 text-sm">
      <div className="text-muted-foreground text-xs">
        引用 · {report?.citations.length ?? 0}
      </div>
      {groups.map(([domain, citations]) => (
        <section key={domain} className="space-y-2">
          <h3 className="text-muted-foreground flex items-center gap-2 text-xs">
            <Globe className="size-3" />
            {domain}
          </h3>
          {citations.map((citation) => {
            const selected =
              selectedId === citation.evidence_id ||
              Boolean(
                selectedId && citation.evidence_ids?.includes(selectedId),
              );
            const snippet =
              citation.excerpts?.find((item) => item.evidence_id === selectedId)
                ?.text ?? citation.snippet;
            return (
              <div
                key={citation.evidence_id}
                data-source-id={selected ? selectedId : citation.evidence_id}
                className={cn(
                  "group rounded-xl p-2 transition-colors",
                  selected ? "bg-muted" : "hover:bg-muted/50",
                )}
              >
                <div className="flex items-start gap-2">
                  <button
                    type="button"
                    aria-label={`回到正文引用 ${citation.number}`}
                    className="bg-muted text-muted-foreground mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[10px]"
                    onClick={() =>
                      onReference(
                        selectedId && selected
                          ? selectedId
                          : citation.evidence_id,
                      )
                    }
                  >
                    {citation.number}
                  </button>
                  <div className="min-w-0 flex-1">
                    <div className="line-clamp-2 text-xs leading-5 font-medium">
                      {citation.title}
                    </div>
                    {citation.url ? (
                      <a
                        href={citation.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-muted-foreground mt-1 block truncate text-xs hover:underline"
                      >
                        {citation.url}
                      </a>
                    ) : (
                      <p className="text-muted-foreground mt-1 text-xs">
                        工具执行记录 · 完整回执见 Trace
                      </p>
                    )}
                  </div>
                </div>
                {selected && (
                  <div className="text-muted-foreground mt-3 text-xs leading-5">
                    <p className="mb-1 font-medium">
                      {citation.provenance === "fetched_document"
                        ? "已读取的原文片段"
                        : "引用关联片段"}
                    </p>
                    <p className="whitespace-pre-wrap">
                      {snippet.slice(0, 900)}
                    </p>
                    {snippet.length > 900 && (
                      <details className="mt-2">
                        <summary className="cursor-pointer">展开更多</summary>
                        <p className="mt-2 whitespace-pre-wrap">
                          {snippet.slice(900, 2000)}
                        </p>
                      </details>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      ))}
      {!groups.length && (
        <p className="text-muted-foreground text-xs">
          报告生成后，引用会在这里按来源分组。
        </p>
      )}
      <details className="border-border/60 space-y-3 border-t pt-4">
        <summary className="text-muted-foreground cursor-pointer text-xs">
          已发现的来源 · {data?.sources.length ?? 0}
        </summary>
        <p className="text-muted-foreground text-[11px] leading-5">
          来自工具实际返回的链接；发现链接不等于已验证原文。
        </p>
        {data?.sources.map((source) => (
          <a
            key={source.id}
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:bg-muted flex items-start gap-2 rounded-lg p-2"
          >
            <Globe className="text-muted-foreground mt-1 size-3 shrink-0" />
            <span className="min-w-0">
              <span className="block truncate text-xs">{source.title}</span>
              <span className="text-muted-foreground text-[11px]">
                {source.domain}
                {source.status === "read" ? " · 已读取" : " · 已发现"}
              </span>
            </span>
            <ExternalLink className="text-muted-foreground mt-1 ml-auto size-3 shrink-0" />
          </a>
        ))}
      </details>
    </div>
  );
}

export function ResearchActivityPanel({
  data,
  onInspect,
}: {
  data?: ResearchSources;
  onInspect: (id?: string) => void;
}) {
  return (
    <div className="p-4">
      <div className="mb-5 flex items-center justify-between">
        <h3 className="text-muted-foreground text-xs">
          研究活动 · {data?.calls.length ?? 0} 次工具调用
        </h3>
        <Button
          variant="ghost"
          size="sm"
          className="text-xs"
          onClick={() => onInspect()}
        >
          <ScrollText className="size-3" />
          Trace
        </Button>
      </div>
      <ol className="border-border/60 ml-1 space-y-5 border-l pl-4">
        {data?.calls.map((call) => (
          <li key={call.id} className="relative">
            <span
              className={cn(
                "bg-background border-muted-foreground absolute top-1 -left-[21px] size-2 rounded-full border",
                call.status === "error" && "border-destructive",
                call.status === "running" &&
                  "bg-foreground/50 animate-pulse motion-reduce:animate-none",
              )}
            />
            <button
              type="button"
              className="text-left text-xs leading-5 hover:underline"
              onClick={() => onInspect(call.id)}
            >
              {call.tool_name}
            </button>
            <div className="text-muted-foreground text-[11px]">
              {call.agent_name ?? call.unit_id} ·{" "}
              {call.status === "running"
                ? "进行中"
                : call.status === "error"
                  ? "失败"
                  : "完成"}
              {call.duration_ms != null
                ? ` · ${(call.duration_ms / 1000).toFixed(1)}s`
                : ""}
            </div>
            {Boolean(call.domains?.length) && (
              <div className="mt-2 flex flex-wrap gap-1">
                {call.domains?.map((domain) => (
                  <span
                    key={domain}
                    className="bg-muted/50 text-muted-foreground inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px]"
                  >
                    <Globe className="size-2.5" />
                    {domain}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ol>
      {!data?.calls.length && (
        <p className="text-muted-foreground text-xs">
          研究开始后，实际调用及涉及的来源会显示在这里。
        </p>
      )}
    </div>
  );
}
