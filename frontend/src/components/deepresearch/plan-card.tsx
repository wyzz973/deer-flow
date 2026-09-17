"use client";

import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  LoaderCircle,
  Square,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  countdownSeconds,
  firstText,
  liveStatus,
  researchProgress,
} from "@/core/deepresearch/presentation";
import {
  reviewStatuses,
  steerableStatuses,
  terminal,
  type ResearchActivity,
  type ResearchMessage,
  type Run,
  type Unit,
} from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

function CountdownRing({ seconds, total }: { seconds: number; total: number }) {
  const radius = 8;
  const circumference = 2 * Math.PI * radius;
  const fraction = Math.max(0, Math.min(1, seconds / Math.max(1, total)));
  return (
    <span className="relative inline-flex size-6 items-center justify-center">
      <svg viewBox="0 0 20 20" className="absolute inset-0 size-6 -rotate-90">
        <circle
          cx="10"
          cy="10"
          r={radius}
          fill="none"
          strokeWidth="1.5"
          className="stroke-current opacity-20"
        />
        <circle
          cx="10"
          cy="10"
          r={radius}
          fill="none"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - fraction)}
          className="stroke-current transition-[stroke-dashoffset] duration-300 motion-reduce:transition-none"
        />
      </svg>
      <span className="text-[10px] tabular-nums">{seconds}</span>
    </span>
  );
}

function stepState(unit: Unit, run: Run) {
  const related = (run.units ?? []).filter(
    (item) =>
      item.id === unit.id || item.parent_gap_id?.startsWith(`${unit.id}-`),
  );
  const statuses = related.map((item) => run.unit_statuses[item.id]);
  if (statuses.includes("RESEARCHING")) return "running";
  if (run.unit_statuses[unit.id] === "COMPLETED") return "done";
  if (run.unit_statuses[unit.id] === "FAILED") return "failed";
  return "pending";
}

export function ResearchPlanCard({
  message,
  run,
  activity,
  busy,
  countdownTotal = 45,
  onEdit,
  onStart,
  onCancel,
  onDetails,
  onRetry,
  onUpdate,
}: {
  message: ResearchMessage;
  run: Run;
  activity?: ResearchActivity;
  busy: boolean;
  countdownTotal?: number;
  onEdit: () => void;
  onStart: () => void;
  onCancel: () => void;
  onDetails: () => void;
  onRetry: (allowLimitedReport?: boolean) => void;
  onUpdate?: () => void;
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
  // Historical plans store an empty title; fall back to their goal.
  const title = firstText(plan.title, plan.goal);
  const latest = plan.plan_version === run.plan?.plan_version;
  const status = run.status;
  const reported = Boolean(
    run.conversation?.some(
      (item) =>
        item.kind === "report" && (item.cycle ?? 0) === (message.cycle ?? 0),
    ),
  );
  const waiting = latest && status === "AWAITING_PLAN_CONFIRMATION";
  const editing = latest && status === "EDITING_PLAN";
  const failed = latest && status === "FAILED";
  const running =
    latest && !reported && !terminal.has(status) && !reviewStatuses.has(status);
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(timer);
  }, [waiting]);
  // Explicit owner consent for historical evidence gaps; the backend rechecks it.
  const limitedReport =
    failed && run.error?.code === "RESEARCH_GAPS" && run.evidence_count > 0;
  const remaining = countdownSeconds(
    run.auto_start_at,
    clock.serverTime,
    now - clock.receivedAt,
  );
  const steps = (
    <ol className="space-y-3 py-4">
      {plan.research_units.map((unit) => {
        const state = latest ? stepState(unit, run) : "pending";
        return (
          <li
            key={unit.id}
            className="flex items-start gap-3 text-sm leading-6"
          >
            {state === "failed" ? (
              <CircleAlert
                aria-label="未完成"
                className="text-destructive mt-1 size-4 shrink-0"
              />
            ) : state === "done" ? (
              <CircleCheck className="text-muted-foreground mt-1 size-4 shrink-0" />
            ) : state === "running" ? (
              <LoaderCircle className="mt-1 size-4 shrink-0 animate-spin motion-reduce:animate-none" />
            ) : (
              <CircleDashed className="text-muted-foreground/60 mt-1 size-4 shrink-0" />
            )}
            <span
              className={cn(
                state === "pending" && running && "text-muted-foreground",
              )}
            >
              {firstText(unit.title, unit.objective)}
            </span>
          </li>
        );
      })}
    </ol>
  );
  if (!latest || reported || status === "COMPLETED")
    return (
      <details className="border-border/60 max-w-xl rounded-2xl border px-4 py-3 text-sm">
        <summary className="text-muted-foreground cursor-pointer">
          {!latest ? "计划已更新" : "研究计划"} · {title}
        </summary>
        {steps}
      </details>
    );
  if (status === "CANCELLED")
    return (
      <div className="border-border/60 text-muted-foreground flex max-w-xl items-center justify-between rounded-2xl border px-4 py-3 text-sm">
        <span>研究已停止</span>
        <span className="truncate pl-4">{title}</span>
      </div>
    );
  const searches = activity?.counts.searches ?? 0;
  return (
    <section
      aria-label="研究计划"
      className="border-border/60 bg-muted/20 max-w-xl rounded-2xl border p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-base leading-6 font-medium">{title}</h2>
        {running && onUpdate && steerableStatuses.has(status) && (
          <Button
            variant="outline"
            size="sm"
            className="h-7 rounded-full"
            aria-label="更新研究要求"
            disabled={busy}
            onClick={onUpdate}
          >
            更新
          </Button>
        )}
      </div>
      {steps}
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
      {waiting || editing ? (
        <div className="flex items-center justify-between gap-2">
          <Button
            variant={editing ? "secondary" : "outline"}
            size="sm"
            className="rounded-full"
            aria-pressed={editing}
            disabled={busy || editing}
            onClick={onEdit}
          >
            编辑
          </Button>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="rounded-full"
              disabled={busy}
              onClick={onCancel}
            >
              取消
            </Button>
            <Button
              size="sm"
              className="gap-2 rounded-full pr-2"
              aria-label={
                remaining !== null && remaining > 0
                  ? `开始研究 倒计时 ${remaining} 秒`
                  : "开始研究"
              }
              disabled={busy || remaining === 0}
              onClick={onStart}
            >
              {remaining === 0 ? "即将开始" : "开始"}
              {waiting && remaining !== null && remaining > 0 && (
                <CountdownRing seconds={remaining} total={countdownTotal} />
              )}
            </Button>
          </div>
        </div>
      ) : failed ? (
        <Button
          variant="outline"
          size="sm"
          onClick={() => onRetry(limitedReport)}
          disabled={busy || (!run.error?.recoverable && !limitedReport)}
        >
          {limitedReport ? "生成带限制的报告" : "从检查点恢复"}
        </Button>
      ) : running ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3 text-xs">
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground min-w-0 truncate text-left"
              onClick={onDetails}
              title="查看研究活动"
            >
              <span role="status">{liveStatus(activity?.current, status)}</span>
            </button>
            {searches > 0 && (
              <span className="text-muted-foreground shrink-0 tabular-nums">
                {searches} 次搜索
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <div className="bg-muted h-1 flex-1 overflow-hidden rounded-full">
              <div
                className="bg-foreground/70 h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none"
                style={{
                  width: `${researchProgress(status, activity?.counts)}%`,
                }}
              />
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="停止研究"
              disabled={busy}
              onClick={onCancel}
            >
              <Square className="size-3 fill-current" />
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
