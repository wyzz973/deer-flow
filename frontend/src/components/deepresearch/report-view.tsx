"use client";

import { Download, FileText, Maximize2 } from "lucide-react";
import { useMemo } from "react";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
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
import {
  citationId,
  reportMarkdown,
  reportSummaryLine,
  reportTitle,
} from "@/core/deepresearch/presentation";
import type { Report } from "@/core/deepresearch/types";

import { CitationPreview } from "./citation-preview";

type RemarkPlugins = Parameters<typeof MarkdownContent>[0]["remarkPlugins"];

// Research reports quote prices such as "$19" and "$39"; inline math parsing
// would turn the text between them into a formula. Reports never need LaTeX.
const REPORT_REMARK_PLUGINS = [
  [remarkGfm, { singleTilde: false }],
] as RemarkPlugins;

export function ResearchReportContent({
  report,
  onCitation,
}: {
  report: Report;
  onCitation: (id: string) => void;
}) {
  const content = useMemo(() => reportMarkdown(report), [report]);
  const byNumber = useMemo(
    () => new Map(report.citations.map((item) => [item.number, item])),
    [report.citations],
  );
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
          const source = byNumber.get(report.citation_map[id]!);
          return (
            <HoverCard openDelay={150} closeDelay={80}>
              <HoverCardTrigger asChild>
                <button
                  type="button"
                  data-evidence-id={id}
                  aria-label={`查看引用 ${report.citation_map[id]}`}
                  className="bg-muted text-muted-foreground hover:bg-foreground hover:text-background mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 align-super text-[10px] leading-none tabular-nums"
                  onClick={() => onCitation(id)}
                >
                  {children}
                </button>
              </HoverCardTrigger>
              {source && (
                <HoverCardContent align="start" className="w-96 p-3">
                  <CitationPreview citation={source} evidenceId={id} />
                </HoverCardContent>
              )}
            </HoverCard>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    }),
    [byNumber, onCitation, report.citation_map],
  );
  return (
    <div className="research-report text-[15px] leading-7 [&_h1]:text-2xl [&_h1]:leading-snug [&_h2]:mt-10 [&_h2]:scroll-mt-20 [&_h2]:text-xl [&_h3]:scroll-mt-20 [&_h3]:text-base [&_p]:leading-7 [&_table]:text-sm">
      <MarkdownContent
        content={content}
        isLoading={false}
        remarkPlugins={REPORT_REMARK_PLUGINS}
        components={components}
      />
    </div>
  );
}

export function ResearchReportActions({
  onDownload,
  onExpand,
}: {
  onDownload: (format: "md" | "html" | "docx") => void;
  onExpand?: () => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="导出报告">
            <Download className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {(["docx", "md", "html"] as const).map((format) => (
            <DropdownMenuItem key={format} onClick={() => onDownload(format)}>
              导出为 {format === "docx" ? "Word" : format.toUpperCase()}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {onExpand && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="全屏阅读报告"
          onClick={onExpand}
        >
          <Maximize2 className="size-4" />
        </Button>
      )}
    </div>
  );
}

export function ResearchReportCard({
  report,
  onExpand,
  onCitation,
  onDownload,
}: {
  report: Report;
  onExpand: () => void;
  onCitation: (id: string) => void;
  onDownload: (format: "md" | "html" | "docx") => void;
}) {
  return (
    <section aria-label="研究报告预览" className="space-y-2">
      <p className="text-muted-foreground text-xs">
        {reportSummaryLine(report)}
      </p>
      <div className="border-border/60 bg-muted/10 overflow-hidden rounded-2xl border">
        <div className="border-border/40 flex items-center justify-between gap-2 border-b px-4 py-2">
          <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
            <span className="bg-primary text-primary-foreground flex size-5 shrink-0 items-center justify-center rounded-md">
              <FileText className="size-3.5" />
            </span>
            <span className="truncate">{reportTitle(report)}</span>
          </span>
          <ResearchReportActions onDownload={onDownload} onExpand={onExpand} />
        </div>
        <div className="relative max-h-80 overflow-hidden px-6 pt-4 pb-12">
          <ResearchReportContent report={report} onCitation={onCitation} />
          <div className="from-background pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-linear-to-t to-transparent" />
        </div>
        <Button
          variant="ghost"
          className="w-full rounded-none text-xs"
          onClick={onExpand}
        >
          阅读全文
        </Button>
      </div>
    </section>
  );
}
