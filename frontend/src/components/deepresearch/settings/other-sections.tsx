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
import { edit, fieldLabels, IDENTIFIER } from "@/core/deepresearch/settings";
import type {
  EditableSettings,
  McpServerSpec,
  SettingsView,
} from "@/core/deepresearch/types";

import {
  JsonField,
  ListField,
  NumberField,
  SelectField,
  SwitchField,
  TextField,
} from "./fields";

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
  const [tools, setTools] = useState<
    | {
        name: string;
        description: string;
        arguments: Record<string, unknown>;
      }[]
    | null
  >(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const update = (change: (target: McpServerSpec) => void) =>
    onChange(edit(draft, (next) => change(next.mcp_servers[name]!)));
  const remote = server.transport !== "stdio";
  const used = draft.sources.some(
    (source) =>
      source.server === name ||
      source.providers.some((provider) => provider.server === name),
  );
  return (
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
              hint="值可以写 $环境变量 或 secret:名字"
              value={server.headers ?? {}}
              disabled={disabled}
              onChange={(value) =>
                update(
                  (target) =>
                    (target.headers = (value ?? {}) as Record<string, string>),
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
              onChange={(value) => update((target) => (target.command = value))}
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
              hint="值可以写 $环境变量 或 secret:名字"
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
          onChange={(value) => update((target) => (target.description = value))}
        />
      </div>
      <div className="space-y-2">
        <Button
          variant="outline"
          size="sm"
          disabled={loading}
          onClick={() => {
            setLoading(true);
            setError("");
            void api
              .mcpTools(name, server)
              .then((result) => {
                setTools(result.tools);
                setError(
                  result.ok ? "" : `连接失败（${result.error ?? "未知错误"}）`,
                );
              })
              .catch((failure: unknown) =>
                setError(
                  failure instanceof Error ? failure.message : String(failure),
                ),
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
        {error && <p className="text-destructive text-xs">{error}</p>}
        {tools && !error && (
          <ul className="space-y-1 text-xs">
            {tools.map((tool) => (
              <li key={tool.name} className="rounded-md border p-2">
                <span className="font-mono font-medium">{tool.name}</span>
                <span className="text-muted-foreground">
                  {" "}
                  · 参数：{Object.keys(tool.arguments).join("、") || "无"}
                </span>
                {tool.description && (
                  <p className="text-muted-foreground mt-1 line-clamp-2">
                    {tool.description}
                  </p>
                )}
              </li>
            ))}
            {!tools.length && (
              <li className="text-muted-foreground">服务没有提供工具。</li>
            )}
          </ul>
        )}
      </div>
    </section>
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
        <McpServerCard
          key={serverName}
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
            placeholder="例如 company-kb"
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
          hint="也是同时写作的章节数；本地模型建议 1-2"
          value={draft.max_concurrency}
          min={1}
          max={8}
          disabled={disabled}
          onChange={(value) => set("max_concurrency", value ?? 3)}
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
          label="单次输出上限（tokens）"
          hint="实际取它和模型上限的较小值"
          value={draft.max_output_tokens}
          min={128}
          max={393216}
          disabled={disabled}
          onChange={(value) => set("max_output_tokens", value ?? 4096)}
        />
        <NumberField
          label="整理失败重试次数"
          value={draft.output_retries}
          min={0}
          max={4}
          disabled={disabled}
          onChange={(value) => set("output_retries", value ?? 2)}
        />
        <NumberField
          label="报告最多章节数"
          value={draft.max_report_sections}
          min={2}
          max={12}
          disabled={disabled}
          onChange={(value) => set("max_report_sections", value ?? 8)}
        />
        <NumberField
          label="引用修复次数"
          value={draft.max_synthesis_repairs}
          min={0}
          max={3}
          disabled={disabled}
          onChange={(value) => set("max_synthesis_repairs", value ?? 1)}
        />
      </div>
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
  onView,
  onReset,
}: {
  view: SettingsView;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
  /** Unsaved edits exist; server-side reset and restore would discard them. */
  dirty: boolean;
  onView: (view: SettingsView) => void;
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
        onView(next);
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
                        onView(next);
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
