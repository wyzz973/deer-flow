"use client";

import {
  ArrowDown,
  ArrowUp,
  FlaskConical,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { researchApi } from "@/core/deepresearch/api";
import {
  edit,
  newProvider,
  newSource,
  providerTypesFor,
  uniqueName,
} from "@/core/deepresearch/settings";
import type {
  EditableSettings,
  ProviderProbe,
  ProviderSpec,
  SettingsView,
  SourceSpec,
} from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import {
  JsonField,
  NumberField,
  SecretRefField,
  SelectField,
  SwitchField,
  TextField,
} from "./fields";

const ROLE_LABELS: Record<SourceSpec["role"], string> = {
  search: "结果只用于发现来源，需打开原文后才能引用",
  read: "打开网页或文档，读到的原文可以引用",
  data: "返回的记录可以直接引用",
};
const ROLE_SHORT: Record<SourceSpec["role"], string> = {
  search: "搜索",
  read: "阅读",
  data: "知识库",
};
const ERROR_KINDS: Record<string, string> = {
  rate_limit: "限流",
  quota: "额度用尽",
  auth: "鉴权失败",
  timeout: "超时",
  network: "网络错误",
  server: "服务异常",
  config: "配置缺失",
  invalid: "请求无效",
  not_found: "页面不存在",
  blocked: "页面拒绝访问",
  empty: "无结果",
  unsupported: "不支持的内容",
};

function move<T>(items: T[], index: number, offset: number) {
  const target = index + offset;
  if (target < 0 || target >= items.length) return;
  [items[index], items[target]] = [items[target]!, items[index]!];
}

function ProviderCard({
  source,
  sourceIndex,
  provider,
  index,
  view,
  draft,
  onChange,
  api,
  disabled,
}: {
  source: SourceSpec;
  sourceIndex: number;
  provider: ProviderSpec;
  index: number;
  view: SettingsView;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
}) {
  const [probeInput, setProbeInput] = useState("");
  const [probe, setProbe] = useState<ProviderProbe | null>(null);
  const [testing, setTesting] = useState(false);
  const preset = view.catalog.source_providers.find(
    (item) => item.type === provider.type,
  );
  const health = view.health.find(
    (item) => item.source === source.name && item.provider === provider.id,
  );
  const update = (change: (target: ProviderSpec, parent: SourceSpec) => void) =>
    onChange(
      edit(draft, (next) => {
        const parent = next.sources[sourceIndex]!;
        change(parent.providers[index]!, parent);
      }),
    );
  const servers = Object.keys(draft.mcp_servers);
  return (
    <div
      className={cn(
        "space-y-3 rounded-lg border p-3",
        provider.enabled === false && "opacity-60",
      )}
      aria-label={`供应商 ${provider.id}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-xs tabular-nums">
          第 {index + 1} 顺位
        </span>
        <span className="font-mono text-sm">{provider.id}</span>
        <Badge variant="secondary">{preset?.label ?? provider.type}</Badge>
        {health?.cooling ? (
          <Badge variant="destructive" title={health.last_error}>
            冷却中 {health.cooldown_seconds}s ·{" "}
            {ERROR_KINDS[health.last_error_kind ?? ""] ??
              health.last_error_kind}
          </Badge>
        ) : health?.last_error_kind && health.failures > 0 ? (
          <Badge variant="outline" title={health.last_error}>
            最近失败：
            {ERROR_KINDS[health.last_error_kind] ?? health.last_error_kind}
            （成功 {health.successes} / 失败 {health.failures}）
          </Badge>
        ) : health?.successes ? (
          <Badge variant="outline">
            成功 {health.successes} 次
            {health.last_latency_ms ? ` · ${health.last_latency_ms}ms` : ""}
          </Badge>
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          <Switch
            checked={provider.enabled !== false}
            disabled={disabled}
            aria-label={`启用供应商 ${provider.id}`}
            onCheckedChange={(value) =>
              update((target) => (target.enabled = value))
            }
          />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="提前"
            disabled={disabled || index === 0}
            onClick={() =>
              update((_target, parent) => move(parent.providers, index, -1))
            }
          >
            <ArrowUp className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="延后"
            disabled={disabled || index === source.providers.length - 1}
            onClick={() =>
              update((_target, parent) => move(parent.providers, index, 1))
            }
          >
            <ArrowDown className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`删除供应商 ${provider.id}`}
            disabled={disabled}
            onClick={() =>
              update((_target, parent) => parent.providers.splice(index, 1))
            }
          >
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <TextField
          label="供应商 ID"
          value={provider.id}
          mono
          disabled={disabled}
          onChange={(value) => update((target) => (target.id = value))}
        />
        <SelectField
          label="类型"
          value={provider.type}
          disabled={disabled}
          options={providerTypesFor(source.role, view).map((item) => ({
            value: item.type,
            label: item.label,
          }))}
          onChange={(value) => update((target) => (target.type = value))}
        />
        {provider.type !== "mcp" &&
          provider.type !== "duckduckgo" &&
          provider.type !== "direct" && (
            <SecretRefField
              label={preset?.requires_key ? "API Key" : "API Key（可选）"}
              value={provider.api_key}
              disabled={disabled}
              statuses={view.secrets.references}
              saved={view.secrets.saved}
              suggestedEnv={preset?.key_env}
              onChange={(value) => update((target) => (target.api_key = value))}
            />
          )}
        {provider.type !== "mcp" &&
          provider.type !== "http" &&
          provider.type !== "duckduckgo" &&
          provider.type !== "direct" && (
            <TextField
              label="接口地址"
              hint={preset?.base_url ? "留空使用官方地址" : "自建服务必填"}
              placeholder={preset?.base_url ?? "http://127.0.0.1:8888"}
              value={provider.base_url}
              mono
              disabled={disabled}
              onChange={(value) =>
                update((target) => (target.base_url = value || null))
              }
            />
          )}
        <NumberField
          label="超时（秒）"
          value={provider.timeout_seconds}
          min={1}
          max={600}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.timeout_seconds = value ?? 30))
          }
        />
      </div>
      {provider.type === "http" && (
        <div className="grid gap-3 md:grid-cols-2">
          <SelectField
            label="请求方法"
            value={provider.method ?? "GET"}
            options={[
              { value: "GET", label: "GET" },
              { value: "POST", label: "POST" },
            ]}
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.method = value as "GET" | "POST"))
            }
          />
          <TextField
            label="接口地址（url）"
            hint="可用 {query}、{url}、{max_results}、{time_range}"
            value={provider.url}
            mono
            disabled={disabled}
            onChange={(value) => update((target) => (target.url = value))}
          />
          <JsonField
            label="请求头（headers）"
            hint="值可以写 $环境变量 或 secret:名字"
            value={provider.headers ?? {}}
            disabled={disabled}
            onChange={(value) =>
              update(
                (target) =>
                  (target.headers = (value ?? {}) as Record<string, string>),
              )
            }
          />
          <JsonField
            label="查询参数（params）"
            value={provider.params ?? {}}
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.params = value ?? {}))
            }
          />
          <JsonField
            label="请求体（body，POST）"
            nullable
            value={provider.body}
            disabled={disabled}
            onChange={(value) => update((target) => (target.body = value))}
          />
        </div>
      )}
      {provider.type === "mcp" && (
        <div className="grid gap-3 md:grid-cols-2">
          <SelectField
            label="MCP 服务"
            hint={servers.length ? undefined : "先在“MCP 服务”中添加服务"}
            value={provider.server ?? undefined}
            options={servers.map((name) => ({ value: name, label: name }))}
            disabled={disabled}
            onChange={(value) => update((target) => (target.server = value))}
          />
          <TextField
            label="MCP 工具原名"
            value={provider.tool}
            mono
            disabled={disabled}
            onChange={(value) => update((target) => (target.tool = value))}
          />
          <JsonField
            label="参数模板（可选）"
            hint="留空时按 query/q/url 等常见参数名自动对应"
            value={provider.arguments ?? {}}
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.arguments = value ?? {}))
            }
          />
        </div>
      )}
      {!["http", "mcp", "direct"].includes(provider.type) && (
        <JsonField
          label="预设选项（options）"
          hint="例如 Tavily 的 search_depth、RAGFlow 的 dataset_ids、LightRAG 的 mode"
          value={provider.options ?? {}}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.options = value ?? {}))
          }
        />
      )}
      {provider.type === "direct" && (
        <SwitchField
          label="允许访问内网地址"
          hint="只在读取公司内网文档时打开；默认拒绝私有地址以防服务端请求伪造"
          checked={Boolean(provider.allow_private_network)}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.allow_private_network = value))
          }
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={probeInput}
          onChange={(event) => setProbeInput(event.target.value)}
          placeholder={
            source.role === "read"
              ? "测试网址，默认 https://example.com/"
              : "测试查询，默认 DeerFlow deep research"
          }
          aria-label={`测试 ${provider.id} 的输入`}
          className="h-8 max-w-sm text-xs"
        />
        <Button
          variant="outline"
          size="sm"
          disabled={testing}
          onClick={() => {
            setTesting(true);
            setProbe(null);
            void api
              .testProvider(
                source,
                provider.id,
                source.role === "read"
                  ? { url: probeInput || undefined }
                  : { query: probeInput || undefined },
                draft.mcp_servers,
              )
              .then(setProbe)
              .catch((error: unknown) =>
                setProbe({
                  ok: false,
                  ms: 0,
                  kind: "request",
                  message:
                    error instanceof Error ? error.message : String(error),
                }),
              )
              .finally(() => setTesting(false));
          }}
        >
          {testing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <FlaskConical className="size-3.5" />
          )}
          测试这个供应商
        </Button>
      </div>
      {probe && (
        <div
          role="status"
          className={cn(
            "rounded-md border p-2 text-xs",
            probe.ok
              ? "border-emerald-500/40 bg-emerald-500/5"
              : "border-destructive/40 bg-destructive/5",
          )}
        >
          <p className="font-medium">
            {probe.ok
              ? `成功 · ${probe.ms}ms · ${probe.count ?? 0} 条`
              : `失败 · ${ERROR_KINDS[probe.kind ?? ""] ?? probe.kind ?? ""}`}
          </p>
          {probe.message && <p className="break-all">{probe.message}</p>}
          {probe.sample?.map((item, sample) => (
            <p key={sample} className="text-muted-foreground truncate">
              {item.title}{" "}
              {item.url && <span className="font-mono">{item.url}</span>}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function SourceCard({
  source,
  index,
  view,
  draft,
  onChange,
  api,
  disabled,
}: {
  source: SourceSpec;
  index: number;
  view: SettingsView;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
}) {
  const [adding, setAdding] = useState("");
  const kind = source.kind ?? "channel";
  const update = (change: (target: SourceSpec) => void) =>
    onChange(edit(draft, (next) => change(next.sources[index]!)));
  const types = providerTypesFor(source.role, view);
  return (
    <section
      className="space-y-4 rounded-xl border p-4"
      aria-label={`数据源 ${source.name}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">{source.name}</h3>
        <Badge variant="secondary">工具 {source.tool}</Badge>
        <Badge variant="outline">{ROLE_SHORT[source.role]}</Badge>
        <Badge variant="outline">
          {source.origin === "internal" ? "内部" : "外部"}
        </Badge>
        {kind !== "channel" && (
          <Badge variant="outline">
            {kind === "mcp" ? "MCP 工具" : "旧版宿主工具"}
          </Badge>
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          aria-label={`删除数据源 ${source.name}`}
          disabled={disabled}
          onClick={() =>
            onChange(
              edit(draft, (next) => {
                next.sources.splice(index, 1);
                next.source_fallback = next.source_fallback.filter(
                  (name) => name !== source.name,
                );
              }),
            )
          }
        >
          <Trash2 className="size-4" />
        </Button>
      </header>
      <div className="grid gap-3 md:grid-cols-3">
        <TextField
          label="数据源名称"
          value={source.name}
          mono
          disabled={disabled}
          onChange={(value) =>
            onChange(
              edit(draft, (next) => {
                next.source_fallback = next.source_fallback.map((name) =>
                  name === source.name ? value : name,
                );
                next.sources[index]!.name = value;
              }),
            )
          }
        />
        <TextField
          label="模型看到的工具名"
          hint="例如 web_search；同一部署内唯一"
          value={source.tool}
          mono
          disabled={disabled}
          onChange={(value) => update((target) => (target.tool = value))}
        />
        <SelectField
          label="作用"
          hint={ROLE_LABELS[source.role]}
          value={source.role}
          options={(Object.keys(ROLE_SHORT) as SourceSpec["role"][]).map(
            (role) => ({ value: role, label: ROLE_SHORT[role] }),
          )}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.role = value as SourceSpec["role"]))
          }
        />
        <SelectField
          label="来源类别"
          value={source.origin}
          options={[
            { value: "external", label: "外部资料" },
            { value: "internal", label: "内部资料" },
          ]}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.origin = value as SourceSpec["origin"]))
          }
        />
        <SelectField
          label="权威等级"
          hint="L1 最权威"
          value={source.level ?? "L4"}
          options={["L1", "L2", "L3", "L4"].map((level) => ({
            value: level,
            label: level,
          }))}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.level = value as SourceSpec["level"]))
          }
        />
        <NumberField
          label="优先级"
          hint="数字越小越靠前"
          value={source.priority ?? 100}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.priority = value ?? 100))
          }
        />
        <TextField
          label="发布方分类"
          value={source.publisher}
          disabled={disabled}
          onChange={(value) => update((target) => (target.publisher = value))}
        />
      </div>
      <TextField
        label="给模型的工具说明（可选）"
        hint="留空使用该作用的默认说明"
        value={source.description}
        disabled={disabled}
        className="w-full"
        onChange={(value) => update((target) => (target.description = value))}
      />
      {kind === "channel" ? (
        <div className="space-y-3">
          <p className="text-muted-foreground text-xs">
            供应商按顺序尝试：限流、额度用尽、鉴权失败或服务异常的供应商会自动冷却一段时间，由下一个接替；某个页面读不了只会换下一个供应商重试。
          </p>
          {source.providers.map((provider, providerIndex) => (
            <ProviderCard
              key={providerIndex}
              source={source}
              sourceIndex={index}
              provider={provider}
              index={providerIndex}
              view={view}
              draft={draft}
              onChange={onChange}
              api={api}
              disabled={disabled}
            />
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="要添加的供应商类型"
              className="border-input bg-background h-8 rounded-md border px-2 text-xs"
              value={adding}
              disabled={disabled}
              onChange={(event) => setAdding(event.target.value)}
            >
              <option value="">选择供应商类型…</option>
              {types.map((item) => (
                <option key={item.type} value={item.type}>
                  {item.label}
                </option>
              ))}
            </select>
            <Button
              variant="outline"
              size="sm"
              disabled={disabled || !adding}
              onClick={() => {
                update((target) =>
                  target.providers.push(newProvider(adding, target.providers)),
                );
                setAdding("");
              }}
            >
              <Plus className="size-3.5" />
              添加供应商
            </Button>
          </div>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          <SelectField
            label="MCP 服务"
            value={source.server ?? undefined}
            options={Object.keys(draft.mcp_servers).map((name) => ({
              value: name,
              label: name,
            }))}
            disabled={disabled || kind === "native"}
            onChange={(value) => update((target) => (target.server = value))}
          />
          <TextField
            label="MCP 工具原名"
            hint="留空时与工具名相同"
            value={source.mcp_tool}
            mono
            disabled={disabled || kind === "native"}
            onChange={(value) =>
              update((target) => (target.mcp_tool = value || null))
            }
          />
        </div>
      )}
    </section>
  );
}

export function SourcesSection({
  view,
  draft,
  onChange,
  api,
  disabled,
}: {
  view: SettingsView;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
}) {
  return (
    <div className="space-y-6">
      <p className="text-muted-foreground text-sm">
        每个数据源是研究员可以调用的一个工具。搜索结果只用于发现来源，阅读工具打开的原文和知识库记录才能被引用。这里的配置与
        DeerFlow 的工具列表无关。
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        <SwitchField
          label="要求内外部来源并存"
          hint="打开后每个研究单元都必须同时查内部与外部来源；只有一类来源时关闭"
          checked={draft.require_dual_source}
          disabled={disabled}
          onChange={(value) =>
            onChange(edit(draft, (next) => (next.require_dual_source = value)))
          }
        />
        <SwitchField
          label="允许引用搜索结果摘要"
          hint="只有搜索接口返回完整正文时才打开"
          checked={draft.cite_search_results}
          disabled={disabled}
          onChange={(value) =>
            onChange(edit(draft, (next) => (next.cite_search_results = value)))
          }
        />
      </div>
      {draft.sources.map((source, index) => (
        <SourceCard
          key={index}
          source={source}
          index={index}
          view={view}
          draft={draft}
          onChange={onChange}
          api={api}
          disabled={disabled}
        />
      ))}
      <div className="flex flex-wrap gap-2">
        {(["search", "read", "data"] as const).map((role) => (
          <Button
            key={role}
            variant="outline"
            disabled={disabled}
            onClick={() =>
              onChange(
                edit(draft, (next) =>
                  next.sources.push(newSource(role, next.sources)),
                ),
              )
            }
          >
            <Plus className="size-4" />
            添加{ROLE_SHORT[role]}源
          </Button>
        ))}
        <Button
          variant="outline"
          disabled={disabled || !Object.keys(draft.mcp_servers).length}
          onClick={() =>
            onChange(
              edit(draft, (next) => {
                const source = newSource("data", next.sources);
                const tool = uniqueName(
                  "mcp_tool",
                  next.sources.map((item) => item.tool),
                ).replace(/-/g, "_");
                next.sources.push({
                  ...source,
                  name: uniqueName(
                    "mcp-source",
                    next.sources.map((item) => item.name),
                  ),
                  kind: "mcp",
                  providers: [],
                  server: Object.keys(next.mcp_servers)[0] ?? null,
                  tool,
                });
              }),
            )
          }
        >
          <Plus className="size-4" />
          添加 MCP 工具源
        </Button>
      </div>
      <section className="space-y-2 rounded-xl border p-4">
        <h3 className="font-medium">计划未指定来源时的默认顺序</h3>
        <div className="flex flex-wrap gap-2">
          {draft.sources.map((source) => {
            const position = draft.source_fallback.indexOf(source.name);
            return (
              <label
                key={source.name}
                className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
              >
                <input
                  type="checkbox"
                  checked={position >= 0}
                  disabled={disabled}
                  onChange={() =>
                    onChange(
                      edit(draft, (next) => {
                        next.source_fallback =
                          position >= 0
                            ? next.source_fallback.filter(
                                (name) => name !== source.name,
                              )
                            : [...next.source_fallback, source.name];
                      }),
                    )
                  }
                />
                {position >= 0 && (
                  <span className="text-muted-foreground tabular-nums">
                    {position + 1}.
                  </span>
                )}
                {source.name}
              </label>
            );
          })}
        </div>
      </section>
    </div>
  );
}
