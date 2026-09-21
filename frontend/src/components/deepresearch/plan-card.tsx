"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

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

/** How long “即将开始” may stay disabled after the deadline passed. */
const START_GRACE_MS = 5000;

// ChatGPT's card: pure white, a hairline border, 24px corners, no shadow.
const card = "bg-card rounded-[24px] border border-(--dr-line) p-4";
// 36px pill buttons, 14px medium.
const pill = "h-9 rounded-full px-4 text-sm font-medium";

/** Ring inside “开始”. `seconds` is a whole number that drops once a second;
 * the arc is sent to where it will be when that second ends and travels there
 * linearly, so it shrinks continuously instead of jumping. The deadline itself
 * stays server-owned: nothing here starts research. */
function CountdownRing({ seconds, total }: { seconds: number; total: number }) {
  const radius = 10;
  const circumference = 2 * Math.PI * radius;
  const fraction = Math.max(0, Math.min(1, (seconds - 1) / Math.max(1, total)));
  return (
    <span className="relative inline-flex size-6 items-center justify-center">
      <svg viewBox="0 0 24 24" className="absolute inset-0 size-6 -rotate-90">
        <circle
          cx="12"
          cy="12"
          r={radius}
          fill="none"
          strokeWidth="2"
          className="stroke-current opacity-30"
        />
        <circle
          data-countdown-arc
          cx="12"
          cy="12"
          r={radius}
          fill="none"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - fraction)}
          className="stroke-current transition-[stroke-dashoffset] duration-1000 ease-linear motion-reduce:transition-none"
        />
      </svg>
      <span className="text-[11px] leading-none tabular-nums">{seconds}</span>
    </span>
  );
}

/** A step in progress: a dark ring with a quarter of it left light, turning. */
export function ResearchSpinner({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 18 18"
      fill="none"
      aria-hidden
      data-research-spinner
      className={cn("research-spinner size-[18px] shrink-0", className)}
    >
      <circle
        cx="9"
        cy="9"
        r="8"
        strokeWidth="2"
        className="stroke-current opacity-25"
      />
      <circle
        cx="9"
        cy="9"
        r="8"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray="37.7 12.6"
        className="stroke-current"
      />
    </svg>
  );
}

/** A step that has not started: a light dashed circle, about twelve dashes. */
function PendingRing() {
  return (
    <svg
      viewBox="0 0 18 18"
      fill="none"
      aria-hidden
      className="mt-[3px] size-[18px] shrink-0"
    >
      <circle
        cx="9"
        cy="9"
        r="8"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray="2.4 1.79"
        stroke="var(--dr-dash, currentColor)"
      />
    </svg>
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
  onExpire,
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
  /** The server-owned deadline passed; the owner refreshes the snapshot. */
  onExpire?: () => void;
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
  const stopped = latest && status === "CANCELLED";
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
  // The server starts research at the deadline. If the snapshot still waits
  // a few seconds later (lost event, restart), hand the button back instead
  // of leaving “即将开始” disabled for good.
  const expired = waiting && remaining === 0;
  const deadline = run.auto_start_at ?? "";
  const [overdueFor, setOverdueFor] = useState<string | null>(null);
  const overdue = expired && overdueFor === deadline;
  const expire = useRef(onExpire);
  expire.current = onExpire;
  useEffect(() => {
    if (!expired) return;
    expire.current?.();
    const timer = setTimeout(() => {
      setOverdueFor(deadline);
      expire.current?.();
    }, START_GRACE_MS);
    return () => clearTimeout(timer);
  }, [expired, deadline]);
  const retry = failed && (
    <Button
      variant="outline"
      className={pill}
      onClick={() => onRetry(limitedReport)}
      disabled={busy || (!run.error?.recoverable && !limitedReport)}
    >
      {limitedReport ? "生成带限制的报告" : "从检查点恢复"}
    </Button>
  );
  const failure = failed && (
    <>
      <p role="alert" className="text-destructive mb-3 text-sm">
        {firstText(run.error?.message, "研究未能完成。")}
      </p>
      {limitedReport && (
        <p className="text-muted-foreground mb-3 text-xs">
          未解决的问题将列入研究限制，引用仍需通过校验。
        </p>
      )}
    </>
  );
  const steps = (
    <ol className="space-y-5 py-4">
      {plan.research_units.map((unit) => {
        const state = latest ? stepState(unit, run) : "pending";
        return (
          // Pending steps keep the foreground colour, like ChatGPT: the icon
          // alone says what has started.
          <li
            key={unit.id}
            data-step-state={state}
            className="flex items-start gap-3 text-base leading-6"
          >
            {state === "failed" ? (
              <CircleAlert
                aria-label="未完成"
                className="text-destructive mt-[3px] size-[18px] shrink-0"
              />
            ) : state === "done" ? (
              <CircleCheck className="mt-[3px] size-[18px] shrink-0" />
            ) : state === "running" ? (
              <ResearchSpinner className="mt-[3px]" />
            ) : (
              <PendingRing />
            )}
            <span>{firstText(unit.title, unit.objective)}</span>
          </li>
        );
      })}
    </ol>
  );
  if (!latest || reported || status === "COMPLETED") {
    // Once the report exists the conversation shows the stats line and the
    // report card in place of the plan, like ChatGPT; the plan stays in the
    // activity panel. A follow-up after the report can still fail or be
    // stopped, and that outcome and the way forward must remain visible.
    if (failed)
      return (
        <section
          aria-label="追问未完成"
          className={cn(card, "border-destructive/30 max-w-xl")}
        >
          {failure}
          {retry}
        </section>
      );
    if (stopped)
      return (
        <p
          role="status"
          className={cn(
            card,
            "text-muted-foreground max-w-xl px-4 py-3 text-sm",
          )}
        >
          研究已停止。已生成的报告仍可阅读和导出，也可以继续就这份报告提问或要求修改。
        </p>
      );
    // Folded, not gone: the steps and how each one ended stay one click away.
    // A plan the owner replaced says so even after its cycle reported, so the
    // one that actually ran is the one labelled as the plan.
    return (
      <details className={cn(card, "max-w-xl px-4 py-3 text-sm")}>
        <summary className="text-muted-foreground cursor-pointer">
          {latest ? "研究计划" : "计划已更新"} · {title}
        </summary>
        {steps}
      </details>
    );
  }
  if (status === "CANCELLED")
    return (
      <div
        className={cn(
          card,
          "text-muted-foreground flex max-w-xl items-center justify-between px-4 py-3 text-sm",
        )}
      >
        <span>研究已停止</span>
        <span className="truncate pl-4">{title}</span>
      </div>
    );
  const searches = activity?.counts.searches ?? 0;
  const statusText = liveStatus(activity?.current, status);
  return (
    <section
      aria-label="研究计划"
      className={cn(
        card,
        "max-w-xl",
        // The card takes the place of the waiting skeleton.
        (waiting || editing) &&
          "animate-in fade-in duration-300 ease-out motion-reduce:animate-none",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="text-base leading-6 font-semibold">{title}</h2>
        {running && onUpdate && steerableStatuses.has(status) && (
          <Button
            variant="outline"
            className={cn(pill, "-my-1.5")}
            aria-label="更新研究要求"
            disabled={busy}
            onClick={onUpdate}
          >
            更新
          </Button>
        )}
      </div>
      {steps}
      {failure}
      {waiting || editing ? (
        <div className="flex items-center justify-between gap-2">
          <Button
            variant={editing ? "secondary" : "outline"}
            className={pill}
            aria-pressed={editing}
            disabled={busy || editing}
            onClick={onEdit}
          >
            编辑
          </Button>
          <div className="flex gap-2">
            <Button
              variant="outline"
              className={pill}
              disabled={busy}
              onClick={onCancel}
            >
              取消
            </Button>
            <Button
              className={cn(pill, "gap-2 border-transparent pr-1.5")}
              aria-label={
                remaining !== null && remaining > 0
                  ? `开始研究 倒计时 ${remaining} 秒`
                  : "开始研究"
              }
              disabled={busy || (remaining === 0 && !overdue)}
              onClick={onStart}
            >
              {remaining === 0 && !overdue ? "即将开始" : "开始"}
              {waiting && remaining !== null && remaining > 0 && (
                <CountdownRing seconds={remaining} total={countdownTotal} />
              )}
            </Button>
          </div>
        </div>
      ) : failed ? (
        retry
      ) : running ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              className="min-w-0 truncate text-left text-[13px] leading-5"
              onClick={onDetails}
              title="查看研究活动"
            >
              {/* Keyed by its text: a new status fades in instead of swapping. */}
              <span
                key={statusText}
                role="status"
                className="loading-shimmer animate-in fade-in max-w-full truncate align-bottom duration-150 motion-reduce:animate-none"
              >
                {statusText}
              </span>
            </button>
            {searches > 0 && (
              <span className="shrink-0 text-sm text-(--dr-text-tertiary) tabular-nums">
                {searches} 次搜索
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-(--dr-track)">
              <div
                className="bg-foreground h-full rounded-full transition-[width] duration-500 motion-reduce:transition-none"
                style={{
                  width: `${researchProgress(status, activity?.counts)}%`,
                }}
              />
            </div>
            <button
              type="button"
              aria-label="停止研究"
              disabled={busy}
              onClick={onCancel}
              className="focus-visible:ring-ring flex size-7 shrink-0 items-center justify-center rounded-full bg-(--dr-track) transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:outline-none disabled:opacity-50 motion-reduce:transition-none"
            >
              <span className="bg-foreground size-2 rounded-[2px]" />
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
