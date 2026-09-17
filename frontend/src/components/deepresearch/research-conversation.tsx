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
  X,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
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
import { useResearchConversation } from "@/core/deepresearch/hooks";
import {
  firstText,
  formatElapsed,
  reportTitle,
  retryRequest,
} from "@/core/deepresearch/presentation";
import {
  reviewStatuses,
  steerableStatuses,
  terminal,
  type Report,
  type ResearchMessage,
} from "@/core/deepresearch/types";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { ResearchPlanCard } from "./plan-card";
import { ResearchReportReader } from "./report-reader";
import { ResearchReportActions, ResearchReportCard } from "./report-view";
import { ResearchGallery } from "./research-gallery";
import { ResearchActivityPanel, ResearchSourcesPanel } from "./sources-panel";
import { ResearchTraceInspector } from "./trace-panel";

type Panel = "sources" | "activity" | "trace";

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
  const { isMobile } = useSidebar();
  const [panel, setPanel] = useState<Panel | null>(null);
  const lastPanel = useRef<Panel>("activity");
  if (panel) lastPanel.current = panel;
  const [selectedCitation, setSelectedCitation] = useState<string>();
  const [traceFocus, setTraceFocus] = useState<string>();
  const [reading, setReading] = useState<Report | null>(null);
  const [updating, setUpdating] = useState(false);
  const composer = useRef<HTMLDivElement>(null);
  const welcome = !runId;
  const status = run?.status ?? "";
  const reviewing = reviewStatuses.has(status);
  const editable = reviewing || status === "COMPLETED";
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
  const messages = useMemo<Message[]>(
    () =>
      records.map(
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
    [records],
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
        isLoading: (running && !steerable) || state.busy,
        isThreadLoading: state.loading,
        error: null,
        getMessagesMetadata: () => undefined,
      }) as unknown as BaseStream<AgentThreadState>,
    [messages, run?.query, running, steerable, state.busy, state.loading],
  );
  const { action } = state;
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
  const citation = useCallback((report: Report, id: string) => {
    setReading(report);
    setSelectedCitation(id);
    setPanel("sources");
  }, []);
  const download = useCallback(
    (format: "md" | "html" | "docx", version?: number) => {
      if (runId)
        void api
          .download(runId, format, version)
          .catch((error: unknown) => toast.error(String(error)));
    },
    [api, runId],
  );
  const inspect = useCallback((id?: string) => {
    setTraceFocus(id);
    setPanel("trace");
  }, []);
  const renderMessage = useCallback(
    (message: Message) => {
      const record = byId.get(message.id ?? "");
      if (!record || !run) return undefined;
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
          />
        );
      if (record.kind === "report" && record.report)
        return (
          <ResearchReportCard
            report={record.report}
            onExpand={() => setReading(record.report!)}
            onCitation={(id) => citation(record.report!, id)}
            onDownload={(format) => download(format, record.report!.version)}
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
      run,
      safeAction,
      state.busy,
    ],
  );
  const panelReport = reading ?? run?.report;
  const pane = (
    <div className="flex h-full min-h-0 flex-col" aria-label="研究详情">
      <div className="border-border/60 flex h-12 shrink-0 items-center gap-1 border-b px-2">
        {lastPanel.current === "trace" ? (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPanel("activity")}
            >
              <ArrowLeft className="size-3" />
              活动
            </Button>
            <span className="ml-2 text-xs">Trace</span>
          </>
        ) : (
          <div role="tablist" aria-label="来源与活动" className="flex gap-1">
            {(["sources", "activity"] as const).map((tab) => (
              <Button
                key={tab}
                variant={lastPanel.current === tab ? "secondary" : "ghost"}
                size="sm"
                role="tab"
                aria-selected={lastPanel.current === tab}
                onClick={() => setPanel(tab)}
              >
                {tab === "sources"
                  ? "来源"
                  : activity?.elapsed_seconds != null
                    ? `活动 · ${formatElapsed(activity.elapsed_seconds)}`
                    : "活动"}
              </Button>
            ))}
          </div>
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          aria-label="关闭研究详情"
          onClick={() => setPanel(null)}
        >
          <X className="size-4" />
        </Button>
      </div>
      <div
        className={cn(
          "min-h-0 flex-1",
          lastPanel.current === "trace" ? "overflow-hidden" : "overflow-y-auto",
        )}
      >
        {lastPanel.current === "sources" ? (
          <ResearchSourcesPanel
            report={panelReport}
            data={state.sources}
            selectedId={selectedCitation}
            onReference={(id) => {
              if (panelReport) setReading(panelReport);
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
          <ResearchActivityPanel activity={activity} onInspect={inspect} />
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
      <ChatBox
        threadId={run?.thread_id ?? "new-research"}
        browserEnabled={false}
        extensionPanel={{
          open: panel !== null,
          title: "研究详情",
          content: pane,
          onClose: () => setPanel(null),
        }}
      >
        <ChatSurface
          isWelcomeMode={welcome}
          header={
            reading ? (
              <>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="关闭报告"
                  onClick={() => setReading(null)}
                >
                  <X className="size-4" />
                </Button>
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                  {reportTitle(reading)}
                </span>
                <ResearchReportActions
                  onDownload={(format) => download(format, reading.version)}
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="来源与活动"
                  onClick={() => setPanel(panel ? null : "sources")}
                >
                  <ListTree className="size-4" />
                </Button>
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
                      onClick={() => inspect()}
                    >
                      <ScrollText className="size-4" />
                    </Button>
                  </>
                )}
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="新建研究"
                  onClick={() => {
                    state.newResearch();
                    setReading(null);
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
            <>
              <div className={cn("size-full", reading && "hidden")}>
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
                <ResearchReportReader
                  report={reading}
                  onCitation={(id) => citation(reading, id)}
                />
              )}
            </>
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
                <PromptInput
                  className="bg-background/5 w-full"
                  disabled={
                    state.busy || (Boolean(runId) && !editable && !steering)
                  }
                  maxFiles={0}
                  onError={() =>
                    toast.error("请通过已接入的研究来源提供资料。")
                  }
                  onSubmit={async ({ text }) => {
                    if (!text.trim()) return;
                    await state.send(text.trim());
                    setUpdating(false);
                  }}
                >
                  {quoted && planTitle && (
                    <div className="border-border/60 flex w-full items-center gap-2 border-b px-3 py-2 text-xs">
                      <CornerDownRight className="text-muted-foreground size-3.5 shrink-0" />
                      <span className="text-muted-foreground min-w-0 flex-1 truncate">
                        “{planTitle}”
                      </span>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={editing ? "放弃计划修改" : "取消更新"}
                        onClick={() => {
                          if (editing)
                            safeAction("plan/resume", {
                              plan_version: run?.plan?.plan_version,
                            });
                          setUpdating(false);
                        }}
                      >
                        <X className="size-3" />
                      </Button>
                    </div>
                  )}
                  <PromptInputBody>
                    <PromptInputTextarea
                      aria-label="研究消息"
                      placeholder={
                        quoted
                          ? "跟进问题或调整"
                          : status === "AWAITING_CLARIFICATION"
                            ? "补充你的需求…"
                            : welcome
                              ? "描述你想研究的问题…"
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
                        variant="ghost"
                        size="sm"
                        aria-label="停止研究"
                        onClick={() => safeAction("cancel")}
                      >
                        停止
                      </Button>
                    ) : (
                      <PromptInputSubmit
                        aria-label={
                          welcome
                            ? "发送研究请求"
                            : steering
                              ? "发送更新"
                              : "发送消息"
                        }
                        status={state.busy ? "submitted" : "ready"}
                        disabled={
                          state.busy ||
                          !cap?.ready ||
                          !controller.textInput.value.trim()
                        }
                      />
                    )}
                  </PromptInputFooter>
                </PromptInput>
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
    </ThreadContext.Provider>
  );
}
