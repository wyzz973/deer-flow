"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Brain,
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  Search,
  Wrench,
} from "lucide-react";
import { Fragment, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import { writeTextToClipboard } from "@/core/clipboard";
import type { researchApi } from "@/core/deepresearch/api";
import {
  callNodeLabel,
  callStage,
  callTask,
  formatDuration,
  formatTokens,
  jsonPayload,
  matchingMessages,
  messageText,
  ROLE_LABELS,
  STAGE_LABELS,
  tokens,
} from "@/core/deepresearch/llm-calls";
import type {
  AuditGeneration,
  AuditMessage,
  AuditToolCall,
  LlmCallSummary,
  Run,
} from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import { JsonView } from "./json-view";

const COLLAPSE_AT = 1600;
const ROLE_STYLES: Record<string, string> = {
  system: "border-l-violet-500",
  user: "border-l-sky-500",
  assistant: "border-l-emerald-500",
  tool: "border-l-amber-500",
};
const STATUS_LABELS: Record<string, string> = {
  ok: "成功",
  error: "失败",
  interrupted: "中断",
  running: "进行中",
};

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      onClick={() => {
        void writeTextToClipboard(text).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        });
      }}
    >
      {done ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </Button>
  );
}

function Highlighted({ text, query }: { text: string; query: string }) {
  const needle = query.trim();
  if (!needle) return <>{text}</>;
  const pattern = new RegExp(
    `(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`,
    "gi",
  );
  return (
    <>
      {text.split(pattern).map((part, index) =>
        index % 2 === 1 ? (
          <mark
            key={index}
            className="rounded-sm bg-yellow-200 px-0.5 dark:bg-yellow-700/60"
          >
            {part}
          </mark>
        ) : (
          <Fragment key={index}>{part}</Fragment>
        ),
      )}
    </>
  );
}

function LongText({
  text,
  query,
  expanded,
  markdown,
}: {
  text: string;
  query: string;
  expanded: boolean;
  markdown?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const long = text.length > COLLAPSE_AT;
  const show = expanded || open || !long || Boolean(query.trim());
  if (markdown)
    return (
      <div className="text-sm">
        <MarkdownContent content={text} isLoading={false} />
      </div>
    );
  return (
    <div>
      <pre
        className={cn(
          "font-sans text-[13px] leading-6 break-words whitespace-pre-wrap",
          !show &&
            "max-h-72 overflow-hidden [mask-image:linear-gradient(to_bottom,black_75%,transparent)]",
        )}
      >
        <Highlighted text={text} query={query} />
      </pre>
      {long && !expanded && !query.trim() && (
        <button
          type="button"
          className="text-primary mt-1 text-xs hover:underline"
          onClick={() => setOpen(!open)}
        >
          {open ? "收起" : `展开全部（${text.length.toLocaleString()} 字符）`}
        </button>
      )}
    </div>
  );
}

function ToolCalls({
  calls,
  invalid,
}: {
  calls?: AuditToolCall[];
  invalid?: boolean;
}) {
  if (!calls?.length) return null;
  return (
    <div className="mt-2 space-y-2">
      {calls.map((call, index) => (
        <div
          key={call.id ?? index}
          className={cn(
            "bg-muted/30 rounded-lg border p-2",
            invalid && "border-destructive/50",
          )}
        >
          <div className="mb-1 flex items-center gap-2 text-xs">
            <Wrench className="size-3.5" />
            <span className="font-medium">{call.name ?? "tool"}</span>
            {invalid && <Badge variant="destructive">无法解析</Badge>}
            {call.id && (
              <span className="text-muted-foreground truncate font-mono">
                {call.id}
              </span>
            )}
            <div className="ml-auto">
              <CopyButton
                text={JSON.stringify(call.args ?? {}, null, 2)}
                label="复制参数"
              />
            </div>
          </div>
          {call.error && (
            <p className="text-destructive text-xs">{call.error}</p>
          )}
          <JsonView value={call.args ?? {}} defaultOpen={3} />
        </div>
      ))}
    </div>
  );
}

function MessageCard({
  message,
  index,
  query,
  expanded,
  matched,
}: {
  message: AuditMessage;
  index: number;
  query: string;
  expanded: boolean;
  matched: boolean;
}) {
  const text = messageText(message.content);
  const payload = message.role === "user" ? jsonPayload(text) : null;
  const [structured, setStructured] = useState(true);
  const [markdown, setMarkdown] = useState(false);
  return (
    <section
      className={cn(
        "bg-background rounded-lg border border-l-4 p-3 [content-visibility:auto]",
        ROLE_STYLES[message.role] ?? "border-l-slate-400",
        matched && "ring-2 ring-yellow-400/70",
      )}
      aria-label={`第 ${index + 1} 条消息：${ROLE_LABELS[message.role] ?? message.role}`}
    >
      <header className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground tabular-nums">#{index + 1}</span>
        <span className="font-medium">
          {ROLE_LABELS[message.role] ?? message.role}
        </span>
        {message.name && <Badge variant="outline">{message.name}</Badge>}
        {message.status === "error" && (
          <Badge variant="destructive">错误</Badge>
        )}
        {message.tool_call_id && (
          <span className="text-muted-foreground truncate font-mono">
            {message.tool_call_id}
          </span>
        )}
        <span className="text-muted-foreground">
          {text.length.toLocaleString()} 字符
        </span>
        <div className="ml-auto flex items-center gap-1">
          {payload && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => setStructured(!structured)}
            >
              {structured ? "看原文" : "按字段查看"}
            </Button>
          )}
          {message.role === "assistant" && text && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => setMarkdown(!markdown)}
            >
              {markdown ? "看原文" : "Markdown 预览"}
            </Button>
          )}
          <CopyButton text={text} label="复制这条消息" />
        </div>
      </header>
      {message.reasoning && (
        <details className="bg-muted/30 mb-2 rounded-md p-2 text-xs">
          <summary className="flex cursor-pointer items-center gap-1">
            <Brain className="size-3.5" />
            思考内容（{message.reasoning.length.toLocaleString()} 字符）
          </summary>
          <LongText
            text={message.reasoning}
            query={query}
            expanded={expanded}
          />
        </details>
      )}
      {payload && structured ? (
        <JsonView value={payload} defaultOpen={1} />
      ) : text ? (
        <LongText
          text={text}
          query={query}
          expanded={expanded}
          markdown={markdown}
        />
      ) : (
        !message.tool_calls?.length && (
          <p className="text-muted-foreground text-xs">（空内容）</p>
        )
      )}
      <ToolCalls calls={message.tool_calls} />
      <ToolCalls calls={message.invalid_tool_calls} invalid />
    </section>
  );
}

function Generation({
  generation,
  query,
}: {
  generation: AuditGeneration;
  query: string;
}) {
  const [markdown, setMarkdown] = useState(true);
  const text = messageText(generation.content);
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {generation.finish_reason && (
          <Badge variant="outline">结束原因：{generation.finish_reason}</Badge>
        )}
        {generation.response_model && (
          <Badge variant="outline">返回模型：{generation.response_model}</Badge>
        )}
        {generation.tool_calls?.length ? (
          <Badge variant="secondary">
            调用工具 {generation.tool_calls.length} 个
          </Badge>
        ) : null}
      </div>
      {generation.reasoning && (
        <details className="bg-muted/30 rounded-lg border p-3 text-sm">
          <summary className="flex cursor-pointer items-center gap-1 text-xs font-medium">
            <Brain className="size-3.5" />
            思考内容（{generation.reasoning.length.toLocaleString()} 字符）
          </summary>
          <LongText
            text={generation.reasoning}
            query={query}
            expanded={false}
          />
        </details>
      )}
      {text ? (
        <div className="rounded-lg border p-3">
          <div className="mb-2 flex items-center gap-2 text-xs">
            <span className="font-medium">回复正文</span>
            <span className="text-muted-foreground">
              {text.length.toLocaleString()} 字符
            </span>
            <div className="ml-auto flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={() => setMarkdown(!markdown)}
              >
                {markdown ? "看原文" : "Markdown 预览"}
              </Button>
              <CopyButton text={text} label="复制回复" />
            </div>
          </div>
          {jsonPayload(text) && !markdown ? (
            <JsonView value={jsonPayload(text)} defaultOpen={2} />
          ) : (
            <LongText text={text} query={query} expanded markdown={markdown} />
          )}
        </div>
      ) : (
        !generation.tool_calls?.length && (
          <p className="text-muted-foreground text-sm">模型没有返回正文。</p>
        )
      )}
      <ToolCalls calls={generation.tool_calls} />
      <ToolCalls calls={generation.invalid_tool_calls} invalid />
      {generation.usage && (
        <details className="text-xs">
          <summary className="text-muted-foreground cursor-pointer">
            用量明细
          </summary>
          <JsonView value={generation.usage} defaultOpen={2} />
        </details>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground text-[11px]">{label}</div>
      <div className="truncate text-sm font-medium tabular-nums">{value}</div>
    </div>
  );
}

/** Full-screen view of one model call: every message it was given and exactly what it returned. */
export function LlmCallDialog({
  api,
  runId,
  run,
  calls,
  callId,
  onSelect,
  onClose,
}: {
  api: ReturnType<typeof researchApi>;
  runId: string;
  run?: Run;
  calls: LlmCallSummary[];
  callId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [onlyNew, setOnlyNew] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const position = calls.findIndex((call) => call.id === callId);
  const summary = position >= 0 ? calls[position] : undefined;
  const detail = useQuery({
    queryKey: ["research-llm-call", api.root, runId, callId],
    queryFn: () => api.llmCall(runId, callId!),
    enabled: Boolean(callId) && Boolean(summary?.audited),
    retry: false,
  });
  const data = detail.data;
  const messages = useMemo(() => data?.messages ?? [], [data]);
  const matches = useMemo(
    () => matchingMessages(messages, query),
    [messages, query],
  );
  // Messages already sent in the previous turn of the same loop. Engines can
  // insert per-turn messages early, so compare message sets, not just prefixes.
  const fresh = useMemo(() => new Set(data?.new_message_indexes ?? []), [data]);
  const repeated =
    data?.previous_call_id && data.new_message_indexes
      ? messages.length - fresh.size
      : 0;
  const hidden = onlyNew ? repeated : 0;
  const call = data ?? summary;
  const stage = call ? STAGE_LABELS[callStage(call)] : "";
  const task = call ? callTask(call, run) : "";
  const turn = useMemo(() => {
    if (!call) return 1;
    const loop = call.group ?? call.execution_id ?? call.id;
    return calls
      .slice(0, position + 1)
      .filter(
        (item) =>
          (item.group ?? item.execution_id ?? item.id) === loop &&
          !callNodeLabel(item),
      ).length;
  }, [call, calls, position]);
  const requestJson = data ? JSON.stringify(data.openai_request, null, 2) : "";
  const generations = data?.response?.generations ?? [];
  return (
    <Dialog open={Boolean(callId)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton={false}
        className="flex h-[92vh] w-[min(1280px,96vw)] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none"
      >
        <header className="border-border/60 flex items-start gap-3 border-b px-5 py-3">
          <div className="min-w-0 flex-1">
            <DialogTitle className="truncate text-base">
              #{position + 1} {stage}
              {task && (
                <span className="text-muted-foreground font-normal">
                  {" "}
                  · {task}
                </span>
              )}
            </DialogTitle>
            <DialogDescription asChild>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                {call?.model && <Badge variant="secondary">{call.model}</Badge>}
                {call?.status && (
                  <Badge
                    variant={
                      call.status === "ok"
                        ? "outline"
                        : call.status === "running"
                          ? "secondary"
                          : "destructive"
                    }
                  >
                    {STATUS_LABELS[call.status] ?? call.status}
                  </Badge>
                )}
                <Badge variant="outline">
                  {call && callNodeLabel(call)
                    ? callNodeLabel(call)
                    : `Agent 第 ${turn} 轮`}
                </Badge>
                {call?.finish_reason && (
                  <span className="text-muted-foreground">
                    结束原因 {call.finish_reason}
                  </span>
                )}
                {call?.started_at && (
                  <span className="text-muted-foreground">
                    {new Date(call.started_at).toLocaleString()}
                  </span>
                )}
              </div>
            </DialogDescription>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="上一次调用"
              disabled={position <= 0}
              onClick={() => onSelect(calls[position - 1]!.id)}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <span className="text-muted-foreground text-xs tabular-nums">
              {position + 1}/{calls.length}
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="下一次调用"
              disabled={position < 0 || position >= calls.length - 1}
              onClick={() => onSelect(calls[position + 1]!.id)}
            >
              <ChevronRight className="size-4" />
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              关闭
            </Button>
          </div>
        </header>
        {call && (
          <div className="border-border/60 grid grid-cols-3 gap-3 border-b px-5 py-2 sm:grid-cols-4 lg:grid-cols-8">
            <Stat label="耗时" value={formatDuration(call.duration_ms)} />
            <Stat
              label="输入 tokens"
              value={formatTokens(tokens(call, "input"))}
            />
            <Stat
              label="输出 tokens"
              value={formatTokens(tokens(call, "output"))}
            />
            <Stat
              label="缓存命中"
              value={formatTokens(tokens(call, "cache_read"))}
            />
            <Stat
              label="推理 tokens"
              value={formatTokens(tokens(call, "reasoning"))}
            />
            <Stat label="消息" value={String(call.message_count ?? "—")} />
            <Stat label="可用工具" value={String(call.tools?.length ?? 0)} />
            <Stat
              label="提示字符"
              value={call.prompt_chars?.toLocaleString() ?? "—"}
            />
          </div>
        )}
        {summary && !summary.audited ? (
          <p className="text-muted-foreground p-6 text-sm">
            这次调用发生在完整审计记录启用之前（或关闭了内容记录），只有耗时与用量指标。
          </p>
        ) : detail.isLoading ? (
          <p className="text-muted-foreground p-6 text-sm">
            正在读取完整请求与返回…
          </p>
        ) : detail.isError ? (
          <p role="alert" className="text-destructive p-6 text-sm">
            {String(detail.error)}
          </p>
        ) : data ? (
          <Tabs defaultValue="request" className="min-h-0 flex-1 gap-0">
            <div className="border-border/60 flex flex-wrap items-center gap-2 border-b px-5 py-2">
              <TabsList variant="line">
                <TabsTrigger value="request">
                  请求 · {messages.length} 条消息
                </TabsTrigger>
                <TabsTrigger value="response">返回</TabsTrigger>
                <TabsTrigger value="tools">
                  工具定义 · {data.tools?.length ?? 0}
                </TabsTrigger>
                <TabsTrigger value="params">参数</TabsTrigger>
                <TabsTrigger value="raw">原始 JSON</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="request" className="min-h-0 overflow-y-auto">
              <div className="bg-background/95 sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b px-5 py-2 backdrop-blur">
                <div className="relative w-64 max-w-full">
                  <Search className="text-muted-foreground absolute top-2 left-2 size-3.5" />
                  <Input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="在这次请求中搜索"
                    aria-label="在请求中搜索"
                    className="h-8 pl-7 text-xs"
                  />
                </div>
                {query.trim() && (
                  <span className="text-muted-foreground text-xs">
                    {matches.size} 条消息命中
                  </span>
                )}
                {repeated > 0 && (
                  <Button
                    variant={onlyNew ? "secondary" : "ghost"}
                    size="sm"
                    className="h-8 text-xs"
                    onClick={() => setOnlyNew(!onlyNew)}
                  >
                    {onlyNew
                      ? `显示全部 ${messages.length} 条`
                      : `只看本轮新增（${repeated} 条与上一轮相同）`}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-xs"
                  onClick={() => setExpanded(!expanded)}
                >
                  {expanded ? "收起长消息" : "展开全部"}
                </Button>
                <div className="ml-auto flex items-center gap-1 text-xs">
                  {data.previous_call_id &&
                    calls.some((item) => item.id === data.previous_call_id) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 text-xs"
                        onClick={() => onSelect(data.previous_call_id!)}
                      >
                        上一轮
                      </Button>
                    )}
                </div>
              </div>
              <div className="space-y-3 px-5 py-4">
                {hidden > 0 && (
                  <p className="text-muted-foreground text-xs">
                    已隐藏 {hidden} 条与上一轮相同的消息。
                  </p>
                )}
                {messages.map((message, index) =>
                  message && (!onlyNew || fresh.has(index)) ? (
                    <MessageCard
                      key={`${data.message_hashes[index] ?? index}-${index}`}
                      message={message}
                      index={index}
                      query={query}
                      expanded={expanded}
                      matched={matches.has(index)}
                    />
                  ) : null,
                )}
              </div>
            </TabsContent>
            <TabsContent
              value="response"
              className="min-h-0 overflow-y-auto px-5 py-4"
            >
              {data.status === "error" || data.status === "interrupted" ? (
                <div className="border-destructive/40 bg-destructive/5 mb-4 flex items-start gap-2 rounded-lg border p-3 text-sm">
                  <AlertTriangle className="text-destructive mt-0.5 size-4" />
                  <div>
                    <p className="font-medium">这次调用没有正常完成</p>
                    <p className="text-muted-foreground text-xs">
                      {[
                        data.error?.code,
                        data.error?.type,
                        data.error?.status_code &&
                          `HTTP ${data.error.status_code}`,
                        data.error?.stage,
                      ]
                        .filter(Boolean)
                        .join(" · ") || data.error_code}
                    </p>
                  </div>
                </div>
              ) : null}
              {generations.length
                ? generations.map((generation, index) => (
                    <Generation
                      key={index}
                      generation={generation}
                      query={query}
                    />
                  ))
                : data.status !== "error" && (
                    <p className="text-muted-foreground text-sm">
                      还没有返回（调用可能仍在进行）。
                    </p>
                  )}
            </TabsContent>
            <TabsContent
              value="tools"
              className="min-h-0 space-y-3 overflow-y-auto px-5 py-4"
            >
              {data.tools?.length ? (
                data.tools.map((tool, index) => {
                  const fn = ((tool.function as
                    | Record<string, unknown>
                    | undefined) ?? tool) as {
                    name?: string;
                    description?: string;
                    parameters?: unknown;
                    input_schema?: unknown;
                  };
                  return (
                    <section
                      key={fn.name ?? index}
                      className="rounded-lg border p-3"
                    >
                      <div className="mb-1 flex items-center gap-2">
                        <Wrench className="size-3.5" />
                        <span className="font-mono text-sm font-medium">
                          {fn.name}
                        </span>
                      </div>
                      {fn.description && (
                        <p className="text-muted-foreground mb-2 text-sm whitespace-pre-wrap">
                          {fn.description}
                        </p>
                      )}
                      <JsonView
                        value={fn.parameters ?? fn.input_schema ?? {}}
                        name="parameters"
                        defaultOpen={2}
                      />
                    </section>
                  );
                })
              ) : (
                <p className="text-muted-foreground text-sm">
                  这次调用没有提供工具。
                </p>
              )}
            </TabsContent>
            <TabsContent
              value="params"
              className="min-h-0 overflow-y-auto px-5 py-4"
            >
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(data.params ?? {}).map(([key, value]) => (
                    <tr key={key} className="border-b">
                      <td className="text-muted-foreground py-2 pr-4 align-top font-mono text-xs">
                        {key}
                      </td>
                      <td className="py-2">
                        {value !== null && typeof value === "object" ? (
                          <JsonView value={value} defaultOpen={3} />
                        ) : (
                          <span className="font-mono text-xs">
                            {JSON.stringify(value)}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!Object.keys(data.params ?? {}).length && (
                <p className="text-muted-foreground text-sm">
                  没有记录到请求参数。
                </p>
              )}
            </TabsContent>
            <TabsContent
              value="raw"
              className="min-h-0 overflow-y-auto px-5 py-4"
            >
              <div className="mb-2 flex items-center gap-2">
                <span className="text-sm font-medium">
                  OpenAI 兼容格式的请求
                </span>
                <CopyButton text={requestJson} label="复制请求 JSON" />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="下载请求 JSON"
                  onClick={() => {
                    const url = URL.createObjectURL(
                      new Blob([requestJson], { type: "application/json" }),
                    );
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = `llm-call-${data.id}.json`;
                    link.click();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }}
                >
                  <Download className="size-3.5" />
                </Button>
              </div>
              <pre className="bg-muted/30 max-h-[50vh] overflow-auto rounded-lg p-3 font-mono text-[12px] leading-5">
                {requestJson}
              </pre>
              <p className="mt-4 mb-2 text-sm font-medium">返回</p>
              <pre className="bg-muted/30 max-h-[40vh] overflow-auto rounded-lg p-3 font-mono text-[12px] leading-5">
                {JSON.stringify(data.response ?? null, null, 2)}
              </pre>
            </TabsContent>
          </Tabs>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
