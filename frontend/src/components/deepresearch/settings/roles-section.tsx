"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { firstText } from "@/core/deepresearch/presentation";
import {
  allowlistChoices,
  edit,
  FIXED_ROLES,
  IDENTIFIER,
  newRole,
  ROLE_TITLES,
} from "@/core/deepresearch/settings";
import type {
  EditableSettings,
  RoleSpec,
  SettingsView,
} from "@/core/deepresearch/types";

import { LongTextField, NumberField, SelectField, TextField } from "./fields";

function RoleCard({
  name,
  role,
  view,
  draft,
  onChange,
  disabled,
}: {
  name: string;
  role: RoleSpec;
  view: SettingsView;
  draft: EditableSettings;
  onChange: (next: EditableSettings) => void;
  disabled: boolean;
}) {
  const fixed = (FIXED_ROLES as readonly string[]).includes(name);
  const update = (change: (target: RoleSpec) => void) =>
    onChange(edit(draft, (next) => change(next.skills[name]!)));
  const engineTools = view.catalog.engine_tools.map((tool) => tool.name);
  const choices = fixed
    ? engineTools.map((tool) => ({ name: tool, label: `${tool}（引擎）` }))
    : allowlistChoices(draft, engineTools);
  const restricted = role.tools !== null && role.tools !== undefined;
  const models = [
    { value: "__default", label: "跟随默认模型" },
    ...draft.models.map((model) => ({
      value: model.name,
      label: firstText(model.display_name, model.name),
    })),
  ];
  return (
    <section
      className="space-y-4 rounded-xl border p-4"
      aria-label={`研究角色 ${ROLE_TITLES[name] ?? firstText(role.name, name)}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">
          {ROLE_TITLES[name] ?? firstText(role.name, name)}
        </h3>
        <code className="text-muted-foreground text-xs">{name}</code>
        {fixed ? (
          <Badge variant="secondary">固定角色</Badge>
        ) : (
          <Badge variant="outline">研究员</Badge>
        )}
        {role.agent && (
          <Badge variant="outline">绑定 DeerFlow 子 Agent：{role.agent}</Badge>
        )}
        <div className="ml-auto flex items-center gap-3">
          {!fixed && (
            <label className="flex items-center gap-2 text-xs">
              启用
              <Switch
                checked={role.enabled !== false}
                disabled={disabled}
                onCheckedChange={(value) =>
                  update((target) => (target.enabled = value))
                }
                aria-label={`启用 ${firstText(role.name, name)}`}
              />
            </label>
          )}
          {!fixed && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`删除角色 ${firstText(role.name, name)}`}
              disabled={disabled}
              onClick={() =>
                onChange(edit(draft, (next) => delete next.skills[name]))
              }
            >
              <Trash2 className="size-4" />
            </Button>
          )}
        </div>
      </header>
      {role.agent && (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
          这个角色来自旧配置，基础提示词、工具和模型取自 DeerFlow 子 Agent “
          {role.agent}”。
          <Button
            variant="outline"
            size="sm"
            className="ml-auto h-7"
            disabled={disabled}
            onClick={() => update((target) => (target.agent = null))}
          >
            改为独立角色
          </Button>
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {!fixed && (
          <TextField
            label="显示名称"
            value={role.name}
            disabled={disabled}
            onChange={(value) => update((target) => (target.name = value))}
          />
        )}
        <SelectField
          label="模型"
          value={role.model ?? "__default"}
          options={models}
          disabled={disabled}
          onChange={(value) =>
            update(
              (target) => (target.model = value === "__default" ? null : value),
            )
          }
        />
        <TextField
          label="角色说明"
          className="md:col-span-2"
          hint={
            fixed
              ? "描述这个固定角色的职责"
              : "规划模型根据这段说明决定把哪些研究单元分给这个角色"
          }
          value={role.description}
          disabled={disabled}
          onChange={(value) => update((target) => (target.description = value))}
        />
        <NumberField
          label="单次执行超时（秒）"
          value={role.timeout_seconds}
          min={5}
          max={1800}
          disabled={disabled}
          onChange={(value) =>
            update((target) => (target.timeout_seconds = value ?? 600))
          }
        />
        <NumberField
          label="最大步数（max_turns）"
          hint="引擎图递归步数，含中间件节点；留空使用引擎默认"
          value={role.max_turns}
          min={1}
          max={1000}
          nullable
          disabled={disabled}
          onChange={(value) => update((target) => (target.max_turns = value))}
        />
      </div>
      <LongTextField
        label="角色系统提示"
        hint="放在系统提示最前面，定义角色身份与边界"
        value={role.system_prompt}
        minRows={3}
        disabled={disabled}
        onChange={(value) => update((target) => (target.system_prompt = value))}
      />
      <LongTextField
        label="方法论（Skill）"
        hint="注入系统提示的研究方法、步骤与输出要求。保存后以内联文本保存，不再读取 Skill 文件"
        value={role.methodology}
        minRows={8}
        disabled={disabled}
        onChange={(value) => update((target) => (target.methodology = value))}
      />
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="font-medium">可用工具</span>
          <label className="flex items-center gap-1.5 text-xs">
            <input
              type="radio"
              name={`tools-${name}`}
              checked={!restricted}
              disabled={disabled}
              onChange={() => update((target) => (target.tools = null))}
            />
            {fixed ? "不使用工具（推荐）" : "全部数据源与默认引擎工具"}
          </label>
          <label className="flex items-center gap-1.5 text-xs">
            <input
              type="radio"
              name={`tools-${name}`}
              checked={restricted}
              disabled={disabled}
              onChange={() => update((target) => (target.tools = []))}
            />
            只允许下面选中的工具
          </label>
        </div>
        {restricted && (
          <div className="flex flex-wrap gap-2">
            {choices.map((choice) => {
              const checked = role.tools?.includes(choice.name) ?? false;
              return (
                <label
                  key={choice.name}
                  className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={() =>
                      update((target) => {
                        const tools = new Set(target.tools ?? []);
                        if (checked) tools.delete(choice.name);
                        else tools.add(choice.name);
                        target.tools = [...tools];
                      })
                    }
                  />
                  {choice.label}
                </label>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}

export function RolesSection({
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
  const [key, setKey] = useState("");
  const names = Object.keys(draft.skills);
  const researchers = names.filter(
    (name) => !(FIXED_ROLES as readonly string[]).includes(name),
  );
  const valid = IDENTIFIER.test(key) && !draft.skills[key];
  return (
    <div className="space-y-6">
      <p className="text-muted-foreground text-sm">
        研究由“规划 → 研究员并行研究 →
        报告撰写”完成。规划和报告撰写是固定角色；研究员可以增删，规划时按“角色说明”分配研究单元。
      </p>
      {FIXED_ROLES.filter((name) => draft.skills[name]).map((name) => (
        <RoleCard
          key={name}
          name={name}
          role={draft.skills[name]!}
          view={view}
          draft={draft}
          onChange={onChange}
          disabled={disabled}
        />
      ))}
      <h3 className="text-base font-medium">研究员（{researchers.length}）</h3>
      {researchers.map((name) => (
        <RoleCard
          key={name}
          name={name}
          role={draft.skills[name]!}
          view={view}
          draft={draft}
          onChange={onChange}
          disabled={disabled}
        />
      ))}
      <div className="flex flex-wrap items-end gap-2 rounded-xl border border-dashed p-4">
        <div className="space-y-1.5">
          <label htmlFor="new-role-key" className="text-sm font-medium">
            新研究员的标识
          </label>
          <Input
            id="new-role-key"
            value={key}
            placeholder="例如 market-analysis"
            className="w-64 font-mono text-xs"
            disabled={disabled}
            onChange={(event) => setKey(event.target.value.trim())}
          />
          <p className="text-muted-foreground text-xs">
            字母、数字、- 或 _；计划里的研究单元用它指定角色，创建后不能改名
          </p>
        </div>
        <Button
          variant="outline"
          disabled={disabled || !valid}
          onClick={() => {
            onChange(edit(draft, (next) => (next.skills[key] = newRole())));
            setKey("");
          }}
        >
          <Plus className="size-4" />
          添加研究员
        </Button>
      </div>
    </div>
  );
}
