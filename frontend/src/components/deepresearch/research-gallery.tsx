"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";

import type { researchApi } from "@/core/deepresearch/api";
import { firstText } from "@/core/deepresearch/presentation";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "参考 GitHub 与 GitLab，研究代码托管平台的智能化改造路径，给出分阶段路线图",
  "调研最近一个月主流大模型的重要发布、能力变化与评测口径",
  "为 5–15 人团队的内部知识库比较 SQLite 与 PostgreSQL，并给出选型建议",
];

/** Below the welcome composer: example requests and recent finished reports. */
export function ResearchGallery({
  api,
  onSuggestion,
}: {
  api: ReturnType<typeof researchApi>;
  onSuggestion: (text: string) => void;
}) {
  const [tab, setTab] = useState<"suggestions" | "reports">("suggestions");
  const history = useQuery({
    queryKey: ["research-history", api.root],
    queryFn: api.list,
    retry: false,
  });
  const reports = useMemo(
    () =>
      (history.data ?? [])
        .filter((run) => run.status === "COMPLETED")
        .slice(0, 8),
    [history.data],
  );
  return (
    <div className="mt-6 w-full">
      <div
        role="tablist"
        aria-label="研究推荐与历史报告"
        className="mb-3 flex gap-4 text-sm"
      >
        {(
          [
            ["suggestions", "推荐"],
            ["reports", "报告"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={cn(
              "pb-1",
              tab === key
                ? "text-foreground font-medium"
                : "text-muted-foreground",
            )}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "suggestions" ? (
        <ul className="space-y-1">
          {SUGGESTIONS.map((text) => (
            <li key={text}>
              <button
                type="button"
                className="text-muted-foreground hover:bg-muted hover:text-foreground w-full rounded-lg px-3 py-2 text-left text-sm"
                onClick={() => onSuggestion(text)}
              >
                {text}
              </button>
            </li>
          ))}
        </ul>
      ) : reports.length ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {reports.map((run) => (
            <Link
              key={run.run_id}
              href={`/workspace/deepresearch/${encodeURIComponent(run.run_id)}`}
              className="border-border/60 hover:bg-muted/40 flex min-h-24 flex-col justify-between rounded-2xl border p-3"
            >
              <span className="line-clamp-2 text-sm font-medium">
                {firstText(run.plan?.title, run.query)}
              </span>
              <span className="text-muted-foreground mt-2 text-xs">
                {new Date(run.updated_at).toLocaleDateString("zh-CN")}
              </span>
            </Link>
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground px-3 text-sm">
          完成的研究报告会出现在这里。
        </p>
      )}
    </div>
  );
}
