"use client";

import { Loader2, Plug, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { researchApi } from "@/core/deepresearch/api";
import { firstText } from "@/core/deepresearch/presentation";
import { edit, newModel } from "@/core/deepresearch/settings";
import type {
  EditableSettings,
  ModelProbe,
  ModelSpec,
  SettingsView,
} from "@/core/deepresearch/types";

import {
  JsonField,
  NumberField,
  SecretRefField,
  SelectField,
  SwitchField,
  TextField,
} from "./fields";

const PROVIDER_ENV: Record<string, string> = {
  openai: "OPENAI_API_KEY",
  deepseek: "DEEPSEEK_API_KEY",
  vllm: "LOCAL_MODEL_API_KEY",
  anthropic: "ANTHROPIC_API_KEY",
  custom: "MODEL_API_KEY",
};

function ProbeResult({ probe }: { probe: ModelProbe }) {
  return (
    <div
      role="status"
      className={
        probe.ok
          ? "rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3 text-xs"
          : "border-destructive/40 bg-destructive/5 rounded-lg border p-3 text-xs"
      }
    >
      <p className="mb-1 font-medium">
        {probe.ok ? "可以用于研究" : "暂时不能用于研究"}
      </p>
      {probe.error && (
        <p className="text-destructive break-all">{probe.error}</p>
      )}
      {probe.plain_reply && (
        <p>
          普通回复 {probe.plain_reply.seconds}s：
          {probe.plain_reply.text || "（空）"}
        </p>
      )}
      {probe.tool_call && (
        <p>
          工具调用 {probe.tool_call.seconds}s：
          {probe.tool_call.tools.length
            ? probe.tool_call.tools.join("、")
            : "没有调用工具"}
        </p>
      )}
      {probe.usage && (
        <p className="text-muted-foreground">
          用量上报：
          {probe.usage.total_tokens == null
            ? "未上报"
            : `${probe.usage.total_tokens} tokens`}
        </p>
      )}
      {probe.warnings?.map((warning) => (
        <p key={warning} className="text-amber-600 dark:text-amber-400">
          {warning}
        </p>
      ))}
    </div>
  );
}

function ModelCard({
  model,
  index,
  view,
  draft,
  onChange,
  api,
  disabled,
}: {
  model: ModelSpec;
  index: number;
  view: SettingsView;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  api: ReturnType<typeof researchApi>;
  disabled: boolean;
}) {
  const [probe, setProbe] = useState<ModelProbe | null>(null);
  const [testing, setTesting] = useState(false);
  const provider = view.catalog.model_providers.find(
    (item) => item.id === model.provider,
  );
  const update = (change: (target: ModelSpec, all: EditableSettings) => void) =>
    onChange(
      edit(draft, (next) => {
        change(next.models[index]!, next);
      }),
    );
  const pricing = draft.pricing[model.name];
  return (
    <section
      className="space-y-4 rounded-xl border p-4"
      aria-label={`模型 ${model.name}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">
          {firstText(model.display_name, model.name)}
        </h3>
        <Badge variant="secondary">{provider?.label ?? model.provider}</Badge>
        {draft.default_model === model.name && <Badge>默认</Badge>}
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            disabled={testing || !model.model}
            onClick={() => {
              setTesting(true);
              setProbe(null);
              void api
                .testModel(model)
                .then(setProbe)
                .catch((error: unknown) =>
                  setProbe({
                    model: model.name,
                    ok: false,
                    error:
                      error instanceof Error ? error.message : String(error),
                  }),
                )
                .finally(() => setTesting(false));
            }}
          >
            {testing ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Plug className="size-3.5" />
            )}
            测试连接
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`删除模型 ${model.name}`}
            disabled={disabled}
            onClick={() =>
              onChange(
                edit(draft, (next) => {
                  next.models.splice(index, 1);
                  delete next.pricing[model.name];
                  for (const field of [
                    "default_model",
                    "rewrite_model",
                    "extraction_model",
                  ] as const)
                    if (next[field] === model.name) next[field] = null;
                  for (const role of Object.values(next.skills))
                    if (role.model === model.name) role.model = null;
                }),
              )
            }
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      </header>
      <p className="text-muted-foreground text-xs">
        “测试连接”使用表单里当前（未保存）的配置，发送一次普通请求和一次工具调用。
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <TextField
          label="名称"
          hint="角色和其他设置通过这个名称引用模型"
          value={model.name}
          disabled={disabled}
          mono
          onChange={(value) =>
            update((target, next) => {
              const old = target.name;
              target.name = value;
              for (const field of [
                "default_model",
                "rewrite_model",
                "extraction_model",
              ] as const)
                if (next[field] === old) next[field] = value;
              for (const role of Object.values(next.skills))
                if (role.model === old) role.model = value;
              if (next.pricing[old]) {
                next.pricing[value] = next.pricing[old];
                delete next.pricing[old];
              }
            })
          }
        />
        <TextField
          label="显示名称"
          value={model.display_name}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.display_name = value))
          }
        />
        <SelectField
          label="服务类型"
          hint={provider?.hint}
          value={model.provider}
          disabled={disabled}
          options={view.catalog.model_providers.map((item) => ({
            value: item.id,
            label: item.label,
          }))}
          onChange={(value) =>
            update(
              (target) => (target.provider = value as ModelSpec["provider"]),
            )
          }
        />
        <TextField
          label="服务端模型名"
          hint="例如 deepseek-v4-flash；vLLM 为 --served-model-name"
          value={model.model}
          disabled={disabled}
          mono
          onChange={(value) => update((target) => (target.model = value))}
        />
        {model.provider === "custom" && (
          <TextField
            label="模型类（use）"
            hint="例如 langchain_openai:ChatOpenAI"
            value={model.use}
            disabled={disabled}
            mono
            onChange={(value) =>
              update((target) => (target.use = value || null))
            }
          />
        )}
        <TextField
          label="接口地址（base_url）"
          hint="留空使用该服务类型的默认地址"
          placeholder={provider?.base_url ?? ""}
          value={model.base_url}
          disabled={disabled}
          mono
          onChange={(value) =>
            update((target) => (target.base_url = value || null))
          }
        />
        <SecretRefField
          label="API Key"
          value={model.api_key}
          disabled={disabled}
          statuses={view.secrets.references}
          saved={view.secrets.saved}
          suggestedEnv={PROVIDER_ENV[model.provider]}
          onChange={(value) => update((target) => (target.api_key = value))}
        />
        <NumberField
          label="单次最大输出 tokens"
          hint="不能超过服务端允许的上限"
          value={model.max_tokens}
          min={128}
          max={393216}
          nullable
          disabled={disabled}
          onChange={(value) => update((target) => (target.max_tokens = value))}
        />
        <NumberField
          label="上下文长度"
          value={model.context_window}
          min={1024}
          nullable
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.context_window = value))
          }
        />
        <NumberField
          label="温度"
          value={model.temperature}
          min={0}
          max={2}
          step={0.1}
          nullable
          disabled={disabled}
          onChange={(value) => update((target) => (target.temperature = value))}
        />
        <NumberField
          label="请求超时（秒）"
          value={model.timeout_seconds}
          min={1}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.timeout_seconds = value ?? 600))
          }
        />
        <NumberField
          label="失败重试次数"
          value={model.max_retries}
          min={0}
          max={10}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.max_retries = value ?? 2))
          }
        />
      </div>
      <SwitchField
        label="支持思考模式开关"
        hint="研究总是关闭思考以节省时间；DeepSeek、Qwen3（vLLM）等模型需要打开此项才会发送关闭开关"
        checked={Boolean(model.supports_thinking)}
        disabled={disabled}
        onChange={(value) =>
          update((target) => (target.supports_thinking = value))
        }
      />
      <details className="rounded-lg border p-3">
        <summary className="cursor-pointer text-sm font-medium">
          高级：额外构造参数与费用单价
        </summary>
        <div className="mt-3 grid gap-4 md:grid-cols-2">
          <JsonField
            label="额外参数（extra）"
            hint="原样传给模型类，例如 extra_body、default_headers"
            value={model.extra ?? {}}
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.extra = value ?? {}))
            }
          />
          <div className="grid grid-cols-2 gap-3">
            <NumberField
              label="输入 / 百万 tokens"
              value={pricing?.input_per_million}
              min={0}
              step={0.01}
              nullable
              disabled={disabled}
              onChange={(value) =>
                onChange(
                  edit(draft, (next) => {
                    if (value == null) delete next.pricing[model.name];
                    else
                      next.pricing[model.name] = {
                        input_per_million: value,
                        output_per_million:
                          next.pricing[model.name]?.output_per_million ?? 0,
                        cached_input_per_million:
                          next.pricing[model.name]?.cached_input_per_million ??
                          null,
                        currency: next.pricing[model.name]?.currency ?? "CNY",
                      };
                  }),
                )
              }
            />
            <NumberField
              label="输出 / 百万 tokens"
              value={pricing?.output_per_million}
              min={0}
              step={0.01}
              nullable
              disabled={disabled || !pricing}
              onChange={(value) =>
                onChange(
                  edit(
                    draft,
                    (next) =>
                      (next.pricing[model.name]!.output_per_million =
                        value ?? 0),
                  ),
                )
              }
            />
            <NumberField
              label="缓存命中输入 / 百万"
              value={pricing?.cached_input_per_million}
              min={0}
              step={0.01}
              nullable
              disabled={disabled || !pricing}
              onChange={(value) =>
                onChange(
                  edit(
                    draft,
                    (next) =>
                      (next.pricing[model.name]!.cached_input_per_million =
                        value),
                  ),
                )
              }
            />
            <TextField
              label="币种"
              value={pricing?.currency}
              disabled={disabled || !pricing}
              onChange={(value) =>
                onChange(
                  edit(
                    draft,
                    (next) => (next.pricing[model.name]!.currency = value),
                  ),
                )
              }
            />
          </div>
        </div>
      </details>
      {probe && <ProbeResult probe={probe} />}
    </section>
  );
}

export function ModelsSection({
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
  const options = draft.models.map((model) => ({
    value: model.name,
    label: model.display_name
      ? `${model.display_name}（${model.name}）`
      : model.name,
  }));
  return (
    <div className="space-y-6">
      <p className="text-muted-foreground text-sm">
        研究只使用这里的模型，与 DeerFlow
        自己的模型列表无关。每个研究任务会记住创建时的模型设置。
      </p>
      <div className="grid gap-4 md:grid-cols-3">
        <SelectField
          label="默认模型"
          hint="没有单独指定模型的角色都用它"
          value={draft.default_model ?? undefined}
          options={options}
          disabled={disabled}
          onChange={(value) =>
            onChange(edit(draft, (next) => (next.default_model = value)))
          }
        />
        <SelectField
          label="请求改写模型"
          hint="把对话改写成研究请求；可选更快的模型"
          value={draft.rewrite_model ?? "__default"}
          options={[{ value: "__default", label: "跟随默认模型" }, ...options]}
          disabled={disabled}
          onChange={(value) =>
            onChange(
              edit(
                draft,
                (next) =>
                  (next.rewrite_model = value === "__default" ? null : value),
              ),
            )
          }
        />
        <SelectField
          label="笔记整理模型"
          hint="把研究笔记整理成结构化发现；选最稳的模型"
          value={draft.extraction_model ?? "__role"}
          options={[{ value: "__role", label: "跟随角色的模型" }, ...options]}
          disabled={disabled}
          onChange={(value) =>
            onChange(
              edit(
                draft,
                (next) =>
                  (next.extraction_model = value === "__role" ? null : value),
              ),
            )
          }
        />
      </div>
      {draft.models.map((model, index) => (
        <ModelCard
          key={index}
          model={model}
          index={index}
          view={view}
          draft={draft}
          onChange={onChange}
          api={api}
          disabled={disabled}
        />
      ))}
      <Button
        variant="outline"
        disabled={disabled}
        onClick={() =>
          onChange(
            edit(draft, (next) => {
              const model = newModel(next.models);
              next.models.push(model);
              next.default_model ??= model.name;
            }),
          )
        }
      >
        <Plus className="size-4" />
        添加模型
      </Button>
    </div>
  );
}
