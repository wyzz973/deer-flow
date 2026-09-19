"use client";

import { Globe, ScrollText } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import {
  formatDuration,
  pageKey,
  readLabel,
  sourceSummary,
  sourceTitle,
} from "@/core/deepresearch/presentation";
import type {
  ActivityItem,
  DiscoveredSource,
  Report,
  ResearchActivity,
  ResearchSources,
} from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import {
  BasisNote,
  CITATION_CARD_CLASS,
  CitationPreview,
  SiteIcon,
} from "./citation-preview";

function domainOf(url: string | null | undefined, fallback: string) {
  try {
    return url ? new URL(url).hostname.replace(/^www\./, "") : fallback;
  } catch {
    return fallback;
  }
}

/** Scanned pages laid out at a time. */
const SCANNED_PAGE = 200;

function withoutScheme(url: string) {
  return url.replace(/^https?:\/\/(?:www\.)?/, "");
}

function DomainHeading({ domain }: { domain: string }) {
  return (
    <h3 className="text-muted-foreground flex items-center gap-2 px-2 text-[13px] leading-5">
      <SiteIcon domain={domain} className="size-4" />
      <span className="min-w-0 truncate">{domain}</span>
    </h3>
  );
}

const entryTitle = "block truncate text-[15px] leading-5 font-semibold";
const entryLine =
  "text-muted-foreground mt-0.5 line-clamp-2 text-sm leading-5 break-words";

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
  // The same page shows up as http/https, with or without "www." or a
  // trailing slash. Compare pages the way the server numbers citations.
  const others = useMemo(() => {
    const seen = new Set(
      (report?.citations ?? []).flatMap((item) =>
        [item.url, item.canonical_url].filter(Boolean).map(pageKey),
      ),
    );
    // A read page outranks the search hit that discovered it.
    const ranked = [...(data?.sources ?? [])].sort(
      (a, b) => Number(b.status === "read") - Number(a.status === "read"),
    );
    const kept = new Set<string>();
    for (const source of ranked) {
      const key = pageKey(source.url);
      if (key && seen.has(key)) continue;
      seen.add(key);
      kept.add(source.id);
    }
    return (data?.sources ?? []).filter((source) => kept.has(source.id));
  }, [data, report]);
  useEffect(() => {
    if (selectedId)
      root.current
        ?.querySelector(`[data-source-id="${CSS.escape(selectedId)}"]`)
        ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);
  // Pages the tools returned but the report does not cite, grouped like the
  // citations above them. A long study scans thousands of pages: the list
  // grows on request instead of laying them all out at once.
  const [scannedLimit, setScannedLimit] = useState(SCANNED_PAGE);
  const scanned = useMemo(() => {
    const map = new Map<string, DiscoveredSource[]>();
    for (const source of others) {
      const group = map.get(source.domain);
      if (group) group.push(source);
      else map.set(source.domain, [source]);
    }
    const shown: [string, DiscoveredSource[]][] = [];
    let left = scannedLimit;
    for (const [domain, sources] of map) {
      if (left <= 0) break;
      shown.push([domain, sources.slice(0, left)]);
      left -= sources.length;
    }
    return shown;
  }, [others, scannedLimit]);
  const scannedRest = Math.max(0, others.length - scannedLimit);
  return (
    <div ref={root} className="space-y-4 px-2 py-4">
      <div className="text-muted-foreground px-2 text-sm">
        引用 · {report?.citations.length ?? 0}
      </div>
      {groups.map(([domain, citations]) => (
        <section key={domain} className="space-y-1">
          <DomainHeading domain={domain} />
          {citations.map((citation) => {
            const selected =
              selectedId === citation.evidence_id ||
              Boolean(
                selectedId && citation.evidence_ids?.includes(selectedId),
              );
            // The excerpt behind the selected alias, else the page's first.
            // Always words clamped to two lines: the raw fetched text (fences,
            // tables, link syntax) once filled the whole panel.
            const summary = sourceSummary(
              (selected
                ? citation.excerpts?.find(
                    (item) => item.evidence_id === selectedId,
                  )
                : undefined
              )?.text ??
                citation.excerpts?.[0]?.text ??
                citation.snippet,
              citation.title,
            );
            const title = sourceTitle(citation.title, citation.url);
            return (
              <div
                key={citation.evidence_id}
                data-source-id={selected ? selectedId : citation.evidence_id}
                data-selected={selected || undefined}
                className={cn(
                  "flex items-start gap-2 rounded-[16px] p-2 transition-colors motion-reduce:transition-none",
                  selected ? "bg-(--dr-chip)" : "hover:bg-(--dr-chip)/60",
                )}
              >
                <button
                  type="button"
                  aria-label={`回到正文引用 ${citation.number}`}
                  className="text-muted-foreground hover:bg-foreground hover:text-background mt-px flex size-[18px] shrink-0 items-center justify-center rounded-full bg-(--dr-track) text-[10px] leading-none font-medium tabular-nums"
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
                      <div className="flex items-center gap-2">
                        {citation.url ? (
                          <a
                            href={citation.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={cn(entryTitle, "hover:underline")}
                          >
                            {title}
                          </a>
                        ) : (
                          <span className={entryTitle}>{title}</span>
                        )}
                        <BasisNote citation={citation} />
                      </div>
                      {summary ? (
                        <p data-source-excerpt className={entryLine}>
                          {summary}
                        </p>
                      ) : (
                        <p className={cn(entryLine, "line-clamp-1")}>
                          {citation.url
                            ? withoutScheme(citation.url)
                            : "工具执行记录 · 完整回执见 Trace"}
                        </p>
                      )}
                    </div>
                  </HoverCardTrigger>
                  <HoverCardContent
                    side="left"
                    align="start"
                    className={CITATION_CARD_CLASS}
                  >
                    <CitationPreview
                      citation={citation}
                      evidenceId={selected ? selectedId : undefined}
                    />
                  </HoverCardContent>
                </HoverCard>
              </div>
            );
          })}
        </section>
      ))}
      {!groups.length && (
        <p className="text-muted-foreground px-2 text-sm">
          报告生成后，引用会在这里按来源分组。
        </p>
      )}
      {scanned.length > 0 && (
        <section
          aria-label="已扫描的来源"
          className="space-y-4 border-t border-(--dr-line-soft) pt-4"
        >
          <div className="px-2">
            <h3 className="text-muted-foreground text-sm">
              已扫描的来源 · {others.length}
            </h3>
            <p className="mt-1 text-xs leading-5 text-(--dr-text-tertiary)">
              来自工具实际返回的链接；发现链接不等于读取过原文，未被报告引用。
            </p>
          </div>
          {scanned.map(([domain, sources]) => (
            <div key={domain} className="space-y-1">
              <DomainHeading domain={domain} />
              {sources.map((source) => {
                const summary = sourceSummary(source.excerpt, source.title);
                return (
                  <a
                    key={source.id}
                    href={source.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block rounded-[16px] p-2 transition-colors hover:bg-(--dr-chip)/60 motion-reduce:transition-none"
                  >
                    <span className="flex items-center gap-2">
                      <span className={entryTitle}>
                        {sourceTitle(source.title, source.url)}
                      </span>
                      {source.status === "read" && (
                        <span className="shrink-0 text-xs text-(--dr-text-tertiary)">
                          已读取
                        </span>
                      )}
                    </span>
                    <span className={cn(entryLine, "block")}>
                      {summary || withoutScheme(source.url)}
                    </span>
                  </a>
                );
              })}
            </div>
          ))}
          {scannedRest > 0 && (
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground mx-2 text-sm underline-offset-2 hover:underline"
              onClick={() => setScannedLimit(scannedLimit + SCANNED_PAGE)}
            >
              再显示 {Math.min(SCANNED_PAGE, scannedRest)} 个
            </button>
          )}
        </section>
      )}
    </div>
  );
}

type Chip = { domain: string; label?: string; href?: string | null };

const chipClass =
  "text-muted-foreground inline-flex h-5 max-w-full items-center gap-2 rounded-full bg-(--dr-chip) px-2 text-[13px] leading-5";

/** Sites as small pills: four at first, the rest on request, in place. */
function DomainChips({ chips }: { chips: Chip[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? chips : chips.slice(0, 4);
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {shown.map((chip, index) => {
        const body = (
          <>
            <SiteIcon domain={chip.domain} className="size-3 text-[8px]" />
            <span className="max-w-[14rem] truncate">
              {chip.label ?? chip.domain}
            </span>
          </>
        );
        return chip.href ? (
          <a
            key={`${chip.domain}-${index}`}
            href={chip.href}
            target="_blank"
            rel="noopener noreferrer"
            title={chip.label ? `${chip.label} · ${chip.domain}` : undefined}
            className={cn(chipClass, "hover:bg-(--dr-track)")}
          >
            {body}
          </a>
        ) : (
          <span key={`${chip.domain}-${index}`} className={chipClass}>
            {body}
          </span>
        );
      })}
      {!expanded && chips.length > 4 && (
        <button
          type="button"
          className={cn(chipClass, "hover:bg-(--dr-track)")}
          onClick={() => setExpanded(true)}
        >
          再显示 {chips.length - 4} 个
        </button>
      )}
    </div>
  );
}

/** The timeline speaks two dialects only, like ChatGPT's: a dot for progress
 * (a title, a grey paragraph, a hairline down to the next entry) and a globe
 * for the web (searching or reading, with site pills). */
function Row({
  web,
  title,
  children,
  tone,
  last,
}: {
  web?: boolean;
  title: ReactNode;
  children?: ReactNode;
  tone?: "muted" | "error";
  last?: boolean;
}) {
  return (
    <li data-activity-row={web ? "web" : "note"} className="relative pl-6">
      <span className="absolute top-0 left-0 flex h-5 w-3.5 items-center justify-center">
        {web ? (
          <Globe
            className={cn(
              "size-3.5",
              tone === "error" ? "text-destructive" : "text-muted-foreground",
            )}
          />
        ) : (
          <span
            className={cn(
              "size-1.5 rounded-full",
              tone === "error" ? "bg-destructive" : "bg-foreground",
            )}
          />
        )}
      </span>
      {!web && !last && (
        <span
          aria-hidden
          className="absolute top-5 -bottom-4 left-[6.5px] w-px bg-(--dr-line)"
        />
      )}
      <div
        className={cn(
          "text-sm leading-5",
          tone === "muted" && "text-muted-foreground",
          tone === "error" && "text-destructive",
        )}
      >
        {title}
      </div>
      {children}
    </li>
  );
}

const detail = "text-muted-foreground mt-1 text-sm leading-5";

type ReadItem = Extract<ActivityItem, { kind: "read" }>;
type TimelineRow =
  | { kind: "item"; item: Exclude<ActivityItem, ReadItem> }
  | { kind: "reads"; failed: boolean; items: ReadItem[] };

/** Consecutive page reads fold into one entry with a pill per page. */
function timelineRows(items: ActivityItem[]): TimelineRow[] {
  const rows: TimelineRow[] = [];
  for (const item of items) {
    if (item.kind !== "read") {
      rows.push({ kind: "item", item });
      continue;
    }
    const failed = item.status === "error";
    const previous = rows.at(-1);
    if (previous?.kind === "reads" && previous.failed === failed)
      previous.items.push(item);
    else rows.push({ kind: "reads", failed, items: [item] });
  }
  return rows;
}

function ActivityRow({
  row,
  active,
  last,
}: {
  row: TimelineRow;
  /** The newest entry of research that is still running. */
  active: boolean;
  last: boolean;
}) {
  if (row.kind === "reads") {
    const count = row.items.length;
    return (
      <Row
        web
        tone={row.failed ? "error" : undefined}
        title={
          row.failed
            ? `读取失败 · ${count} 个网页`
            : active
              ? "正在阅读"
              : `已阅读 ${count} 个网页`
        }
      >
        <DomainChips
          chips={row.items.map((item) => ({
            domain: item.domain,
            label: readLabel(item.title, item.url, item.domain),
            href: item.url,
          }))}
        />
      </Row>
    );
  }
  const item = row.item;
  switch (item.kind) {
    case "plan":
      return (
        <Row last={last} tone="muted" title={`制定研究计划：${item.title}`} />
      );
    case "step":
      return <Row last={last} title={`开始研究：${item.title}`} />;
    case "note":
      return <Row last={last} title={item.text} />;
    case "search":
      return (
        <Row
          web
          title={
            active
              ? "正在搜索"
              : item.domains.length
                ? `已搜索 ${item.domains.length} 个网站`
                : item.count > 1
                  ? `已搜索 ${item.count} 次`
                  : "已搜索"
          }
        >
          {item.queries.length > 0 && (
            <p className={cn(detail, "line-clamp-2")}>
              {item.queries.slice(0, 3).join(" · ")}
            </p>
          )}
          {item.domains.length > 0 && (
            <DomainChips chips={item.domains.map((domain) => ({ domain }))} />
          )}
        </Row>
      );
    case "tool":
      return <Row last={last} tone="muted" title={item.name} />;
    case "step_done":
      return (
        <Row last={last} title={`完成：${item.title}`}>
          {item.summary && <p className={detail}>{item.summary}</p>}
        </Row>
      );
    case "step_failed":
      return (
        <Row last={last} tone="error" title={`未完成：${item.title}`}>
          <p className={detail}>研究继续进行，报告会说明这部分可能不完整。</p>
        </Row>
      );
    case "gap":
      return (
        <Row
          last={last}
          tone="muted"
          title={`还有 ${item.count} 处可以补充，继续检索`}
        />
      );
    case "limited":
      return (
        <Row
          last={last}
          tone="muted"
          title="部分问题在公开资料中无法完全核实，将在报告中说明"
        />
      );
    case "update":
      return <Row last={last} title={`收到调整：${item.text}`} />;
    case "writing":
      return <Row last={last} title="开始撰写报告" />;
    case "outline":
      return (
        <Row last={last} title="确定报告结构">
          <p className={detail}>{item.sections.join(" · ")}</p>
        </Row>
      );
    case "section":
      return <Row last={last} tone="muted" title={`完成章节：${item.title}`} />;
    case "done":
      return <Row last={last} title={`已生成报告：${item.title}`} />;
    case "failed":
      return (
        <Row last={last} tone="error" title={item.message ?? "研究失败"} />
      );
    case "cancelled":
      return <Row last={last} tone="muted" title="研究已停止" />;
  }
}

export function ResearchActivityPanel({
  activity,
  title,
  onInspect,
}: {
  activity?: ResearchActivity;
  /** The plan title, shown as the panel heading like ChatGPT's. */
  title?: string;
  onInspect: (id?: string) => void;
}) {
  const rows = useMemo(
    () => timelineRows(activity?.items ?? []),
    [activity?.items],
  );
  const itemCount = activity?.items.length ?? 0;
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
  }, [itemCount, live]);
  return (
    <section aria-label="研究活动" className="p-4">
      <div className="mb-4 flex items-start justify-between gap-3">
        <h3 className="min-w-0 text-lg leading-6 font-normal break-words">
          {title ?? "研究活动"}
        </h3>
        <button
          type="button"
          className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring flex h-7 shrink-0 items-center gap-1 rounded-lg px-2 text-xs focus-visible:ring-2 focus-visible:outline-none"
          onClick={() => onInspect()}
        >
          <ScrollText className="size-3" />
          Trace
        </button>
      </div>
      <ol className="space-y-4">
        {rows.map((row, index) => {
          const first = row.kind === "reads" ? row.items[0]! : row.item;
          return (
            <ActivityRow
              key={`${first.kind}-${first.at}-${index}`}
              row={row}
              active={live && index === rows.length - 1}
              last={index === rows.length - 1}
            />
          );
        })}
      </ol>
      {activity?.finished_at && activity.status === "COMPLETED" && (
        <p className="text-muted-foreground mt-5 text-sm leading-5">
          用时 {formatDuration(activity.elapsed_seconds)} ·{" "}
          <span className="text-emerald-600 dark:text-emerald-400">已完成</span>
        </p>
      )}
      {!itemCount && (
        <p className="text-muted-foreground text-sm leading-5">
          研究开始后，搜索、阅读的网页和进展会显示在这里。
        </p>
      )}
      <div ref={end} aria-hidden className="h-px" />
    </section>
  );
}
