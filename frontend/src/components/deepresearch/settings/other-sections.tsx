"use client";

import { useQuery } from "@tanstack/react-query";
import {
  KeyRound,
  ListChecks,
  Loader2,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { researchApi } from "@/core/deepresearch/api";
import { firstText } from "@/core/deepresearch/presentation";
import {
  edit,
  fieldLabels,
  IDENTIFIER,
  mcpBindings,
  renameMcpServer,
  REPORT_LENGTH_SCALE,
  rowKey,
} from "@/core/deepresearch/settings";
import type {
  EditableSettings,
  McpServerSpec,
  McpToolsResult,
  SettingsView,
} from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import {
  CREDENTIAL_HINT,
  FieldScope,
  JsonField,
  ListField,
  NumberField,
  SelectField,
  SwitchField,
  TextField,
} from "./fields";

const MCP_FAILURES: Record<string, string> = {
  auth: "鉴权失败",
  network: "无法连接",
  timeout: "超时",
  server: "服务异常",
  config: "配置错误",
};

/** The server name is a key: it changes only to a free, valid name, on blur or
 * Enter, and sources and providers bound to the server follow. */
function McpServerName({
  name,
  draft,
  onChange,
  disabled,
}: {
  name: string;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  disabled: boolean;
}) {
  const [text, setText] = useState<string | null>(null);
  const value = text ?? name;
  const problem =
    value === name
      ? ""
      : !IDENTIFIER.test(value)
        ? "只能包含字母、数字、- 和 _，并以字母或数字开头"
        : draft.mcp_servers[value]
          ? "已有同名的 MCP 服务"
          : "";
  const commit = () => {
    if (value !== name && !problem)
      onChange(edit(draft, (next) => void renameMcpServer(next, name, value)));
    setText(null);
  };
  return (
    <div className="space-y-1.5">
      <label htmlFor={`mcp-name-${name}`} className="text-sm font-medium">
        服务名称
      </label>
      <Input
        id={`mcp-name-${name}`}
        value={value}
        disabled={disabled}
        aria-invalid={Boolean(problem)}
        className="font-mono text-xs"
        onChange={(event) => setText(event.target.value.trim())}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
          if (event.key === "Escape") setText(null);
        }}
      />
      <p className="text-muted-foreground text-xs leading-5">
        {problem ? (
          <span className="text-destructive">
            {problem}；离开输入框时恢复原名
          </span>
        ) : (
          "离开输入框或按回车后改名，使用它的数据源和供应商会一起更新"
        )}
      </p>
    </div>
  );
}

function McpServerCard({
  name,
  server,
  draft,
  onChange,
  api,
  disabled,
}: {
  name: string;
  server: McpServerSpec;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
}) {
  const [tools, setTools] = useState<McpToolsResult["tools"] | null>(null);
  const [error, setError] = useState<{ title: string; message: string } | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const update = (change: (target: McpServerSpec) => void) =>
    onChange(edit(draft, (next) => change(next.mcp_servers[name]!)));
  const remote = server.transport !== "stdio";
  const used = draft.sources.some(
    (source) =>
      source.server === name ||
      source.providers.some((provider) => provider.server === name),
  );
  // Tools of this server that a source or provider calls.
  const bound = mcpBindings(draft).filter((item) => item.server === name);
  const inUse = [...new Set(bound.map((item) => item.tool))];
  const allowed = server.allowed_tools ?? null;
  const blocked = allowed
    ? inUse.filter((tool) => !allowed.includes(tool))
    : [];
  const unlisted =
    allowed && tools
      ? allowed.filter((tool) => !tools.some((item) => item.name === tool))
      : [];
  const allow = (tool: string, value: boolean) =>
    update((target) => {
      const next = new Set(target.allowed_tools ?? []);
      if (value) next.add(tool);
      else next.delete(tool);
      target.allowed_tools = [...next];
    });
  return (
    <FieldScope.Provider value={`MCP 服务 ${name}`}>
      <section
        className="space-y-4 rounded-xl border p-4"
        aria-label={`MCP 服务 ${name}`}
      >
        <header className="flex flex-wrap items-center gap-2">
          <h3 className="font-mono font-medium">{name}</h3>
          <Badge variant="outline">{server.transport}</Badge>
          {used && <Badge variant="secondary">已被数据源使用</Badge>}
          <div className="ml-auto flex items-center gap-2">
            <SwitchField
              label="启用"
              checked={server.enabled !== false}
              disabled={disabled}
              onChange={(value) => update((target) => (target.enabled = value))}
            />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`删除 MCP 服务 ${name}`}
              disabled={disabled || used}
              title={used ? "先删除使用它的数据源或供应商" : undefined}
              onClick={() =>
                onChange(edit(draft, (next) => delete next.mcp_servers[name]))
              }
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        </header>
        <div className="grid gap-3 md:grid-cols-2">
          <McpServerName
            name={name}
            draft={draft}
            onChange={onChange}
            disabled={disabled}
          />
          <SelectField
            label="传输方式"
            value={server.transport}
            options={[
              { value: "http", label: "Streamable HTTP" },
              {
                value: "streamable_http",
                label: "Streamable HTTP（streamable_http）",
              },
              { value: "sse", label: "SSE" },
              { value: "stdio", label: "本机命令（stdio）" },
            ]}
            disabled={disabled}
            onChange={(value) =>
              update(
                (target) =>
                  (target.transport = value as McpServerSpec["transport"]),
              )
            }
          />
          <NumberField
            label="连接与列出工具超时（秒）"
            value={server.timeout_seconds ?? 60}
            min={1}
            max={3600}
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.timeout_seconds = value ?? 60))
            }
          />
          {remote ? (
            <>
              <TextField
                label="服务地址（url）"
                value={server.url}
                mono
                disabled={disabled}
                onChange={(value) => update((target) => (target.url = value))}
              />
              <JsonField
                label="请求头（headers）"
                hint={CREDENTIAL_HINT}
                value={server.headers ?? {}}
                disabled={disabled}
                onChange={(value) =>
                  update(
                    (target) =>
                      (target.headers = (value ?? {}) as Record<
                        string,
                        string
                      >),
                  )
                }
              />
            </>
          ) : (
            <>
              <TextField
                label="命令（command）"
                hint="例如 npx 或 uvx"
                value={server.command}
                mono
                disabled={disabled}
                onChange={(value) =>
                  update((target) => (target.command = value))
                }
              />
              <ListField
                label="参数（每行一个）"
                value={server.args}
                placeholder={"-y\n@modelcontextprotocol/server-everything"}
                disabled={disabled}
                onChange={(value) => update((target) => (target.args = value))}
              />
              <JsonField
                label="环境变量（env）"
                hint={CREDENTIAL_HINT}
                value={server.env ?? {}}
                disabled={disabled}
                onChange={(value) =>
                  update(
                    (target) =>
                      (target.env = (value ?? {}) as Record<string, string>),
                  )
                }
              />
            </>
          )}
          <TextField
            label="说明"
            value={server.description}
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.description = value))
            }
          />
        </div>
        <div className="space-y-2 rounded-lg border p-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="font-medium">工具白名单</span>
            <label className="flex items-center gap-1.5 text-xs">
              <input
                type="radio"
                name={`mcp-allow-${name}`}
                checked={allowed === null}
                disabled={disabled}
                onChange={() =>
                  update((target) => (target.allowed_tools = null))
                }
              />
              不限制
            </label>
            <label className="flex items-center gap-1.5 text-xs">
              <input
                type="radio"
                name={`mcp-allow-${name}`}
                checked={allowed !== null}
                disabled={disabled}
                onChange={() =>
                  // Start from what research already calls, so switching the
                  // allowlist on never breaks a configured source.
                  update((target) => (target.allowed_tools ??= inUse))
                }
              />
              只允许列出的工具
            </label>
          </div>
          {allowed === null ? (
            <p className="text-muted-foreground text-xs leading-5">
              未限制：数据源/供应商可以引用该服务的任何工具。服务以后新增的工具也会自动可用；内部服务建议改为白名单。
            </p>
          ) : (
            <>
              <ListField
                label="允许的工具（每行一个）"
                hint="只有这里列出的工具可以被数据源和供应商引用；服务以后新增的工具不会自动开放。也可以先“连接并列出工具”再勾选"
                value={allowed}
                placeholder="search_documents"
                disabled={disabled}
                onChange={(value) =>
                  update((target) => (target.allowed_tools = value))
                }
              />
              {blocked.length > 0 && (
                <p className="text-destructive text-xs">
                  正在使用但不在白名单中：{blocked.join("、")}
                  。保存前把它们加入白名单，或修改对应的数据源/供应商。
                </p>
              )}
            </>
          )}
        </div>
        <div className="space-y-2">
          <Button
            variant="outline"
            size="sm"
            disabled={loading}
            onClick={() => {
              setLoading(true);
              setError(null);
              void api
                .mcpTools(name, server)
                .then((result: McpToolsResult) => {
                  setTools(result.tools);
                  setError(
                    result.ok
                      ? null
                      : {
                          title: MCP_FAILURES[result.kind ?? ""] ?? "连接失败",
                          message: result.message ?? result.error ?? "未知错误",
                        },
                  );
                })
                .catch((failure: unknown) =>
                  setError({
                    title: "请求失败",
                    message:
                      failure instanceof Error
                        ? failure.message
                        : String(failure),
                  }),
                )
                .finally(() => setLoading(false));
            }}
          >
            {loading ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <ListChecks className="size-3.5" />
            )}
            连接并列出工具
          </Button>
          <p className="text-muted-foreground text-xs">
            使用表单里当前（未保存）的配置连接。
          </p>
          {error && (
            <div
              role="alert"
              className="border-destructive/40 bg-destructive/5 rounded-md border p-2 text-xs"
            >
              <p className="font-medium">{error.title}</p>
              <p className="break-all">{error.message}</p>
            </div>
          )}
          {tools && !error && (
            <ul className="space-y-1 text-xs">
              {tools.map((tool) => {
                const open = allowed === null || allowed.includes(tool.name);
                return (
                  <li
                    key={tool.name}
                    className={cn(
                      "flex items-start gap-2 rounded-md border p-2",
                      !open && "opacity-60",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      aria-label={`允许工具 ${tool.name}`}
                      checked={open}
                      disabled={disabled || allowed === null}
                      title={
                        allowed === null
                          ? "当前未限制；选择“只允许列出的工具”后可以勾选"
                          : undefined
                      }
                      onChange={(event) =>
                        allow(tool.name, event.target.checked)
                      }
                    />
                    <div className="min-w-0">
                      <span className="font-mono font-medium">{tool.name}</span>
                      {inUse.includes(tool.name) && (
                        <Badge variant="secondary" className="ml-2">
                          使用中
                        </Badge>
                      )}
                      <span className="text-muted-foreground">
                        {" "}
                        · 参数：{Object.keys(tool.arguments).join("、") || "无"}
                      </span>
                      {tool.description && (
                        <p className="text-muted-foreground mt-1 line-clamp-2">
                          {tool.description}
                        </p>
                      )}
                    </div>
                  </li>
                );
              })}
              {!tools.length && (
                <li className="text-muted-foreground">服务没有提供工具。</li>
              )}
              {unlisted.length > 0 && (
                <li className="text-amber-600 dark:text-amber-400">
                  白名单里有服务没有提供的工具：{unlisted.join("、")}
                </li>
              )}
            </ul>
          )}
        </div>
      </section>
    </FieldScope.Provider>
  );
}

export function McpSection({
  draft,
  onChange,
  api,
  disabled,
}: {
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
}) {
  const [name, setName] = useState("");
  const valid = IDENTIFIER.test(name) && !draft.mcp_servers[name];
  return (
    <div className="space-y-6">
      <p className="text-muted-foreground text-sm">
        研究专用的 MCP 服务，与 DeerFlow 的 extensions_config.json 无关。MCP
        工具可以作为数据源的一个供应商（参与故障切换），也可以直接作为一个数据源暴露给研究员。返回格式不固定也没关系：系统会自动识别结果里的标题、链接和正文。
      </p>
      {Object.entries(draft.mcp_servers).map(([serverName, server]) => (
        // Keyed by the server, not its name, so a rename keeps the card.
        <McpServerCard
          key={rowKey(server)}
          name={serverName}
          server={server}
          draft={draft}
          onChange={onChange}
          api={api}
          disabled={disabled}
        />
      ))}
      <div className="flex flex-wrap items-end gap-2 rounded-xl border border-dashed p-4">
        <div className="space-y-1.5">
          <label htmlFor="new-mcp-name" className="text-sm font-medium">
            服务名称
          </label>
          <Input
            id="new-mcp-name"
            value={name}
            placeholder="例如 internal-kb"
            className="w-64 font-mono text-xs"
            disabled={disabled}
            onChange={(event) => setName(event.target.value.trim())}
          />
        </div>
        <Button
          variant="outline"
          disabled={disabled || !valid}
          onClick={() => {
            onChange(
              edit(
                draft,
                (next) =>
                  (next.mcp_servers[name] = {
                    transport: "http",
                    url: "http://127.0.0.1:9000/mcp",
                    headers: {},
                    args: [],
                    env: {},
                    timeout_seconds: 60,
                    enabled: true,
                    description: "",
                    allowed_tools: null,
                  }),
              ),
            );
            setName("");
          }}
        >
          <Plus className="size-4" />
          添加 MCP 服务
        </Button>
      </div>
    </div>
  );
}

export function RuntimeSection({
  view,
  draft,
  onChange,
  disabled,
}: {
  view: SettingsView;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  disabled: boolean;
}) {
  const set = <K extends keyof EditableSettings>(
    key: K,
    value: EditableSettings[K],
  ) => onChange(edit(draft, (next) => (next[key] = value)));
  const ceiling = view.operator.budget_ceiling;
  const gapCodes = view.catalog.gap_codes ?? [];
  const selectedGaps = draft.supplement_gap_codes ?? [];
  const rangeProblem =
    draft.plan_min_units > draft.plan_max_units
      ? "最多步骤数不能小于最少步骤数"
      : "";
  // Show the compaction threshold in tokens using the summary model's window.
  const contextWindow =
    draft.models.find(
      (model) => model.name === (draft.compaction.model ?? draft.default_model),
    )?.context_window ?? 0;
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-3">
        <NumberField
          label="同时研究的单元数"
          hint="同时执行的研究步骤数；本地模型建议 1-2"
          value={draft.max_concurrency}
          min={1}
          max={8}
          step={1}
          disabled={disabled}
          onChange={(value) => set("max_concurrency", value ?? 3)}
        />
        <NumberField
          label="章节写作并发"
          hint="章节写作并发；留空=与研究并发相同"
          value={draft.writer_concurrency}
          min={1}
          max={16}
          step={1}
          nullable
          placeholder="与研究并发相同"
          disabled={disabled}
          onChange={(value) => set("writer_concurrency", value)}
        />
        <NumberField
          label="计划倒计时（秒）"
          value={draft.plan_countdown_seconds}
          min={1}
          max={600}
          disabled={disabled}
          onChange={(value) => set("plan_countdown_seconds", value ?? 45)}
        />
        <NumberField
          label="计划最少步骤数"
          hint="计划的研究步骤数范围（1–12）"
          value={draft.plan_min_units}
          min={1}
          max={12}
          step={1}
          disabled={disabled}
          onChange={(value) => set("plan_min_units", value ?? 3)}
        />
        <NumberField
          label="计划最多步骤数"
          hint={
            rangeProblem ? (
              <span className="text-destructive">{rangeProblem}</span>
            ) : (
              "计划的研究步骤数范围（1–24），不能小于最少步骤数"
            )
          }
          value={draft.plan_max_units}
          min={1}
          max={24}
          step={1}
          disabled={disabled}
          onChange={(value) => set("plan_max_units", value ?? 6)}
        />
        <NumberField
          label="单次输出上限（tokens）"
          hint="默认 8192；实际取它和模型上限的较小值，节点可以在“节点调参”里单独设置"
          value={draft.max_output_tokens}
          min={128}
          max={393216}
          step={1}
          disabled={disabled}
          onChange={(value) => set("max_output_tokens", value ?? 8192)}
        />
        <NumberField
          label="每个研究单元的检索次数上限"
          hint="一步最多检索多少次，打开已找到的网页不计入；用完时检索工具会要求模型读完已找到的页面后收尾，而不是让研究失败。留空只受部署上限约束"
          value={draft.max_searches_per_unit}
          min={1}
          max={500}
          step={1}
          nullable
          disabled={disabled}
          onChange={(value) => set("max_searches_per_unit", value)}
        />
        <NumberField
          label="单个研究步骤的软时限（秒）"
          hint="单个研究步骤的软时限（秒）：到点后检索工具会让研究员收尾写笔记，而不是被硬超时杀掉；留空=不限"
          value={draft.max_seconds_per_unit}
          min={30}
          max={14400}
          step={1}
          nullable
          placeholder="不限"
          disabled={disabled}
          onChange={(value) => set("max_seconds_per_unit", value)}
        />
        <NumberField
          label="每步发现条数上限"
          hint="每个研究步骤交给写作的发现条数上限"
          value={draft.max_findings_per_unit}
          min={1}
          max={100}
          step={1}
          disabled={disabled}
          onChange={(value) => set("max_findings_per_unit", value ?? 12)}
        />
        <NumberField
          label="写报告预留时间（秒）"
          hint={`从时间预算里为写报告预留的秒数；留空=上限的 1/4（120–900 秒）。研究在只剩这么多时间时收尾，而不是到点整体失败${ceiling.max_elapsed_seconds ? `。当前执行时长上限 ${ceiling.max_elapsed_seconds} 秒` : ""}`}
          value={draft.report_time_reserve_seconds}
          min={30}
          max={7200}
          step={1}
          nullable
          placeholder="上限的 1/4"
          disabled={disabled}
          onChange={(value) => set("report_time_reserve_seconds", value)}
        />
        <NumberField
          label="整理失败重试次数"
          value={draft.output_retries}
          min={0}
          max={4}
          step={1}
          disabled={disabled}
          onChange={(value) => set("output_retries", value ?? 2)}
        />
        <NumberField
          label="报告最多章节数"
          value={draft.max_report_sections}
          min={2}
          max={12}
          step={1}
          disabled={disabled}
          onChange={(value) => set("max_report_sections", value ?? 8)}
        />
        <NumberField
          label="报告长度系数"
          hint="报告长度系数：乘到每章与摘要的长度目标上；1.0 时同题约 2.9 万字，ChatGPT 约 5 千字加表格，0.4–0.5 接近它的密度"
          value={draft.report_length_scale ?? REPORT_LENGTH_SCALE.fallback}
          min={REPORT_LENGTH_SCALE.min}
          max={REPORT_LENGTH_SCALE.max}
          step={0.1}
          disabled={disabled}
          onChange={(value) =>
            set("report_length_scale", value ?? REPORT_LENGTH_SCALE.fallback)
          }
        />
        <NumberField
          label="引用修复次数"
          value={draft.max_synthesis_repairs}
          min={0}
          max={3}
          step={1}
          disabled={disabled}
          onChange={(value) => set("max_synthesis_repairs", value ?? 1)}
        />
      </div>
      {/* An older gateway has no gap catalog; nothing to choose from then. */}
      {gapCodes.length > 0 && (
        <section className="space-y-2 rounded-xl border p-4">
          <h3 className="font-medium">触发补研的缺口</h3>
          <p className="text-muted-foreground text-xs leading-5">
            哪些缺口会触发补研；全部取消=从不补研，缺口直接写进报告局限。慢模型建议只保留
            coverage、unsupported。
          </p>
          <div className="grid gap-2 md:grid-cols-2">
            {gapCodes.map((gap) => (
              <label
                key={gap.code}
                className="flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs"
              >
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={selectedGaps.includes(gap.code)}
                  disabled={disabled}
                  onChange={(event) =>
                    set(
                      "supplement_gap_codes",
                      // Kept in catalog order, so toggling back is no change.
                      gapCodes
                        .map((item) => item.code)
                        .filter((code) =>
                          code === gap.code
                            ? event.target.checked
                            : selectedGaps.includes(code),
                        ),
                    )
                  }
                />
                <span>
                  <span className="font-mono">{gap.code}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    {gap.description}
                  </span>
                </span>
              </label>
            ))}
          </div>
          {!selectedGaps.length && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              已全部取消：研究不会补研，所有缺口直接写进报告的局限。
            </p>
          )}
        </section>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        <SwitchField
          label="缺口未补齐时仍写报告"
          hint="在报告中说明局限；关闭后需要用户确认"
          checked={draft.allow_limited_report}
          disabled={disabled}
          onChange={(value) => set("allow_limited_report", value)}
        />
        <SwitchField
          label="记录完整的 LLM 调用"
          hint="保存每次模型调用的全部提示词与返回，供“LLM 调用”审计查看"
          checked={draft.llm_audit}
          disabled={disabled}
          onChange={(value) => set("llm_audit", value)}
        />
        <SwitchField
          label="Trace 记录内容"
          hint="关闭后 Trace 与 LLM 调用都只保留耗时与用量"
          checked={draft.trace_capture_content}
          disabled={disabled}
          onChange={(value) => set("trace_capture_content", value)}
        />
      </div>
      <section className="space-y-3 rounded-xl border p-4">
        <h3 className="font-medium">上下文压缩</h3>
        <p className="text-muted-foreground text-xs">
          研究员打开的网页很快会超出模型的上下文长度。超过阈值时，较早的消息会被改写成一份笔记（内容要求见“提示词
          ·
          上下文压缩”），最近的消息原样保留；角色的系统提示词和当前任务始终保留。
        </p>
        <div className="grid gap-4 md:grid-cols-3">
          <SwitchField
            label="超长时压缩上下文"
            hint="关闭后超出上下文长度会直接报错"
            checked={draft.compaction.enabled}
            disabled={disabled}
            onChange={(value) =>
              onChange(edit(draft, (next) => (next.compaction.enabled = value)))
            }
          />
          <NumberField
            label="触发阈值（占上下文长度）"
            hint={`0.6 表示用到模型上下文的 60% 时压缩${contextWindow ? `，当前约 ${Math.round(contextWindow * draft.compaction.trigger_fraction).toLocaleString()} tokens` : ""}`}
            value={draft.compaction.trigger_fraction}
            min={0.05}
            max={1}
            step={0.05}
            disabled={disabled}
            onChange={(value) =>
              onChange(
                edit(
                  draft,
                  (next) => (next.compaction.trigger_fraction = value ?? 0.6),
                ),
              )
            }
          />
          <NumberField
            label="未声明上下文长度时的阈值（tokens）"
            value={draft.compaction.fallback_trigger_tokens}
            min={2000}
            max={2000000}
            step={1}
            disabled={disabled}
            onChange={(value) =>
              onChange(
                edit(
                  draft,
                  (next) =>
                    (next.compaction.fallback_trigger_tokens = value ?? 48000),
                ),
              )
            }
          />
          <NumberField
            label="压缩后保留的最近内容（占阈值比例）"
            hint={`0.4 表示保留阈值的 40%${contextWindow ? `，约 ${Math.round(contextWindow * draft.compaction.trigger_fraction * draft.compaction.keep_fraction).toLocaleString()} tokens` : ""}；留得越多，下一次压缩来得越快`}
            value={draft.compaction.keep_fraction}
            min={0.05}
            max={0.9}
            step={0.05}
            disabled={disabled}
            onChange={(value) =>
              onChange(
                edit(
                  draft,
                  (next) => (next.compaction.keep_fraction = value ?? 0.4),
                ),
              )
            }
          />
          <NumberField
            label="送给摘要模型的最大 tokens"
            value={draft.compaction.max_summary_input_tokens}
            min={1000}
            max={400000}
            step={1}
            disabled={disabled}
            onChange={(value) =>
              onChange(
                edit(
                  draft,
                  (next) =>
                    (next.compaction.max_summary_input_tokens = value ?? 24000),
                ),
              )
            }
          />
          <SelectField
            label="写摘要的模型"
            value={draft.compaction.model ?? "__role"}
            options={[
              { value: "__role", label: "跟随角色的模型" },
              ...draft.models.map((model) => ({
                value: model.name,
                label: firstText(model.display_name, model.name),
              })),
            ]}
            disabled={disabled}
            onChange={(value) =>
              onChange(
                edit(
                  draft,
                  (next) =>
                    (next.compaction.model = value === "__role" ? null : value),
                ),
              )
            }
          />
        </div>
      </section>
      <section className="space-y-2 rounded-xl border p-4">
        <h3 className="font-medium">默认引擎工具</h3>
        <p className="text-muted-foreground text-xs">
          研究员除数据源外还能使用的引擎工具；角色可在自己的工具列表里另行指定。
        </p>
        <div className="flex flex-wrap gap-2">
          {view.catalog.engine_tools.map((tool) => (
            <label
              key={tool.name}
              className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
              title={tool.description}
            >
              <input
                type="checkbox"
                checked={draft.engine_tools.includes(tool.name)}
                disabled={disabled}
                onChange={(event) =>
                  set(
                    "engine_tools",
                    event.target.checked
                      ? [...draft.engine_tools, tool.name]
                      : draft.engine_tools.filter((name) => name !== tool.name),
                  )
                }
              />
              <span className="font-mono">{tool.name}</span>
              <span className="text-muted-foreground">{tool.description}</span>
            </label>
          ))}
        </div>
      </section>
      <section className="space-y-2 rounded-xl border p-4 text-sm">
        <h3 className="font-medium">部署上限（只读，由运维配置文件决定）</h3>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs md:grid-cols-4">
          {(
            [
              [
                "运行模式",
                view.operator.runner === "demo"
                  ? "演示（合成数据）"
                  : "真实研究",
              ],
              ["同时运行的研究任务", view.operator.max_active_runs],
              ["补研轮数上限", ceiling.max_iterations],
              ["研究单元上限", ceiling.max_units],
              ["工具调用上限", ceiling.max_tool_calls ?? "不限"],
              ["执行时长上限（秒）", ceiling.max_elapsed_seconds ?? "不限"],
              [
                "Token 上限",
                ceiling.max_model_tokens?.toLocaleString() ?? "不限",
              ],
              ["网站图标代取", view.operator.favicons ? "开启" : "关闭"],
            ] as const
          ).map(([term, value]) => (
            <div key={term} className="space-y-0.5">
              <dt className="text-muted-foreground">{term}</dt>
              <dd className="tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

export function SecretsSection({
  view,
  api,
  disabled,
  dirty,
  onSecrets,
  onRestored,
  onReset,
}: {
  view: SettingsView;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
  /** Unsaved edits exist; server-side reset and restore would discard them. */
  dirty: boolean;
  /** A secret was saved or deleted: only the secret names and statuses changed. */
  onSecrets: (view: SettingsView) => void;
  /** An earlier version was restored: the answer replaces the whole form. */
  onRestored: (view: SettingsView) => void;
  onReset: (fields?: string[]) => void;
}) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const history = useQuery({
    queryKey: ["research-settings-history", api.root, view.version],
    queryFn: api.settingsHistory,
    retry: false,
  });
  const save = (secret: string, secretValue: string | null) => {
    setBusy(true);
    void api
      .saveSecret(secret, secretValue)
      .then((next) => {
        onSecrets(next);
        toast.success(
          secretValue ? `已保存密钥 ${secret}` : `已删除密钥 ${secret}`,
        );
        setName("");
        setValue("");
      })
      .catch((error: unknown) =>
        toast.error(error instanceof Error ? error.message : String(error)),
      )
      .finally(() => setBusy(false));
  };
  const references = Object.entries(view.secrets.references);
  return (
    <div className="space-y-6">
      <section className="space-y-3 rounded-xl border p-4">
        <h3 className="flex items-center gap-2 font-medium">
          <KeyRound className="size-4" />
          保存的密钥
        </h3>
        <p className="text-muted-foreground text-xs">
          密钥只写不读：保存后页面和接口都不会再返回它的值。在模型或供应商里用{" "}
          <code>secret:名字</code> 引用。也可以继续使用 <code>$环境变量</code>。
        </p>
        <ul className="space-y-1 text-sm">
          {view.secrets.saved.map((secret) => (
            <li
              key={secret}
              className="flex items-center gap-2 rounded-md border px-3 py-1.5"
            >
              <span className="font-mono text-xs">secret:{secret}</span>
              <Badge
                variant="outline"
                className="text-emerald-600 dark:text-emerald-400"
              >
                已保存
              </Badge>
              <Button
                variant="ghost"
                size="icon-sm"
                className="ml-auto"
                aria-label={`删除密钥 ${secret}`}
                disabled={disabled || busy}
                onClick={() => save(secret, null)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </li>
          ))}
          {!view.secrets.saved.length && (
            <li className="text-muted-foreground text-xs">
              还没有保存的密钥。
            </li>
          )}
        </ul>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value.trim())}
            placeholder="名字，例如 bocha-key"
            aria-label="密钥名字"
            className="w-56 font-mono text-xs"
            disabled={disabled}
          />
          <Input
            value={value}
            type="password"
            autoComplete="new-password"
            onChange={(event) => setValue(event.target.value)}
            placeholder="密钥值"
            aria-label="密钥值"
            className="w-72 text-xs"
            disabled={disabled}
          />
          <Button
            variant="outline"
            disabled={disabled || busy || !IDENTIFIER.test(name) || !value}
            onClick={() => save(name, value)}
          >
            <Plus className="size-4" />
            保存密钥
          </Button>
        </div>
      </section>
      <section className="space-y-2 rounded-xl border p-4">
        <h3 className="font-medium">当前设置用到的凭据</h3>
        <ul className="grid gap-1 text-xs md:grid-cols-2">
          {references.map(([reference, status]) => (
            <li key={reference} className="flex items-center gap-2">
              <code>{reference}</code>
              <Badge variant={status === "set" ? "outline" : "destructive"}>
                {status === "set" ? "已找到值" : "未找到值"}
              </Badge>
            </li>
          ))}
          {!references.length && (
            <li className="text-muted-foreground">没有引用凭据。</li>
          )}
        </ul>
      </section>
      <section className="space-y-2 rounded-xl border p-4">
        <div className="flex items-center gap-2">
          <h3 className="font-medium">修改历史</h3>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            disabled={disabled || dirty || !view.overridden.length}
            onClick={() => onReset()}
          >
            <RotateCcw className="size-3.5" />
            全部恢复为配置文件的设置
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          当前第 {view.version} 版
          {view.updated_at
            ? `，保存于 ${new Date(view.updated_at).toLocaleString()}`
            : ""}
          。已修改的部分：
          {view.overridden.length
            ? fieldLabels(view.overridden)
            : "无（与配置文件一致）"}
          。每个研究任务记住创建时的版本，修改只影响之后新建的研究。
        </p>
        {dirty && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            页面上有未保存的修改，先保存或放弃后才能恢复历史版本。
          </p>
        )}
        <ul className="space-y-1 text-xs">
          {history.data?.items.map((item) => (
            <li
              key={item.version}
              className="flex items-center gap-2 rounded-md border px-3 py-1.5"
            >
              <span className="tabular-nums">第 {item.version} 版</span>
              <span className="text-muted-foreground">
                {new Date(item.updated_at).toLocaleString()}
              </span>
              <span className="text-muted-foreground">
                {item.updated_by ?? ""}
              </span>
              <span className="min-w-0 truncate">
                {fieldLabels(item.fields) || "恢复为配置文件"}
              </span>
              {item.version !== view.version && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto h-7 text-xs"
                  disabled={disabled || dirty}
                  onClick={() =>
                    void api
                      .restoreSettings(view.version, item.version)
                      .then((next) => {
                        onRestored(next);
                        toast.success(`已恢复第 ${item.version} 版的设置`);
                      })
                      .catch((error: unknown) =>
                        toast.error(
                          error instanceof Error
                            ? error.message
                            : String(error),
                        ),
                      )
                  }
                >
                  恢复此版本
                </Button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
