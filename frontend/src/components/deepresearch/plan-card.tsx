"use client";

import { Check, Circle, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { countdownSeconds } from "@/core/deepresearch/presentation";
import type { ResearchMessage, Run } from "@/core/deepresearch/types";

export function ResearchPlanCard({
  message,
  run,
  busy,
  onEdit,
  onStart,
  onCancel,
  onResume,
  onDetails,
  onRetry,
}: {
  message: ResearchMessage;
  run: Run;
  busy: boolean;
  onEdit: () => void;
  onStart: () => void;
  onCancel: () => void;
  onResume: () => void;
  onDetails: () => void;
  onRetry: (allowLimitedReport?: boolean) => void;
}) {
  const [now, setNow] = useState(() => performance.now());
  const clock = useMemo(
    () => ({
      serverTime: run.server_time ?? new Date().toISOString(),
      receivedAt: run.client_received_at ?? performance.now(),
    }),
    [run.server_time, run.client_received_at],
  );
  const plan = message.plan!;
  const latest = plan.plan_version === run.plan?.plan_version;
  const waiting = latest && run.status === "AWAITING_PLAN_CONFIRMATION";
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(timer);
  }, [waiting]);
  const editing = latest && run.status === "EDITING_PLAN";
  const failed = latest && run.status === "FAILED";
  // This is explicit owner consent, not a bypass for invalid citations or a
  // report without evidence. The authenticated backend rechecks eligibility.
  const limitedReport =
    failed && run.error?.code === "RESEARCH_GAPS" && run.evidence_count > 0;
  const remaining = countdownSeconds(
    run.auto_start_at,
    clock.serverTime,
    now - clock.receivedAt,
  );
  const inactive = !latest || ["COMPLETED", "CANCELLED"].includes(run.status);
  const body = (
    <ol className="space-y-4 py-5">
      {plan.research_units.map((unit) => {
        const status = latest ? run.unit_statuses[unit.id] : undefined;
        return (
          <li
            key={unit.id}
            className="flex items-start gap-3 text-sm leading-6"
          >
            {status === "COMPLETED" ? (
              <Check className="text-muted-foreground mt-1 size-4 shrink-0" />
            ) : status === "RESEARCHING" ? (
              <Loader2 className="mt-1 size-4 shrink-0 animate-spin motion-reduce:animate-none" />
            ) : (
              <Circle className="text-muted-foreground/40 mt-1 size-4 shrink-0" />
            )}
            <span>{unit.objective}</span>
          </li>
        );
      })}
    </ol>
  );
  if (inactive)
    return (
      <details className="border-border/60 max-w-xl rounded-2xl border px-4 py-3 text-sm">
        <summary className="text-muted-foreground cursor-pointer">
          {!latest
            ? "计划已更新"
            : run.status === "CANCELLED"
              ? "研究已取消"
              : "研究已完成"}{" "}
          · {plan.goal}
        </summary>
        {body}
      </details>
    );
  return (
    <section
      aria-label="研究计划"
      className="border-border/60 bg-muted/20 max-w-xl rounded-2xl border p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-base leading-6 font-medium">{plan.goal}</h2>
        {!waiting && !editing && !failed && (
          <Button variant="ghost" size="sm" onClick={onDetails}>
            查看过程
          </Button>
        )}
      </div>
      {body}
      {failed && (
        <p role="alert" className="text-destructive mb-3 text-sm">
          {run.error?.message}
        </p>
      )}
      {limitedReport && (
        <p className="text-muted-foreground mb-3 text-xs">
          未解决的问题将列入研究限制，引用仍需通过校验。
        </p>
      )}
      <div className="flex items-center justify-between gap-2">
        {waiting || editing ? (
          <>
            <Button
              variant="outline"
              size="sm"
              className="rounded-full"
              disabled={busy || editing}
              onClick={onEdit}
            >
              {editing ? "修改中" : "编辑"}
            </Button>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={onCancel}
              >
                取消
              </Button>
              {editing ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={onResume}
                >
                  放弃修改
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="gap-3 rounded-full"
                  disabled={busy || remaining === 0}
                  onClick={onStart}
                >
                  {remaining === 0 ? "即将开始" : "开始研究"}
                  {remaining !== null && remaining > 0 && (
                    <span
                      className="text-xs tabular-nums"
                      aria-label={`倒计时 ${remaining} 秒`}
                    >
                      {remaining}
                    </span>
                  )}
                </Button>
              )}
            </div>
          </>
        ) : failed ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onRetry(limitedReport)}
            disabled={busy || (!run.error?.recoverable && !limitedReport)}
          >
            {limitedReport ? "生成带限制的报告" : "从检查点恢复"}
          </Button>
        ) : (
          <>
            <span role="status" className="text-muted-foreground text-xs">
              {run.status === "PLANNING"
                ? "正在调整计划…"
                : run.status === "SYNTHESIZING"
                  ? "正在整理报告…"
                  : "正在研究…"}{" "}
              · {run.usage.tool_calls} 次工具调用
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={onCancel}
            >
              停止研究
            </Button>
          </>
        )}
      </div>
      {!waiting && !editing && !failed && (
        <div className="bg-muted mt-3 h-1 overflow-hidden rounded-full">
          <div
            className="bg-foreground/40 h-full rounded-full transition-[width] motion-reduce:transition-none"
            style={{
              width: `${(100 * plan.research_units.filter((u) => run.unit_statuses[u.id] === "COMPLETED").length) / plan.research_units.length}%`,
            }}
          />
        </div>
      )}
    </section>
  );
}
