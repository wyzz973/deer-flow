"use client";

import { Download, FileText, Maximize2 } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import {
  citationId,
  escapeReportText,
  uniqueCitationIds,
} from "@/core/deepresearch/presentation";
import type { Report, Segment } from "@/core/deepresearch/types";

function reportMarkdown(report: Report) {
  const render = (segments: Segment[]) =>
    segments
      .map(
        (segment) =>
          escapeReportText(segment.text) +
          uniqueCitationIds(segment.evidence_ids, report.citation_map)
            .map((id) => `[${report.citation_map[id]}](#citation-${id})`)
            .join(""),
      )
      .join("\n\n");
  const table = report.report.comparison_table;
  const cell = (value: string) =>
    value.replace(/\|/g, "\\|").replace(/\n/g, " ");
  const comparison = table
    ? `## 快速对比\n\n| 维度 | ${table.headers.map(cell).join(" | ")} |\n| ${table.headers
        .map(() => "---")
        .concat("---")
        .join(" | ")} |\n` +
      table.rows
        .map(
          (row) =>
            `| ${cell(row.label)} | ${row.cells.map((item) => cell(render([item]))).join(" | ")} |`,
        )
        .join("\n") +
      "\n\n"
    : "";
  return (
    `# ${escapeReportText(report.report.title)}\n\n## 执行摘要\n\n${render(report.report.executive_summary)}\n\n` +
    comparison +
    report.report.sections
      .map(
        (section) =>
          `## ${escapeReportText(section.heading)}\n\n${render(section.segments)}`,
      )
      .join("\n\n") +
    `\n\n## 结论\n\n${render(report.report.conclusion)}`
  );
}

export function ResearchReportContent({
  report,
  onCitation,
}: {
  report: Report;
  onCitation: (id: string) => void;
}) {
  const content = useMemo(() => reportMarkdown(report), [report]);
  const citationTitles = useMemo(
    () => new Map(report.citations.map((item) => [item.number, item.title])),
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
          return (
            <button
              type="button"
              data-evidence-id={id}
              aria-label={`查看引用 ${report.citation_map[id]}`}
              title={citationTitles.get(report.citation_map[id]!)}
              className="text-muted-foreground hover:bg-muted mx-0.5 inline-flex min-w-4 items-center justify-center rounded-full px-1 align-super text-[10px] tabular-nums"
              onClick={() => onCitation(id)}
            >
              {children}
            </button>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    }),
    [onCitation, report.citation_map, citationTitles],
  );
  return (
    <div className="research-report text-[15px] leading-7 [&_h1]:text-2xl [&_h1]:leading-snug [&_h2]:mt-8 [&_h2]:text-xl [&_h3]:text-base [&_p]:leading-7 [&_table]:text-sm">
      <MarkdownContent
        content={content}
        isLoading={false}
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
          {(["md", "docx", "html"] as const).map((format) => (
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
          aria-label="展开报告"
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
        研究完成 · {report.citations.length} 条引用
      </p>
      <div className="border-border/60 bg-muted/10 overflow-hidden rounded-2xl border">
        <div className="border-border/40 flex items-center justify-between gap-2 border-b px-4 py-2">
          <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
            <FileText className="size-4 shrink-0" />
            <span className="truncate">{report.report.title}</span>
          </span>
          <ResearchReportActions onDownload={onDownload} onExpand={onExpand} />
        </div>
        <div className="relative max-h-72 overflow-hidden px-5 pt-4 pb-10">
          <ResearchReportContent report={report} onCitation={onCitation} />
          <div className="from-background pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-linear-to-t to-transparent" />
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
