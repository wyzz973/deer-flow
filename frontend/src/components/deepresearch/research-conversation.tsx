"use client";

import type { Message } from "@langchain/langgraph-sdk";
import type { BaseStream } from "@langchain/langgraph-sdk/react";
import {
  ArrowLeft,
  CornerDownRight,
  FlaskConical,
  ListTree,
  Plus,
  ScrollText,
  Settings2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { ChatBox } from "@/components/workspace/chats/chat-box";
import { ChatProviders } from "@/components/workspace/chats/chat-providers";
import { ChatSurface } from "@/components/workspace/chats/chat-surface";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { renderDiagrams } from "@/core/deepresearch/diagrams";
import { useResearchConversation } from "@/core/deepresearch/hooks";
import type { CallStage } from "@/core/deepresearch/llm-calls";
import {
  composerAccepts,
  firstText,
  formatElapsed,
  reportTitle,
  retryRequest,
  waitingPhase,
} from "@/core/deepresearch/presentation";
import {
  reviewStatuses,
  steerableStatuses,
  terminal,
  type LlmCallSummary,
  type Report,
  type ResearchMessage,
} from "@/core/deepresearch/types";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { LlmCallDialog } from "./llm-call-dialog";
import { ResearchLlmCalls } from "./llm-calls-panel";
import { ResearchMetricsPanel } from "./metrics-panel";
import { ResearchPlanCard } from "./plan-card";
import { ResearchPlanPending } from "./plan-pending";
import { ResearchReportReader } from "./report-reader";
import { ResearchReportActions, ResearchReportCard } from "./report-view";
import { ResearchRequestCard } from "./request-card";
import { ResearchGallery } from "./research-gallery";
import { ResearchActivityPanel, ResearchSourcesPanel } from "./sources-panel";
import { ResearchTraceInspector } from "./trace-panel";

type Panel = "sources" | "activity" | "metrics" | "trace";

/** Synthetic, view-only message that holds the place of the next card. */
const PENDING_ID = "research-pending";
/** The reader fades in and out, like ChatGPT's (300ms). */
const READER_FADE_MS = 300;
/** ChatGPT's side panel: 374px plus its hairline. Still draggable. */
const PANEL_WIDTH = "375px";

const panelTab =
  "focus-visible:ring-ring h-[34px] rounded-[12px] px-3 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none motion-reduce:transition-none";
const headerIcon =
  "text-foreground hover:bg-accent focus-visible:ring-ring flex size-9 items-center justify-center rounded-lg transition-colors focus-visible:ring-2 focus-visible:outline-none motion-reduce:transition-none";

/** Folding the sidebar for reading is not the owner's preference. The sidebar
 * stores its state in a cookie the server reads on the next load; put back
 * what they chose, so a reload while reading does not keep it folded. */
function keepSidebarPreference(open: boolean) {
  document.cookie = `sidebar_state=${open}; path=/; max-age=${60 * 60 * 24 * 7}`;
}

function reducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  );
}

export function ResearchConversation(props: {
  initialRunId?: string;
  apiBase?: string;
}) {
  return (
    <ChatProviders>
      <ResearchView {...props} />
    </ChatProviders>
  );
}

function ResearchView({
  initialRunId,
  apiBase = "",
}: {
  initialRunId?: string;
  apiBase?: string;
}) {
  const state = useResearchConversation(initialRunId, apiBase);
  const { run, runId, cap, api, activity } = state;
  const controller = usePromptInputController();
  const { isMobile, open: sidebarOpen, setOpen: setSidebarOpen } = useSidebar();
  const [panel, setPanel] = useState<Panel | null>(null);
  const lastPanel = useRef<Panel>("activity");
  if (panel) lastPanel.current = panel;
  const [selectedCitation, setSelectedCitation] = useState<string>();
  const [traceFocus, setTraceFocus] = useState<string>();
  // The audit view opens on model calls; activity links open the span timeline.
  const [traceMode, setTraceMode] = useState<"calls" | "timeline">("calls");
  const [callStage, setCallStage] = useState<CallStage>();
  const [auditCall, setAuditCall] = useState<{
    id: string;
    calls: LlmCallSummary[];
  } | null>(null);
  const [reading, setReading] = useState<Report | null>(null);
  // The reader covers the conversation while it fades in or out; only a
  // settled reader hides the list underneath.
  const [readerPhase, setReaderPhase] = useState<"open" | "moving" | "closing">(
    "open",
  );
  const [readerScrolled, setReaderScrolled] = useState(false);
  const [updating, setUpdating] = useState(false);
  const composer = useRef<HTMLDivElement>(null);
  const welcome = !runId;
  const status = run?.status ?? "";
  const reviewing = reviewStatuses.has(status);
  const steerable = steerableStatuses.has(status);
  const running = Boolean(run && !terminal.has(status) && !reviewing);
  const editing = status === "EDITING_PLAN";
  // An update is a non-interrupting message to a running research, like
  // ChatGPT's “更新”. It ends automatically once research stops accepting it.
  const steering = updating && steerable;
  const planTitle = run?.plan
    ? firstText(run.plan.title, run.plan.goal)
    : undefined;
  const records = useMemo<ResearchMessage[]>(() => {
    if (!run) return [];
    if (run.conversation?.length) return run.conversation;
    // Read-only projection for legacy runs; no synthetic execution records.
    return [
      {
        id: "initial",
        role: "user",
        kind: "text",
        text: run.query,
        at: run.created_at,
      },
      ...(run.plan
        ? [
            {
              id: "legacy-plan",
              role: "assistant",
              kind: "plan",
              text: run.plan.goal,
              plan: run.plan,
              at: run.created_at,
            } as ResearchMessage,
          ]
        : []),
      ...(run.report
        ? [
            {
              id: "legacy-report",
              role: "assistant",
              kind: "report",
              text: reportTitle(run.report),
              report: run.report,
              at: run.updated_at,
            } as ResearchMessage,
          ]
        : []),
    ];
  }, [run]);
  const byId = useMemo(() => new Map(records.map((r) => [r.id, r])), [records]);
  // Between a sent message and the next card the conversation is not idle:
  // “正在思考”, then a skeleton where the plan card will appear.
  const pending = run
    ? waitingPhase(status, records, run.plan?.plan_version)
    : null;
  const lastUser = records.map((record) => record.role).lastIndexOf("user");
  const rewritten = records
    .slice(lastUser + 1)
    .some((record) => record.kind === "rewrite");
  const messages = useMemo<Message[]>(
    () => [
      ...records.map(
        (record) =>
          ({
            id: record.id,
            type: record.role === "user" ? "human" : "ai",
            content:
              record.kind === "report"
                ? (record.report?.markdown ?? record.text)
                : record.text,
            additional_kwargs: { research_message_id: record.id },
          }) as Message,
      ),
      // MessageList drops assistant messages without content before it asks
      // renderMessage, so the placeholder carries its accessible text.
      ...(pending
        ? [{ id: PENDING_ID, type: "ai", content: "正在思考…" } as Message]
        : []),
    ],
    [records, pending],
  );
  // MessageList and ChatBox consume this read-only view. Mutations go through
  // the research API, not invented SDK submit/history methods.
  const thread = useMemo(
    () =>
      ({
        messages,
        values: {
          messages,
          title: run?.query ?? "DeepResearch",
          artifacts: [],
        },
        // The waiting placeholder is the activity indicator while it shows;
        // the list's own "执行中…" line would say the same thing twice.
        isLoading: ((running && !steerable) || state.busy) && !pending,
        isThreadLoading: state.loading,
        error: null,
        getMessagesMetadata: () => undefined,
      }) as unknown as BaseStream<AgentThreadState>,
    [
      messages,
      run?.query,
      running,
      steerable,
      pending,
      state.busy,
      state.loading,
    ],
  );
  // A plan card carries the outcome of a failed or stopped run. Without one
  // (planning itself failed), the composer area has to say so.
  const orphanOutcome =
    (status === "FAILED" || status === "CANCELLED") &&
    !records.some(
      (record) =>
        record.kind === "plan" &&
        record.plan?.plan_version === run?.plan?.plan_version,
    );
  // Navigation can select another conversation while this page stays mounted
  // (see `useResearchConversation`); views of the previous one must not linger.
  const shownRun = useRef(runId);
  useEffect(() => {
    const previous = shownRun.current;
    shownRun.current = runId;
    if (!previous || previous === runId) return;
    setReading(null);
    setReaderPhase("open");
    setPanel(null);
    setUpdating(false);
    setSelectedCitation(undefined);
    setAuditCall(null);
  }, [runId]);
  const { action, refresh } = state;
  const safeAction = useCallback(
    (name: string, body: unknown = {}) => {
      void action(name, body).catch(() => undefined);
    },
    [action],
  );
  const focusComposer = useCallback(() => {
    requestAnimationFrame(() =>
      composer.current?.querySelector("textarea")?.focus(),
    );
  }, []);
  const edit = useCallback(() => {
    if (!run?.plan) return;
    void action("plan/pause", { plan_version: run.plan.plan_version })
      .then(focusComposer)
      .catch(() => undefined);
  }, [run?.plan, action, focusComposer]);
  const readerTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const openReader = useCallback(
    (report: Report) => {
      clearTimeout(readerTimer.current);
      if (reading || reducedMotion()) setReaderPhase("open");
      else {
        setReaderScrolled(false);
        setReaderPhase("moving");
        readerTimer.current = setTimeout(
          () => setReaderPhase("open"),
          READER_FADE_MS,
        );
      }
      setReading(report);
    },
    [reading],
  );
  const closeReader = useCallback(() => {
    clearTimeout(readerTimer.current);
    const finish = () => {
      setReading(null);
      setReaderPhase("open");
      setReaderScrolled(false);
    };
    if (reducedMotion()) return finish();
    setReaderPhase("closing");
    readerTimer.current = setTimeout(finish, READER_FADE_MS);
  }, []);
  // Reading takes the whole width, like ChatGPT: the sidebar folds to its icon
  // rail while the reader is open and comes back when it closes (or when this
  // page goes away), if reading is what folded it.
  const sidebar = useRef({ open: sidebarOpen, setOpen: setSidebarOpen });
  sidebar.current = { open: sidebarOpen, setOpen: setSidebarOpen };
  const readerShown = reading !== null && readerPhase !== "closing";
  useEffect(() => {
    if (!readerShown || isMobile || !sidebar.current.open) return;
    sidebar.current.setOpen(false);
    keepSidebarPreference(true);
    return () => sidebar.current.setOpen(true);
  }, [readerShown, isMobile]);
  useEffect(() => () => clearTimeout(readerTimer.current), []);
  const citation = useCallback(
    (report: Report, id: string) => {
      openReader(report);
      setSelectedCitation(id);
      setPanel("sources");
    },
    [openReader],
  );
  const download = useCallback(
    (format: "md" | "html" | "docx", report?: Report) => {
      if (!runId) return;
      const run = async () => {
        // Word is the only export that holds pictures, and Mermaid needs a
        // browser to draw them, so this page draws them for the server.
        const diagrams =
          format === "docx" && report?.document
            ? await renderDiagrams(report.document)
            : undefined;
        await api.download(runId, format, report?.version, diagrams);
      };
      void run().catch((error: unknown) => toast.error(String(error)));
    },
    [api, runId],
  );
  const inspect = useCallback((id?: string, stage?: CallStage) => {
    setTraceFocus(id);
    setTraceMode(id ? "timeline" : "calls");
    setCallStage(stage);
    setPanel("trace");
  }, []);
  const renderMessage = useCallback(
    (message: Message) => {
      if (message.id === PENDING_ID)
        return pending ? (
          <ResearchPlanPending phase={pending} rewritten={rewritten} />
        ) : undefined;
      const record = byId.get(message.id ?? "");
      if (!record || !run) return undefined;
      if (record.kind === "rewrite")
        return (
          <ResearchRequestCard
            message={record}
            onInspect={() => inspect(undefined, "rewrite")}
          />
        );
      if (record.kind === "plan" && record.plan)
        return (
          <ResearchPlanCard
            message={record}
            run={run}
            activity={activity}
            busy={state.busy}
            countdownTotal={cap?.plan_countdown_seconds}
            onEdit={edit}
            onStart={() =>
              safeAction("plan/approve", {
                plan_version: record.plan!.plan_version,
              })
            }
            onCancel={() => safeAction("cancel")}
            onDetails={() => setPanel("activity")}
            // Only an explicit limited-report choice is sent. Other retries must
            // not persist a refusal the owner never expressed.
            onRetry={(allowLimitedReport = false) =>
              safeAction("retry", retryRequest(allowLimitedReport))
            }
            onUpdate={() => {
              setUpdating(true);
              focusComposer();
            }}
            onExpire={refresh}
          />
        );
      if (record.kind === "report" && record.report)
        return (
          <ResearchReportCard
            report={record.report}
            selectedId={selectedCitation}
            onExpand={() => openReader(record.report!)}
            onCitation={(id) => citation(record.report!, id)}
            onDownload={(format) => download(format, record.report)}
          />
        );
      return undefined;
    },
    [
      activity,
      byId,
      cap?.plan_countdown_seconds,
      citation,
      download,
      edit,
      focusComposer,
      inspect,
      openReader,
      pending,
      refresh,
      rewritten,
      run,
      safeAction,
      selectedCitation,
      state.busy,
    ],
  );
  const panelReport = reading ?? run?.report;
  const pane = (
    // Pure white with a hairline on the left. On mobile this renders inside a
    // portalled Sheet, so it carries the research palette itself.
    <div
      className="deepresearch-surface bg-card text-foreground flex h-full min-h-0 flex-col border-l border-(--dr-line-soft)"
      aria-label="研究详情"
    >
      <div className="flex h-12 shrink-0 items-center gap-1 px-2">
        {lastPanel.current === "trace" ? (
          <>
            <button
              type="button"
              className={cn(
                panelTab,
                "hover:bg-accent flex items-center gap-1",
              )}
              onClick={() => setPanel("activity")}
            >
              <ArrowLeft className="size-3" />
              活动
            </button>
            <div role="tablist" aria-label="调用审计" className="flex gap-1">
              {(["calls", "timeline"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  role="tab"
                  aria-selected={traceMode === mode}
                  className={cn(
                    panelTab,
                    traceMode === mode ? "bg-(--dr-chip)" : "hover:bg-accent",
                  )}
                  onClick={() => setTraceMode(mode)}
                >
                  {mode === "calls" ? "LLM 调用" : "Trace 时间线"}
                </button>
              ))}
            </div>
          </>
        ) : (
          <div role="tablist" aria-label="来源与活动" className="flex gap-1">
            {/* “指标” is ours, not ChatGPT's: it stays, at the end. */}
            {(["sources", "activity", "metrics"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={lastPanel.current === tab}
                className={cn(
                  panelTab,
                  lastPanel.current === tab
                    ? "bg-(--dr-chip)"
                    : "hover:bg-accent",
                )}
                onClick={() => setPanel(tab)}
              >
                {tab === "sources"
                  ? "来源"
                  : tab === "metrics"
                    ? "指标"
                    : activity?.elapsed_seconds != null
                      ? `活动 · ${formatElapsed(activity.elapsed_seconds)}`
                      : "活动"}
              </button>
            ))}
          </div>
        )}
        <button
          type="button"
          className={cn(headerIcon, "ml-auto size-8")}
          aria-label="关闭研究详情"
          onClick={() => setPanel(null)}
        >
          <X className="size-4" />
        </button>
      </div>
      <div
        // Keyed by tab: the new tab's content fades in.
        key={
          lastPanel.current === "trace"
            ? `trace:${traceMode}`
            : lastPanel.current
        }
        className={cn(
          "animate-in fade-in min-h-0 flex-1 duration-200 motion-reduce:animate-none",
          lastPanel.current === "trace" ? "overflow-hidden" : "overflow-y-auto",
        )}
      >
        {lastPanel.current === "sources" ? (
          <ResearchSourcesPanel
            report={panelReport}
            data={state.sources}
            selectedId={selectedCitation}
            onReference={(id) => {
              if (panelReport) openReader(panelReport);
              setSelectedCitation(id);
              // A mobile Sheet otherwise covers the destination being scrolled
              // into view. Desktop keeps the two views side by side.
              if (isMobile) setPanel(null);
              requestAnimationFrame(() =>
                document
                  .querySelector(
                    `[data-research-report-reader] [data-evidence-id="${CSS.escape(id)}"]`,
                  )
                  ?.scrollIntoView({
                    block: "center",
                    behavior: window.matchMedia(
                      "(prefers-reduced-motion: reduce)",
                    ).matches
                      ? "instant"
                      : "smooth",
                  }),
              );
            }}
          />
        ) : lastPanel.current === "activity" ? (
          <ResearchActivityPanel
            activity={activity}
            title={planTitle}
            onInspect={inspect}
          />
        ) : lastPanel.current === "metrics" ? (
          runId && (
            <ResearchMetricsPanel api={api} runId={runId} active={running} />
          )
        ) : traceMode === "calls" ? (
          runId && (
            <ResearchLlmCalls
              key={`${runId}:${callStage ?? "all"}`}
              api={api}
              runId={runId}
              run={run ?? undefined}
              active={running}
              stage={callStage}
              onOpen={(id, calls) => setAuditCall({ id, calls })}
            />
          )
        ) : (
          runId && (
            <ResearchTraceInspector
              key={`${runId}:${traceFocus ?? "all"}`}
              api={api}
              runId={runId}
              active={running}
              focusId={traceFocus}
              revision={run?.updated_at}
            />
          )
        )}
      </div>
    </div>
  );
  const quoted = editing || steering;
  return (
    <ThreadContext.Provider value={{ thread, isMock: Boolean(apiBase) }}>
      {/* The research palette is scoped to this page; see globals.css. */}
      <div className="deepresearch-surface bg-background text-foreground size-full min-h-0">
        {runId && auditCall && (
          <LlmCallDialog
            key={runId}
            api={api}
            runId={runId}
            run={run ?? undefined}
            calls={auditCall.calls}
            callId={auditCall.id}
            onSelect={(id) => setAuditCall({ ...auditCall, id })}
            onClose={() => setAuditCall(null)}
          />
        )}
        <ChatBox
          threadId={run?.thread_id ?? "new-research"}
          browserEnabled={false}
          extensionPanel={{
            open: panel !== null,
            title: "研究详情",
            content: pane,
            onClose: () => setPanel(null),
            defaultSize: PANEL_WIDTH,
          }}
        >
          <ChatSurface
            isWelcomeMode={welcome}
            // The reader's bar is bare: no title, and its hairline appears only
            // once the article has scrolled under it.
            headerClassName={
              reading
                ? cn(
                    "border-b shadow-none transition-[border-color] duration-150 motion-reduce:transition-none",
                    readerScrolled
                      ? "border-(--dr-line)"
                      : "border-transparent",
                  )
                : undefined
            }
            header={
              reading ? (
                <>
                  <button
                    type="button"
                    className={headerIcon}
                    aria-label="关闭报告"
                    onClick={closeReader}
                  >
                    <X className="size-5" strokeWidth={1.75} />
                  </button>
                  <span className="sr-only">{reportTitle(reading)}</span>
                  <span className="flex-1" />
                  <ResearchReportActions
                    report={reading}
                    onDownload={(format) => download(format, reading)}
                  />
                  <button
                    type="button"
                    className={headerIcon}
                    aria-label="来源与活动"
                    aria-pressed={panel !== null}
                    onClick={() => setPanel(panel ? null : "sources")}
                  >
                    <ListTree className="size-5" strokeWidth={1.75} />
                  </button>
                </>
              ) : (
                <>
                  <SidebarTrigger className="md:hidden" />
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">
                    {planTitle ?? run?.query ?? "DeepResearch"}
                  </span>
                  {runId && (
                    <>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="来源与活动"
                        onClick={() => setPanel(panel ? null : "activity")}
                      >
                        <ListTree className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="查看 Trace"
                        title="LLM 调用审计与 Trace"
                        onClick={() => inspect()}
                      >
                        <ScrollText className="size-4" />
                      </Button>
                    </>
                  )}
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="研究设置"
                    title="研究设置：模型、角色、提示词与数据源"
                    asChild
                  >
                    <Link
                      href={
                        apiBase
                          ? "/deepresearch-demo/settings"
                          : "/workspace/deepresearch/settings"
                      }
                    >
                      <Settings2 className="size-4" />
                    </Link>
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="新建研究"
                    onClick={() => {
                      state.newResearch();
                      setReading(null);
                      setReaderPhase("open");
                      setPanel(null);
                      setUpdating(false);
                      controller.textInput.setInput("");
                    }}
                  >
                    <Plus className="size-4" />
                  </Button>
                </>
              )
            }
            messages={
              <div className="relative size-full">
                <div
                  className={cn(
                    "size-full",
                    reading && readerPhase === "open" && "hidden",
                  )}
                >
                  <MessageList
                    threadId={run?.thread_id ?? "new-research"}
                    thread={thread}
                    className={cn("size-full", !welcome && "pt-10")}
                    testId="research-message-list"
                    enableSidecarActions={false}
                    renderMessage={renderMessage}
                    archiveDownloadsEnabled={false}
                    runDurationEnabled={false}
                  />
                </div>
                {reading && (
                  <div
                    className={cn(
                      "bg-background absolute inset-0 duration-300",
                      readerPhase === "closing"
                        ? "animate-out fade-out fill-mode-forwards ease-in-out"
                        : "animate-in fade-in ease-in-out",
                    )}
                  >
                    <ResearchReportReader
                      report={reading}
                      selectedId={selectedCitation}
                      onCitation={(id) => citation(reading, id)}
                      onScrolledChange={setReaderScrolled}
                    />
                  </div>
                )}
              </div>
            }
            composer={
              reading ? null : (
                <div ref={composer} data-research-composer className="relative">
                  {welcome && (
                    <h1 className="absolute inset-x-0 bottom-full mb-8 text-center text-2xl font-medium">
                      你想研究什么？
                    </h1>
                  )}
                  {state.error && (
                    <p role="alert" className="text-destructive mb-3 text-sm">
                      {state.error}
                    </p>
                  )}
                  {orphanOutcome &&
                    (status === "FAILED" ? (
                      <div
                        role="alert"
                        className="text-destructive mb-3 flex items-center justify-between gap-3 text-sm"
                      >
                        <span>
                          {firstText(run?.error?.message, "研究未能完成。")}
                        </span>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="text-foreground shrink-0"
                          disabled={state.busy || !run?.error?.recoverable}
                          onClick={() =>
                            safeAction("retry", retryRequest(false))
                          }
                        >
                          从检查点恢复
                        </Button>
                      </div>
                    ) : (
                      <p
                        role="status"
                        className="text-muted-foreground mb-3 text-sm"
                      >
                        研究已停止。继续研究请新建研究。
                      </p>
                    ))}
                  <ResearchComposer
                    status={status}
                    welcome={welcome}
                    busy={state.busy}
                    ready={Boolean(cap?.ready)}
                    updating={updating}
                    hasReport={Boolean(run?.report)}
                    retryable={Boolean(
                      run?.error?.recoverable === true ||
                      (run?.error?.code === "RESEARCH_GAPS" &&
                        run.evidence_count > 0),
                    )}
                    quote={quoted ? planTitle : undefined}
                    onSend={async (text) => {
                      await state.send(text);
                      setUpdating(false);
                    }}
                    onDismissQuote={() => {
                      if (editing)
                        safeAction("plan/resume", {
                          plan_version: run?.plan?.plan_version,
                        });
                      setUpdating(false);
                    }}
                    onStop={() => safeAction("cancel")}
                  />
                  {cap?.mode === "demo" && (
                    <p
                      role="note"
                      className="text-muted-foreground mt-2 text-center text-[11px]"
                    >
                      演示模式：合成测试数据，不代表真实研究结论。
                    </p>
                  )}
                  {welcome && (
                    <ResearchGallery
                      api={api}
                      onSuggestion={(text) => {
                        controller.textInput.setInput(text);
                        focusComposer();
                      }}
                    />
                  )}
                </div>
              )
            }
          />
        </ChatBox>
      </div>
    </ThreadContext.Provider>
  );
}

/** The research composer. It accepts a message only when the conversation
 * does (`composerAccepts`); every other control in the form is a plain button,
 * so dismissing a quote or stopping research can never submit the draft. */
export function ResearchComposer({
  status,
  welcome,
  busy,
  ready,
  updating,
  hasReport = false,
  retryable = false,
  quote,
  onSend,
  onDismissQuote,
  onStop,
}: {
  status: string;
  welcome: boolean;
  busy: boolean;
  ready: boolean;
  updating: boolean;
  /** A report exists: a stopped or failed follow-up can still be continued. */
  hasReport?: boolean;
  /** A failed run that the plan card can still retry. */
  retryable?: boolean;
  /** Plan title quoted above the input while editing or updating the plan. */
  quote?: string;
  onSend: (text: string) => Promise<void>;
  onDismissQuote: () => void;
  onStop: () => void;
}) {
  const controller = usePromptInputController();
  const reviewing = reviewStatuses.has(status);
  const steerable = steerableStatuses.has(status);
  // An empty status is a conversation still loading, not running research.
  const running = Boolean(status) && !terminal.has(status) && !reviewing;
  const editing = status === "EDITING_PLAN";
  const steering = updating && steerable;
  const accepts = composerAccepts({ welcome, status, updating, hasReport });
  return (
    <PromptInput
      className="bg-background/5 w-full"
      maxFiles={0}
      onError={() => toast.error("请通过已接入的研究来源提供资料。")}
      onSubmit={async ({ text }) => {
        if (!text.trim()) return;
        // Enter reaches this handler even when no submit button is rendered.
        // Rejecting (instead of returning) keeps the draft in the input.
        if (busy || !ready || !accepts)
          throw new Error("The conversation is not accepting messages.");
        await onSend(text.trim());
      }}
    >
      {quote && (
        <div className="border-border/60 flex w-full items-center gap-2 border-b px-3 py-2 text-xs">
          <CornerDownRight className="text-muted-foreground size-3.5 shrink-0" />
          <span className="text-muted-foreground min-w-0 flex-1 truncate">
            “{quote}”
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={editing ? "放弃计划修改" : "取消更新"}
            onClick={onDismissQuote}
          >
            <X className="size-3" />
          </Button>
        </div>
      )}
      <PromptInputBody>
        <PromptInputTextarea
          aria-label="研究消息"
          disabled={!accepts}
          placeholder={
            quote
              ? "跟进问题或调整"
              : status === "AWAITING_CLARIFICATION"
                ? "补充你的需求…"
                : welcome
                  ? "描述你想研究的问题…"
                  : accepts && (status === "FAILED" || status === "CANCELLED")
                    ? "继续提问或调整研究…"
                    : status === "FAILED"
                      ? retryable
                        ? "研究未完成：可在上方重试，或新建研究"
                        : "研究未完成：继续研究请新建研究"
                      : status === "CANCELLED"
                        ? "研究已停止：继续研究请新建研究"
                        : running
                          ? steerable
                            ? "研究进行中，点击计划上的“更新”补充要求"
                            : ["PLANNING", "CREATED"].includes(status)
                              ? "正在制定研究计划…"
                              : status === "RESPONDING"
                                ? "正在思考…"
                                : "正在整理报告…"
                          : "继续提问或调整研究…"
          }
          autoFocus
          // Browsers and extensions tag the focused field before React
          // hydrates (an embedded browser's focus id, a grammar checker's
          // markers). Those attributes are not ours to reconcile.
          suppressHydrationWarning
        />
      </PromptInputBody>
      <PromptInputFooter>
        <PromptInputTools>
          <span className="text-muted-foreground flex items-center gap-1.5 px-1 text-xs">
            <FlaskConical className="size-3.5" />
            深度研究
          </span>
        </PromptInputTools>
        {running && !steering ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label="停止研究"
            disabled={busy}
            onClick={onStop}
          >
            停止
          </Button>
        ) : (
          <PromptInputSubmit
            aria-label={
              welcome ? "发送研究请求" : steering ? "发送更新" : "发送消息"
            }
            status={busy ? "submitted" : "ready"}
            disabled={
              busy || !ready || !accepts || !controller.textInput.value.trim()
            }
          />
        )}
      </PromptInputFooter>
    </PromptInput>
  );
}
