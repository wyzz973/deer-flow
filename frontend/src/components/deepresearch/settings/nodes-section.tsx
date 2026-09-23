"use client";

import { RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { firstText } from "@/core/deepresearch/presentation";
import {
  edit,
  editNode,
  JSON_MODE_NODES,
  NODE_DEFAULTS,
  NODE_LABELS,
  nodeSpec,
} from "@/core/deepresearch/settings";
import type {
  EditableSettings,
  NodeName,
  NodeSpec,
  SettingsView,
} from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import {
  FieldScope,
  JsonField,
  NumberField,
  SelectField,
  SwitchField,
} from "./fields";

type NodeInfo = SettingsView["catalog"]["nodes"][number];

function NodeCard({
  info,
  draft,
  onChange,
  disabled,
}: {
  info: NodeInfo;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  disabled: boolean;
}) {
  const node = nodeSpec(draft, info.name);
  const tuned = Boolean(draft.nodes?.[info.name]);
  const update = (change: (target: NodeSpec) => void) =>
    onChange(edit(draft, (next) => editNode(next, info.name, change)));
  // Only direct calls can ask for a JSON object. A switch left on elsewhere
  // (an operator file) stays visible so it can be turned off.
  const jsonMode = JSON_MODE_NODES.includes(info.name) || node.json_mode;
  const models = [
    { value: "__inherit", label: "继承（不单独指定）" },
    ...draft.models.map((model) => ({
      value: model.name,
      label: firstText(model.display_name, model.name),
    })),
  ];
  // Only a model that declares supports_thinking has a switch to send. The
  // node may inherit its model, so this is a warning, never a block.
  const reasoning = draft.models.filter((model) => model.supports_thinking);
  const thinkable = node.model
    ? reasoning.some((model) => model.name === node.model)
    : reasoning.length > 0;
  return (
    <FieldScope.Provider value={`节点 ${info.label}`}>
      <section
        className={cn(
          "space-y-4 rounded-xl border p-4",
          tuned && "border-primary/50",
        )}
        aria-label={`节点 ${info.label}`}
      >
        <header className="flex flex-wrap items-center gap-2">
          <h3 className="font-medium">{info.label}</h3>
          <code className="text-muted-foreground text-xs">{info.name}</code>
          {tuned ? (
            <Badge>已调整</Badge>
          ) : (
            <Badge variant="outline">全部继承</Badge>
          )}
          {!node.enabled && <Badge variant="destructive">已关闭</Badge>}
          <div className="ml-auto flex items-center gap-3">
            {(info.optional || !node.enabled) && (
              <label className="flex items-center gap-2 text-xs">
                启用
                <Switch
                  checked={node.enabled}
                  disabled={disabled}
                  aria-label={`启用节点 ${info.label}`}
                  onCheckedChange={(value) =>
                    update((target) => (target.enabled = value))
                  }
                />
              </label>
            )}
            {tuned && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                disabled={disabled}
                onClick={() =>
                  update((target) =>
                    Object.assign(target, structuredClone(NODE_DEFAULTS)),
                  )
                }
              >
                <RotateCcw className="size-3.5" />
                恢复继承
              </Button>
            )}
          </div>
        </header>
        <p className="text-muted-foreground text-sm">{info.description}</p>
        <p className="text-muted-foreground text-xs leading-5">
          调参建议：{info.advice}
        </p>
        <div
          className={cn(
            "grid gap-4 md:grid-cols-3",
            !node.enabled && "opacity-60",
          )}
        >
          <SelectField
            label="模型"
            hint={`未指定模型时使用：${info.model_fallback}`}
            value={node.model ?? "__inherit"}
            options={models}
            disabled={disabled}
            onChange={(value) =>
              update(
                (target) =>
                  (target.model = value === "__inherit" ? null : value),
              )
            }
          />
          <NumberField
            label="温度"
            hint="0–2；留空使用模型自己的设置"
            value={node.temperature}
            min={0}
            max={2}
            step={0.1}
            nullable
            placeholder="继承"
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.temperature = value))
            }
          />
          <NumberField
            label="top_p"
            hint="大于 0、不超过 1；留空使用模型自己的设置"
            value={node.top_p}
            min={0.01}
            max={1}
            step={0.05}
            nullable
            placeholder="继承"
            disabled={disabled}
            onChange={(value) => update((target) => (target.top_p = value))}
          />
          <NumberField
            label="单次输出上限（max_tokens）"
            hint={`留空使用“运行参数”里的单次输出上限（当前 ${draft.max_output_tokens.toLocaleString()}）`}
            value={node.max_tokens}
            min={128}
            max={393216}
            step={1}
            nullable
            placeholder="继承"
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.max_tokens = value))
            }
          />
          <NumberField
            label="超时（秒）"
            hint="这个节点执行一次的时限：研究步骤是整个 Agent 循环，改写与整理是一次直接调用；留空使用角色的超时"
            value={node.timeout_seconds}
            min={5}
            max={14400}
            step={1}
            nullable
            placeholder="继承"
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.timeout_seconds = value))
            }
          />
          <NumberField
            label="输出不合格时的重试次数"
            hint={`0–4；留空使用“运行参数”里的重试次数（当前 ${draft.output_retries}）`}
            value={node.output_retries}
            min={0}
            max={4}
            step={1}
            nullable
            placeholder="继承"
            disabled={disabled}
            onChange={(value) =>
              update((target) => (target.output_retries = value))
            }
          />
          <SelectField
            label="模型思考"
            hint={
              thinkable
                ? "开启后这个节点会带上模型自己的思考开关；思考会显著拉长一次调用，检索类节点通常不值得"
                : "所选模型没有勾选“支持思考”，开启后不会发送思考开关"
            }
            value={
              node.thinking === null || node.thinking === undefined
                ? "__inherit"
                : node.thinking
                  ? "on"
                  : "off"
            }
            options={[
              { value: "__inherit", label: "继承（默认关闭）" },
              { value: "on", label: "开启思考" },
              { value: "off", label: "关闭思考" },
            ]}
            disabled={disabled}
            onChange={(value) =>
              update(
                (target) =>
                  (target.thinking =
                    value === "__inherit" ? null : value === "on"),
              )
            }
          />
        </div>
        {jsonMode && (
          <SwitchField
            label="JSON 模式（response_format）"
            hint={
              JSON_MODE_NODES.includes(info.name)
                ? "要求供应商直接返回 JSON 对象；只在网关支持 response_format 时打开，否则请求会被拒绝"
                : "这个节点不是直接调用，JSON 模式对它不起作用，可以关闭"
            }
            checked={node.json_mode}
            disabled={disabled}
            onChange={(value) => update((target) => (target.json_mode = value))}
          />
        )}
        <details className="rounded-lg border p-3">
          <summary className="cursor-pointer text-sm font-medium">
            高级：附加请求体（extra_body）
          </summary>
          <div className="mt-3">
            <JsonField
              label="extra_body"
              hint='合并进这个节点的请求体，例如 {"repetition_penalty": 1.05} 或网关专用开关'
              value={node.extra_body}
              disabled={disabled}
              onChange={(value) =>
                update((target) => (target.extra_body = value ?? {}))
              }
            />
          </div>
        </details>
      </section>
    </FieldScope.Provider>
  );
}

export function NodesSection({
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
  // An older gateway has no node catalog; the nodes of the draft are still shown.
  const catalog: NodeInfo[] =
    view.catalog.nodes ??
    (Object.keys(draft.nodes ?? {}) as NodeName[]).map((name) => ({
      name,
      label: NODE_LABELS[name] ?? name,
      description: "",
      model_fallback: "默认模型",
      advice: "",
      optional: false,
    }));
  return (
    <div className="space-y-6">
      <div className="text-muted-foreground space-y-2 text-sm">
        <p>
          一次研究依次经过：改写 → 计划 → 并行检索研究 → 笔记整理 → 大纲 →
          并行章节写作 →
          执行摘要。每个节点可以单独指定模型与采样参数；留空的字段继承模型、角色和“运行参数”里的设置。
        </p>
        <p>
          输出 JSON 的节点（改写、计划、笔记整理、大纲、追问分流）建议温度
          0–0.3；JSON
          模式只对直接调用的两个节点（请求改写、笔记整理）有效。只有请求改写和执行摘要可以关闭。
        </p>
        <p>
          研究默认不开思考：检索一轮多半是工具调用，计划是个短对象，思考的代价主要是等待。写章节、定大纲这类一次成文的节点可以单独开。开关只对在“模型”里勾了“支持思考”的模型生效。
        </p>
      </div>
      {catalog.map((info) => (
        <NodeCard
          key={info.name}
          info={info}
          draft={draft}
          onChange={onChange}
          disabled={disabled}
        />
      ))}
      {!catalog.length && (
        <p className="text-muted-foreground text-sm">
          当前网关没有提供节点目录，升级并重启网关后可以在这里调参。
        </p>
      )}
    </div>
  );
}
