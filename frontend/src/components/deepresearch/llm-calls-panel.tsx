"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Download, RefreshCw, Search, Wrench } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { researchApi } from "@/core/deepresearch/api";
import {
  type CallStage,
  callStage,
  formatDuration,
  formatTokens,
  groupCalls,
  callNodeLabel,
  STAGE_LABELS,
  tokens,
  toolCallNames,
  toolCallSummary,
} from "@/core/deepresearch/llm-calls";
import type { LlmCallSummary, Run } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

const STATUS_DOT: Record<string, string> = {
  ok: "bg-emerald-500",
  error: "bg-destructive",
  interrupted: "bg-destructive",
  running: "bg-sky-500 animate-pulse motion-reduce:animate-none",
};

function matches(call: LlmCallSummary, needle: string) {
  if (!needle) return true;
  return [
    call.model,
    call.preview,
    call.last_input?.preview,
    call.unit_id,
    call.agent_name,
    call.skill,
    call.contract,
    ...(call.tools ?? []),
    ...toolCallNames(call),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

/** Every model call of a run, grouped by stage and task, newest state polled while running. */
export function ResearchLlmCalls({
  api,
  runId,
  run,
  active,
  stage: initialStage,
  onOpen,
}: {
  api: ReturnType<typeof researchApi>;
  runId: string;
  run?: Run;
  active: boolean;
  stage?: CallStage;
  onOpen: (callId: string, calls: LlmCallSummary[]) => void;
}) {
  const [stage, setStage] = useState<CallStage | "all">(initialStage ?? "all");
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const calls = useQuery({
    queryKey: ["research-llm-calls", api.root, runId],
    queryFn: () => api.llmCalls(runId),
    refetchInterval: active ? 3000 : false,
    retry: false,
  });
  const items = useMemo(() => calls.data?.items ?? [], [calls.data]);
  const stages = useMemo(() => [...new Set(items.map(callStage))], [items]);
  const needle = query.trim().toLowerCase();
  const visible = useMemo(
    () =>
      items.filter(
        (call) =>
          (stage === "all" || callStage(call) === stage) &&
          matches(call, needle),
      ),
    [items, stage, needle],
  );
  const groups = useMemo(
    () =>
      groupCalls(items, run)
        .map((group) => ({
          ...group,
          calls: group.calls.filter((call) =>
            visible.includes(items[call.index - 1]!),
          ),
        }))
        .filter((group) => group.calls.length),
    [items, run, visible],
  );
  const totals = useMemo(
    () => ({
      input: items.reduce((sum, call) => sum + tokens(call, "input"), 0),
      output: items.reduce((sum, call) => sum + tokens(call, "output"), 0),
      duration: items.reduce((sum, call) => sum + (call.duration_ms ?? 0), 0),
      errors: items.filter(
        (call) => call.status === "error" || call.status === "interrupted",
      ).length,
    }),
    [items],
  );
  return (
    <div
      className="flex h-full min-h-0 flex-col text-xs"
      data-testid="llm-calls-panel"
    >
      <div className="border-border/60 bg-background sticky top-0 z-10 space-y-2 border-b p-3">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="text-muted-foreground absolute top-2 left-2 size-3.5" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索模型、任务、工具或回复"
              aria-label="搜索模型调用"
              className="h-8 pl-7 text-xs"
            />
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="刷新模型调用"
            disabled={calls.isFetching}
            onClick={() => void calls.refetch()}
          >
            <RefreshCw
              className={cn(
                "size-3.5",
                calls.isFetching && "animate-spin motion-reduce:animate-none",
              )}
            />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="导出模型调用 JSONL"
            onClick={() => void api.downloadLlmCalls(runId)}
          >
            <Download className="size-3.5" />
          </Button>
        </div>
        <div
          className="flex flex-wrap gap-1"
          role="tablist"
          aria-label="按阶段筛选"
        >
          {(["all", ...stages] as const).map((value) => (
            <Button
              key={value}
              role="tab"
              aria-selected={stage === value}
              variant={stage === value ? "secondary" : "ghost"}
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setStage(value)}
            >
              {value === "all"
                ? `全部 ${items.length}`
                : `${STAGE_LABELS[value]} ${items.filter((call) => callStage(call) === value).length}`}
            </Button>
          ))}
        </div>
        <p className="text-muted-foreground">
          输入 {formatTokens(totals.input)} · 输出 {formatTokens(totals.output)}{" "}
          tokens · 模型耗时 {formatDuration(totals.duration)}
          {totals.errors > 0 && (
            <span className="text-destructive"> · {totals.errors} 次失败</span>
          )}
        </p>
      </div>
      {calls.isError && (
        <p role="alert" className="text-destructive p-3">
          {String(calls.error)}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {!calls.isLoading && !items.length && (
          <p className="text-muted-foreground p-4">还没有模型调用。</p>
        )}
        {groups.map((group) => {
          const closed = collapsed.has(group.key);
          return (
            <section key={group.key} className="border-border/40 border-b">
              <button
                type="button"
                className="hover:bg-muted/40 flex w-full items-center gap-2 px-3 py-2 text-left"
                aria-expanded={!closed}
                onClick={() =>
                  setCollapsed((old) => {
                    const next = new Set(old);
                    if (next.has(group.key)) next.delete(group.key);
                    else next.add(group.key);
                    return next;
                  })
                }
              >
                <ChevronDown
                  className={cn(
                    "size-3.5 transition-transform motion-reduce:transition-none",
                    closed && "-rotate-90",
                  )}
                />
                <span className="min-w-0 flex-1 truncate font-medium">
                  {group.label}
                </span>
                <span className="text-muted-foreground shrink-0 tabular-nums">
                  {group.calls.length} 次 · {formatTokens(group.inputTokens)}→
                  {formatTokens(group.outputTokens)} ·{" "}
                  {formatDuration(group.durationMs)}
                </span>
              </button>
              {!closed && (
                <ol>
                  {group.calls.map((call) => {
                    const names = toolCallSummary(call);
                    const node = callNodeLabel(call);
                    return (
                      <li key={call.id}>
                        <button
                          type="button"
                          className="hover:bg-muted/60 flex w-full flex-col gap-1 px-3 py-2 pl-8 text-left"
                          onClick={() => onOpen(call.id, items)}
                          aria-label={`查看第 ${call.index} 次模型调用`}
                        >
                          <span className="flex w-full items-center gap-2">
                            <span
                              className={cn(
                                "size-2 shrink-0 rounded-full",
                                STATUS_DOT[call.status ?? ""] ??
                                  "bg-muted-foreground",
                              )}
                            />
                            <span className="text-muted-foreground tabular-nums">
                              #{call.index}
                            </span>
                            <span>{node ?? `第 ${call.turn} 轮`}</span>
                            <span className="text-muted-foreground min-w-0 truncate">
                              {call.model}
                            </span>
                            <span className="text-muted-foreground ml-auto shrink-0 tabular-nums">
                              {formatTokens(tokens(call, "input"))}→
                              {formatTokens(tokens(call, "output"))} ·{" "}
                              {formatDuration(call.duration_ms)}
                            </span>
                          </span>
                          {(names.length > 0 ||
                            Boolean(call.preview) ||
                            Boolean(call.error)) && (
                            <span className="text-muted-foreground flex w-full items-center gap-1.5">
                              {names.length > 0 && (
                                <span className="inline-flex shrink-0 items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-700 dark:text-amber-400">
                                  <Wrench className="size-3" />
                                  {names.slice(0, 3).join(" · ")}
                                  {names.length > 3 && ` +${names.length - 3}`}
                                </span>
                              )}
                              <span className="min-w-0 truncate">
                                {call.error
                                  ? `失败：${call.error.code ?? call.error.type ?? ""}`
                                  : call.preview}
                              </span>
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ol>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
