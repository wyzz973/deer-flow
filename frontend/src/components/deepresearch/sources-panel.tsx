"use client";

import {
  CircleAlert,
  CircleCheck,
  FileText,
  Globe,
  ListChecks,
  MessageSquarePlus,
  PenLine,
  ScrollText,
  Search,
  Wrench,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import {
  formatDuration,
  readableExcerpt,
  readLabel,
  sourceSummary,
  sourceTitle,
} from "@/core/deepresearch/presentation";
import type {
  ActivityItem,
  Report,
  ResearchActivity,
  ResearchSources,
} from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import { CitationPreview, SiteIcon } from "./citation-preview";

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
      const domain =
        citation.domain ??
        domainOf(
          citation.url,
          citation.origin === "runtime" ? "工具执行记录" : citation.publisher,
        );
      map.set(domain, [...(map.get(domain) ?? []), citation]);
    }
    return [...map];
  }, [report]);
  const cited = useMemo(
    () => new Set((report?.citations ?? []).map((item) => item.url)),
    [report],
  );
  const others = (data?.sources ?? []).filter(
    (source) => !cited.has(source.url),
  );
  useEffect(() => {
    if (selectedId)
      root.current
        ?.querySelector(`[data-source-id="${CSS.escape(selectedId)}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  return (
    <div ref={root} className="space-y-5 p-4 text-sm">
      <div className="text-muted-foreground text-xs">
        引用 · {report?.citations.length ?? 0}
      </div>
      {groups.map(([domain, citations]) => (
        <section key={domain} className="space-y-1">
          <h3 className="text-foreground/80 flex items-center gap-2 text-xs font-medium">
            <SiteIcon domain={domain} />
            {domain}
          </h3>
          {citations.map((citation) => {
            const selected =
              selectedId === citation.evidence_id ||
              Boolean(
                selectedId && citation.evidence_ids?.includes(selectedId),
              );
            const snippet = readableExcerpt(
              citation.excerpts?.find((item) => item.evidence_id === selectedId)
                ?.text ?? citation.snippet,
            );
            const summary = sourceSummary(
              citation.excerpts?.[0]?.text ?? citation.snippet,
              citation.title,
            );
            return (
              <div
                key={citation.evidence_id}
                data-source-id={selected ? selectedId : citation.evidence_id}
                className={cn(
                  "rounded-xl p-2 transition-colors",
                  selected ? "bg-muted" : "hover:bg-muted/50",
                )}
              >
                <div className="flex items-start gap-2">
                  <button
                    type="button"
                    aria-label={`回到正文引用 ${citation.number}`}
                    className="bg-muted text-muted-foreground hover:bg-foreground hover:text-background mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] tabular-nums"
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
                  <HoverCard openDelay={200} closeDelay={100}>
                    <HoverCardTrigger asChild>
                      <div className="min-w-0 flex-1">
                        <div className="line-clamp-2 text-[13px] leading-5 font-medium">
                          {sourceTitle(citation.title, citation.url)}
                        </div>
                        {summary && !selected && (
                          <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs leading-5">
                            {summary}
                          </p>
                        )}
                        {citation.url ? (
                          <a
                            href={citation.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-muted-foreground/80 mt-0.5 block truncate text-[11px] hover:underline"
                          >
                            {citation.published_at
                              ? `${citation.published_at.slice(0, 10)} · `
                              : ""}
                            {citation.url.replace(/^https?:\/\/(?:www\.)?/, "")}
                          </a>
                        ) : (
                          <p className="text-muted-foreground mt-0.5 text-xs">
                            工具执行记录 · 完整回执见 Trace
                          </p>
                        )}
                      </div>
                    </HoverCardTrigger>
                    <HoverCardContent
                      side="left"
                      align="start"
                      className="w-96 p-3"
                    >
                      <CitationPreview
                        citation={citation}
                        evidenceId={selected ? selectedId : undefined}
                      />
                    </HoverCardContent>
                  </HoverCard>
                </div>
                {selected && snippet && (
                  <div className="text-muted-foreground mt-3 ml-7 text-xs leading-5">
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
      {others.length > 0 && (
        <details className="border-border/60 space-y-3 border-t pt-4">
          <summary className="text-muted-foreground cursor-pointer text-xs">
            研究中接触的其他网页 · {others.length}
          </summary>
          <p className="text-muted-foreground text-[11px] leading-5">
            来自工具实际返回的链接；发现链接不等于读取过原文，未被报告引用。
          </p>
          {others.map((source) => (
            <a
              key={source.id}
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:bg-muted flex items-start gap-2 rounded-lg p-2"
            >
              <SiteIcon domain={source.domain} className="mt-0.5 size-4" />
              <span className="min-w-0">
                <span className="block truncate text-xs">
                  {sourceTitle(source.title, source.url)}
                </span>
                <span className="text-muted-foreground text-[11px]">
                  {source.domain}
                  {source.status === "read" ? " · 已读取" : " · 已发现"}
                </span>
              </span>
            </a>
          ))}
        </details>
      )}
    </div>
  );
}

function DomainChips({ domains }: { domains: string[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? domains : domains.slice(0, 4);
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {shown.map((domain) => (
        <span
          key={domain}
          className="bg-muted/60 text-muted-foreground inline-flex items-center gap-1 rounded-full py-0.5 pr-2 pl-0.5 text-[11px]"
        >
          <SiteIcon domain={domain} className="size-4" />
          {domain}
        </span>
      ))}
      {!expanded && domains.length > 4 && (
        <button
          type="button"
          className="bg-muted/60 text-muted-foreground hover:text-foreground rounded-full px-2 py-0.5 text-[11px]"
          onClick={() => setExpanded(true)}
        >
          再显示 {domains.length - 4} 个
        </button>
      )}
    </div>
  );
}

function Row({
  icon,
  title,
  children,
  tone,
}: {
  icon: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  tone?: "muted" | "error";
}) {
  return (
    <li className="relative pl-7">
      <span
        className={cn(
          "bg-background absolute top-0.5 left-0 flex size-5 items-center justify-center",
          tone === "error" ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {icon}
      </span>
      <div
        className={cn(
          "text-[13px] leading-5",
          tone === "muted" && "text-muted-foreground",
        )}
      >
        {title}
      </div>
      {children}
    </li>
  );
}

function ActivityRow({ item }: { item: ActivityItem }) {
  const icon = "size-3.5";
  switch (item.kind) {
    case "plan":
      return (
        <Row
          icon={<ListChecks className={icon} />}
          title={`制定研究计划：${item.title}`}
          tone="muted"
        />
      );
    case "step":
      return (
        <Row
          icon={<Search className={icon} />}
          title={`开始研究：${item.title}`}
        />
      );
    case "note":
      return (
        <Row
          icon={<span className="bg-foreground/70 size-1.5 rounded-full" />}
          title={item.text}
        />
      );
    case "search":
      return (
        <Row
          icon={<Globe className={icon} />}
          title={item.count > 1 ? `搜索 ${item.count} 次` : "搜索"}
        >
          {item.queries.length > 0 && (
            <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
              {item.queries.slice(0, 3).join(" · ")}
            </p>
          )}
          {item.domains.length > 0 && <DomainChips domains={item.domains} />}
        </Row>
      );
    case "read":
      return (
        <Row
          icon={<FileText className={icon} />}
          title={item.status === "error" ? "读取失败" : "阅读"}
          tone={item.status === "error" ? "error" : undefined}
        >
          <p className="text-muted-foreground mt-0.5 flex min-w-0 items-center gap-1.5 text-xs">
            {item.domain && (
              <SiteIcon domain={item.domain} className="size-4" />
            )}
            <span className="truncate">
              {readLabel(item.title, item.url, item.domain)}
            </span>
          </p>
        </Row>
      );
    case "tool":
      return (
        <Row
          icon={<Wrench className={icon} />}
          title={item.name}
          tone="muted"
        />
      );
    case "step_done":
      return (
        <Row
          icon={<CircleCheck className={icon} />}
          title={`完成：${item.title}`}
        >
          {item.summary && (
            <p className="text-muted-foreground mt-0.5 text-xs leading-5">
              {item.summary}
            </p>
          )}
        </Row>
      );
    case "step_failed":
      return (
        <Row
          icon={<CircleAlert className={icon} />}
          title={`未完成：${item.title}`}
          tone="error"
        >
          <p className="text-muted-foreground mt-0.5 text-xs leading-5">
            研究继续进行，报告会说明这部分可能不完整。
          </p>
        </Row>
      );
    case "gap":
      return (
        <Row
          icon={<Search className={icon} />}
          title={`还有 ${item.count} 处可以补充，继续检索`}
          tone="muted"
        />
      );
    case "limited":
      return (
        <Row
          icon={<CircleAlert className={icon} />}
          title="部分问题在公开资料中无法完全核实，将在报告中说明"
          tone="muted"
        />
      );
    case "update":
      return (
        <Row
          icon={<MessageSquarePlus className={icon} />}
          title={`收到调整：${item.text}`}
        />
      );
    case "writing":
      return <Row icon={<PenLine className={icon} />} title="开始撰写报告" />;
    case "outline":
      return (
        <Row icon={<PenLine className={icon} />} title="确定报告结构">
          <p className="text-muted-foreground mt-0.5 text-xs leading-5">
            {item.sections.join(" · ")}
          </p>
        </Row>
      );
    case "section":
      return (
        <Row
          icon={<PenLine className={icon} />}
          title={`完成章节：${item.title}`}
          tone="muted"
        />
      );
    case "done":
      return (
        <Row
          icon={<CircleCheck className={icon} />}
          title={`已生成报告：${item.title}`}
        />
      );
    case "failed":
      return (
        <Row
          icon={<CircleAlert className={icon} />}
          title={item.message ?? "研究失败"}
          tone="error"
        />
      );
    case "cancelled":
      return (
        <Row
          icon={<CircleAlert className={icon} />}
          title="研究已停止"
          tone="muted"
        />
      );
  }
}

export function ResearchActivityPanel({
  activity,
  onInspect,
}: {
  activity?: ResearchActivity;
  onInspect: (id?: string) => void;
}) {
  const items = activity?.items ?? [];
  const live = Boolean(activity) && !activity?.finished_at;
  // Follow live progress only while the end of the timeline is on screen, so
  // reading earlier steps is never interrupted by new activity.
  const end = useRef<HTMLDivElement>(null);
  const following = useRef(false);
  useEffect(() => {
    const node = end.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => {
      following.current = entry?.isIntersecting ?? false;
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (live && following.current) {
      end.current?.scrollIntoView({ block: "nearest" });
    }
  }, [items.length, live]);
  return (
    <div className="p-4">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-muted-foreground text-xs">研究活动</h3>
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
      <ol className="border-border/60 ml-2.5 space-y-4 border-l pl-0 [&>li]:-ml-2.5">
        {items.map((item, index) => (
          <ActivityRow key={`${item.kind}-${item.at}-${index}`} item={item} />
        ))}
      </ol>
      {activity?.finished_at && activity.status === "COMPLETED" && (
        <p className="text-muted-foreground mt-5 text-xs">
          用时 {formatDuration(activity.elapsed_seconds)} ·{" "}
          <span className="text-emerald-600 dark:text-emerald-400">完成</span>
        </p>
      )}
      {!items.length && (
        <p className="text-muted-foreground text-xs">
          研究开始后，搜索、阅读的网页和进展会显示在这里。
        </p>
      )}
      <div ref={end} aria-hidden className="h-px" />
    </div>
  );
}
