"use client";

import {
  ChevronDown,
  ChevronRight,
  Download,
  RefreshCw,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { writeTextToClipboard } from "@/core/clipboard";
import type { researchApi } from "@/core/deepresearch/api";
import { spanDepth, traceSpans } from "@/core/deepresearch/trace-model";
import type { ResearchEvent } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

const TRACE_PAGE = 200;
// One load stops here even if the trace goes on: every span becomes a row and
// a timeline mark. “加载后续事件” continues from the cursor.
const TRACE_LOAD_CAP = 5000;

export function ResearchTraceInspector({
  api,
  runId,
  active,
  focusId,
  revision,
}: {
  api: ReturnType<typeof researchApi>;
  runId: string;
  active: boolean;
  focusId?: string;
  revision?: string;
}) {
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(focusId);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const cursor = useRef(0),
    fetching = useRef(false),
    pending = useRef(false),
    alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const load = useCallback(async () => {
    if (fetching.current) {
      pending.current = true;
      return;
    }
    fetching.current = true;
    setBusy(true);
    try {
      do {
        pending.current = false;
        // A finished run has no later refresh: read to the end of the trace,
        // not just its first page.
        for (let loaded = 0; ; ) {
          const page = await api.trace(runId, cursor.current, TRACE_PAGE);
          if (!alive.current) return;
          cursor.current = page.next_cursor;
          if (page.items.length) setEvents((old) => [...old, ...page.items]);
          setError("");
          loaded += page.items.length;
          const full = page.items.length >= TRACE_PAGE;
          setMore(full);
          if (!full || loaded >= TRACE_LOAD_CAP) break;
        }
      } while (pending.current && alive.current);
    } catch (e) {
      if (alive.current) setError(String(e));
    } finally {
      fetching.current = false;
      if (alive.current) setBusy(false);
    }
  }, [api, runId]);
  useEffect(() => {
    void load();
  }, [load, revision]);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      void load();
    }, 2000);
    return () => clearInterval(timer);
  }, [active, load]);
  const spans = useMemo(() => traceSpans(events, active), [active, events]);
  const map = useMemo(() => new Map(spans.map((s) => [s.id, s])), [spans]);
  const children = useMemo(() => new Set(spans.map((s) => s.parent)), [spans]);
  const first = spans[0]?.start ?? Date.now();
  const end = Math.max(
    first + 1,
    ...spans.map((s) => s.start + (s.duration ?? 1)),
  );
  const current = selected ? map.get(selected) : undefined;
  const filtered = spans.filter((s) => {
    if (query)
      return `${s.name} ${s.kind} ${s.status}`
        .toLowerCase()
        .includes(query.toLowerCase());
    let parent = s.parent;
    const seen = new Set<string>();
    while (parent && !seen.has(parent)) {
      if (collapsed.has(parent)) return false;
      seen.add(parent);
      parent = map.get(parent)?.parent;
    }
    return true;
  });
  return (
    <div className="flex h-full min-h-0 flex-col text-xs">
      <div className="border-border/60 bg-background sticky top-0 z-10 space-y-3 border-b p-3">
        <div className="flex items-center gap-2">
          <Search className="text-muted-foreground size-3.5" />
          <Input
            aria-label="搜索 Trace"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索事件、模型、工具或状态"
            className="h-8 text-xs"
          />
          <Button
            aria-label="刷新 Trace"
            variant="ghost"
            size="icon-sm"
            disabled={busy}
            onClick={() => {
              void load();
            }}
          >
            <RefreshCw className={cn("size-3.5", busy && "animate-spin")} />
          </Button>
          <Button
            aria-label="导出 Trace JSONL"
            variant="ghost"
            size="icon-sm"
            onClick={() => {
              void api
                .downloadTrace(runId)
                .catch((e: unknown) => setError(String(e)));
            }}
          >
            <Download className="size-3.5" />
          </Button>
        </div>
        <div className="space-y-1" aria-label="Trace 时间轴">
          {["node", "model", "tool"].map((kind) => (
            <div key={kind} className="bg-muted/30 relative h-3 rounded">
              {spans
                .filter((s) =>
                  kind === "node"
                    ? !["model", "tool"].includes(s.kind)
                    : s.kind === kind,
                )
                .map((s) => (
                  // Thousands of marks must not sit in the tab order; the
                  // span list below reaches every one of them by keyboard.
                  <button
                    key={s.id}
                    type="button"
                    tabIndex={-1}
                    title={`${s.name} · ${s.duration ?? "?"} ms`}
                    aria-label={`定位 ${s.name}`}
                    className={cn(
                      "absolute h-3 min-w-1 rounded-sm opacity-60 hover:opacity-100",
                      s.status === "error"
                        ? "bg-destructive"
                        : kind === "tool"
                          ? "bg-chart-2"
                          : "bg-primary",
                    )}
                    style={{
                      left: `${(100 * (s.start - first)) / (end - first)}%`,
                      width: `${Math.max(0.3, (100 * (s.duration ?? 1)) / (end - first))}%`,
                    }}
                    onClick={() => {
                      setSelected(s.id);
                      setCollapsed(new Set());
                    }}
                  />
                ))}
            </div>
          ))}
        </div>
        <div className="text-muted-foreground flex justify-between">
          <span>
            {spans.length} 个事件 · {((end - first) / 1000).toFixed(1)}s
            {more && " · 还有更多"}
          </span>
          <button
            type="button"
            onClick={() =>
              setCollapsed(
                collapsed.size ? new Set() : new Set(spans.map((s) => s.id)),
              )
            }
          >
            {collapsed.size ? "展开全部" : "折叠全部"}
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-destructive p-3">
          {error}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        {filtered.map((span) => (
          <div
            key={span.id}
            className={cn(
              "border-border/40 flex items-center gap-1 border-b py-2 pr-3",
              selected === span.id && "bg-muted",
            )}
            style={{ paddingLeft: 10 + spanDepth(span, map) * 12 }}
          >
            <button
              type="button"
              aria-label={`折叠或展开 ${span.name}`}
              className={cn("size-4", !children.has(span.id) && "invisible")}
              onClick={() =>
                setCollapsed((old) => {
                  const next = new Set(old);
                  if (next.has(span.id)) next.delete(span.id);
                  else next.add(span.id);
                  return next;
                })
              }
            >
              {collapsed.has(span.id) ? (
                <ChevronRight className="size-3" />
              ) : (
                <ChevronDown className="size-3" />
              )}
            </button>
            <button
              type="button"
              className="min-w-0 flex-1 truncate text-left"
              onClick={() => setSelected(span.id)}
            >
              {span.name}
            </button>
            <span
              className={cn(
                "text-[10px]",
                span.status === "error"
                  ? "text-destructive"
                  : "text-muted-foreground",
              )}
            >
              {span.status}
            </span>
            <span className="text-muted-foreground w-12 text-right tabular-nums">
              {span.duration == null ? "—" : `${span.duration}ms`}
            </span>
          </div>
        ))}
        <Button
          variant="ghost"
          size="sm"
          className="w-full text-xs"
          disabled={busy}
          onClick={() => {
            void load();
          }}
        >
          {more ? "还有更多事件，继续加载" : "加载后续事件"}
        </Button>
      </div>
      {current && (
        <section
          className="border-border/60 max-h-[45%] overflow-auto border-t p-3"
          aria-label="Trace 事件详情"
        >
          <div className="mb-3 flex justify-between font-medium">
            <span>{current.name}</span>
            <button type="button" onClick={() => setSelected(undefined)}>
              关闭详情
            </button>
          </div>
          <p className="text-muted-foreground mb-3 break-all">
            {current.kind} · {current.id}
          </p>
          {(
            [
              ["输入", current.input],
              ["输出", current.output],
            ] as const
          ).map(([label, payload]) => (
            <details key={label} open className="mb-3">
              <summary className="cursor-pointer">{label}</summary>
              <Button
                variant="ghost"
                size="sm"
                className="float-right text-[10px]"
                onClick={() => {
                  void writeTextToClipboard(
                    JSON.stringify(payload ?? null, null, 2),
                  );
                }}
              >
                复制
              </Button>
              <pre className="text-muted-foreground mt-2 leading-5 break-all whitespace-pre-wrap">
                {payload === undefined
                  ? "未记录内容"
                  : JSON.stringify(payload, null, 2)}
              </pre>
            </details>
          ))}
        </section>
      )}
    </div>
  );
}
