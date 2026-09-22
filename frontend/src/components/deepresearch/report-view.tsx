"use client";

import { Download, FileText, Maximize2 } from "lucide-react";
import { createContext, useContext, useMemo, useRef } from "react";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import { writeTextToClipboard } from "@/core/clipboard";
import {
  citationId,
  reportMarkdown,
  reportSummaryLine,
  reportTitle,
} from "@/core/deepresearch/presentation";
import type { Citation, Report } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import { CITATION_CARD_CLASS, CitationPreview } from "./citation-preview";

type RemarkPlugins = Parameters<typeof MarkdownContent>[0]["remarkPlugins"];

// Research reports quote prices such as "$19" and "$39"; inline math parsing
// would turn the text between them into a formula. Reports never need LaTeX.
const REPORT_REMARK_PLUGINS = [
  [remarkGfm, { singleTilde: false }],
] as RemarkPlugins;

// The citation the reader clicked. It travels by context so the Markdown
// `components` map (and with it every citation button) stays the same element
// type when the selection changes.
const SelectedCitation = createContext<string | undefined>(undefined);

function CitationMark({
  id,
  number,
  source,
  onCite,
  children,
}: {
  id: string;
  number: number;
  source?: Citation;
  onCite: (id: string) => void;
  children?: React.ReactNode;
}) {
  const selected = useContext(SelectedCitation) === id;
  return (
    <HoverCard openDelay={100} closeDelay={80}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          data-evidence-id={id}
          aria-label={`查看引用 ${number}`}
          aria-pressed={selected}
          className={cn(
            "mx-0.5 inline-flex h-3.5 min-w-3.5 items-center justify-center rounded-full px-[3px] align-super text-[9px] leading-none font-medium tabular-nums transition-colors motion-reduce:transition-none",
            selected
              ? "bg-(--dr-selected) text-(--dr-selected-foreground)"
              : "text-muted-foreground hover:bg-foreground hover:text-background bg-(--dr-track)",
          )}
          onClick={() => onCite(id)}
        >
          {children}
        </button>
      </HoverCardTrigger>
      {source && (
        <HoverCardContent align="start" className={CITATION_CARD_CLASS}>
          <CitationPreview citation={source} evidenceId={id} />
        </HoverCardContent>
      )}
    </HoverCard>
  );
}

// The preview card sets everything one size smaller than the reader.
const REPORT_TYPE = {
  reader:
    "text-base leading-6 [&_h1]:text-[28px] [&_h1]:leading-[33px] [&_h2]:mt-9 [&_h2]:scroll-mt-20 [&_h2]:text-2xl [&_h2]:leading-8 [&_h3]:mt-6 [&_h3]:scroll-mt-20 [&_h3]:text-xl [&_h3]:leading-7 [&_li]:leading-6 [&_p]:leading-6",
  preview:
    "text-sm leading-[22px] [--research-table-size:13px] [&_h1]:text-2xl [&_h1]:leading-[31px] [&_h2]:mt-7 [&_h2]:text-xl [&_h2]:leading-7 [&_h3]:mt-5 [&_h3]:text-base [&_h3]:leading-6 [&_li]:leading-[22px] [&_p]:leading-[22px]",
} as const;

export function ResearchReportContent({
  report,
  onCitation,
  selectedId,
  size = "reader",
}: {
  report: Report;
  onCitation: (id: string) => void;
  /** Evidence ID of the citation shown in the sources panel. */
  selectedId?: string;
  size?: keyof typeof REPORT_TYPE;
}) {
  const content = useMemo(() => reportMarkdown(report), [report]);
  const byNumber = useMemo(
    () => new Map(report.citations.map((item) => [item.number, item])),
    [report.citations],
  );
  // Callers pass inline handlers. A new `components` map is a new element
  // type for every link: each parent render would remount all citation
  // buttons, dropping keyboard focus and any open preview.
  const cite = useRef(onCitation);
  cite.current = onCitation;
  const components = useMemo(
    () => ({
      a: ({
        href,
        children,
      }: {
        href?: string;
        children?: React.ReactNode;
      }) => {
        const id = citationId(href, report.citation_map);
        if (id) {
          const number = report.citation_map[id]!;
          return (
            <CitationMark
              id={id}
              number={number}
              source={byNumber.get(number)}
              onCite={(target) => cite.current(target)}
            >
              {children}
            </CitationMark>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
      // Report text derives from fetched pages. Loading an image it names,
      // including protocol-relative `//host/p.png?d=…`, would let injected
      // text send data to that host. Reports carry no images: keep the words.
      img: ({ alt }: { alt?: string }) =>
        alt?.trim() ? <span data-report-image-alt>{alt}</span> : null,
    }),
    [byNumber, report.citation_map],
  );
  return (
    <SelectedCitation.Provider value={selectedId}>
      <div className={cn("research-report", REPORT_TYPE[size])}>
        <MarkdownContent
          content={content}
          isLoading={false}
          remarkPlugins={REPORT_REMARK_PLUGINS}
          components={components}
        />
      </div>
    </SelectedCitation.Provider>
  );
}

const iconButton =
  "text-foreground hover:bg-accent focus-visible:ring-ring flex size-9 items-center justify-center rounded-lg transition-colors focus-visible:ring-2 focus-visible:outline-none motion-reduce:transition-none";

export function ResearchReportActions({
  report,
  onDownload,
  onExpand,
}: {
  report: Report;
  onDownload: (format: "md" | "html" | "docx", report: Report) => void;
  onExpand?: () => void;
}) {
  return (
    <div className="flex items-center">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" aria-label="导出报告" className={iconButton}>
            <Download className="size-5" strokeWidth={1.75} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="deepresearch-surface research-fade min-w-[193px] rounded-[16px] border-(--dr-line) p-1.5"
        >
          <DropdownMenuItem
            className="h-9 rounded-lg px-3 text-sm"
            onClick={() => {
              // The exportable Markdown (with its reference list), not the
              // display form whose citations are in-page anchors.
              void writeTextToClipboard(
                report.markdown || reportMarkdown(report),
              ).then((ok) =>
                ok ? toast.success("已复制报告内容") : toast.error("复制失败"),
              );
            }}
          >
            复制内容
          </DropdownMenuItem>
          {(["md", "docx", "html"] as const).map((format) => (
            <DropdownMenuItem
              key={format}
              className="h-9 rounded-lg px-3 text-sm"
              onClick={() => onDownload(format, report)}
            >
              导出到{" "}
              {format === "docx"
                ? "Word"
                : format === "md"
                  ? "Markdown"
                  : "HTML"}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {onExpand && (
        <button
          type="button"
          aria-label="全屏阅读报告"
          className={iconButton}
          onClick={onExpand}
        >
          <Maximize2 className="size-5" strokeWidth={1.75} />
        </button>
      )}
    </div>
  );
}

export function ResearchReportCard({
  report,
  selectedId,
  onExpand,
  onCitation,
  onDownload,
}: {
  report: Report;
  selectedId?: string;
  onExpand: () => void;
  onCitation: (id: string) => void;
  onDownload: (format: "md" | "html" | "docx", report: Report) => void;
}) {
  const title = reportTitle(report);
  return (
    <section aria-label="研究报告预览" className="space-y-3">
      <p className="text-muted-foreground text-sm leading-5">
        {reportSummaryLine(report)}
      </p>
      <div className="bg-card overflow-hidden rounded-[16px] border border-(--dr-line)">
        <div className="flex h-12 items-center justify-between gap-2 pr-1.5 pl-3">
          <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-(--dr-accent) text-white">
              <FileText className="size-4" />
            </span>
            <span className="truncate">{title}</span>
          </span>
          <ResearchReportActions
            report={report}
            onDownload={onDownload}
            onExpand={onExpand}
          />
        </div>
        <div className="relative">
          {/* The preview scrolls inside the card; full-screen reading opens
              from the icon above. Focusable, so the keyboard can scroll it. */}
          <div
            role="region"
            aria-label={`报告预览：${title}`}
            tabIndex={0}
            data-research-report-preview
            className="focus-visible:ring-ring max-h-[400px] overflow-y-auto px-6 pt-2 pb-16 focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
          >
            <ResearchReportContent
              report={report}
              size="preview"
              selectedId={selectedId}
              onCitation={onCitation}
            />
          </div>
          <div
            aria-hidden
            className="from-card pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-linear-to-t to-transparent"
          />
        </div>
      </div>
    </section>
  );
}
