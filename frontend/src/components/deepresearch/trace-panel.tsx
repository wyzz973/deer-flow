"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { type researchApi } from "@/core/deepresearch/api";
import type { ResearchEvent } from "@/core/deepresearch/types";

type Api = ReturnType<typeof researchApi>;

/** Fetch only when expanded; a forward cursor preserves access to older spans. */
export function ResearchTracePanel({
  api,
  runId,
}: {
  api: Api;
  runId: string;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ResearchEvent[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const cursor = useRef(0);
  const fetching = useRef(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    if (fetching.current) return;
    fetching.current = true;
    setBusy(true);
    setError("");
    try {
      const page = await api.trace(runId, cursor.current);
      if (!alive.current) return;
      cursor.current = page.next_cursor;
      setItems((old) => [...old, ...page.items]);
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      fetching.current = false;
      if (alive.current) setBusy(false);
    }
  }, [api, runId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  return (
    <details
      className="mt-6 rounded-xl border border-black/10 p-4 dark:border-white/10"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary className="cursor-pointer text-sm">
        本地 Trace · 节点 / Agent / 模型 / 工具
      </summary>
      {open && (
        <div className="mt-4 space-y-3">
          <p className="text-xs text-neutral-500">
            Trace ID: {runId}。内容已脱敏并限长；未结束的 span
            可能仍在执行，或在进程中断前未写入结束记录。
          </p>
          <div className="flex gap-4 text-sm">
            <button
              type="button"
              disabled={busy}
              className="underline disabled:opacity-40"
              onClick={() => {
                void load();
              }}
            >
              {busy ? "加载中" : "加载后续 / 刷新"}
            </button>
            <button
              type="button"
              className="underline"
              onClick={() => {
                void api
                  .downloadTrace(runId)
                  .catch((e: unknown) => setError(String(e)));
              }}
            >
              导出完整 Trace JSONL
            </button>
          </div>
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          <div className="max-h-[36rem] space-y-2 overflow-auto">
            {items.map((item) => (
              <details
                key={item.seq}
                className="rounded border border-black/10 p-3 text-xs dark:border-white/10"
              >
                <summary className="cursor-pointer break-words">
                  {new Date(item.at).toLocaleTimeString()} ·{" "}
                  {String(item.data.kind)} · {String(item.data.name)} ·{" "}
                  {item.type === "trace.started"
                    ? "开始"
                    : String(item.data.status)}
                  {typeof item.data.duration_ms === "number"
                    ? ` · ${item.data.duration_ms} ms`
                    : ""}
                </summary>
                <pre className="mt-3 leading-6 break-all whitespace-pre-wrap">
                  {JSON.stringify(item.data, null, 2)}
                </pre>
              </details>
            ))}
          </div>
          {!busy && items.length === 0 && (
            <p className="text-sm text-neutral-500">
              此任务尚无 Trace 记录；旧任务不会自动补录。
            </p>
          )}
        </div>
      )}
    </details>
  );
}
