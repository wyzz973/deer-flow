"use client";

import { RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { edit } from "@/core/deepresearch/settings";
import type { EditableSettings, SettingsView } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import { LongTextField } from "./fields";

export function PromptsSection({
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
  const [filter, setFilter] = useState("");
  const stages = useMemo(
    () => [...new Set(view.catalog.prompts.map((prompt) => prompt.stage))],
    [view],
  );
  const [stage, setStage] = useState<string>("全部");
  const needle = filter.trim().toLowerCase();
  const prompts = view.catalog.prompts.filter(
    (prompt) =>
      (stage === "全部" || prompt.stage === stage) &&
      (!needle ||
        [
          prompt.label,
          prompt.description,
          prompt.key,
          draft.prompts[prompt.key] ?? "",
        ]
          .join("\n")
          .toLowerCase()
          .includes(needle)),
  );
  return (
    <div className="space-y-5">
      <p className="text-muted-foreground text-sm">
        这里是研究流程发给模型的全部指令。任务数据（研究简报、证据、输出结构）由系统在运行时附加；
        <code>{"{language}"}</code> 会替换为读者语言。可以随时恢复默认。
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {["全部", ...stages].map((value) => (
          <Button
            key={value}
            variant={stage === value ? "secondary" : "ghost"}
            size="sm"
            onClick={() => setStage(value)}
          >
            {value}
          </Button>
        ))}
        <Input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="搜索提示词"
          aria-label="搜索提示词"
          className="ml-auto h-8 w-56 text-xs"
        />
      </div>
      {prompts.map((prompt) => {
        // The operator file may already override a built-in prompt; that is the baseline.
        const baseline = view.defaults.prompts[prompt.key] ?? prompt.default;
        const value = draft.prompts[prompt.key] ?? baseline;
        const modified = value !== baseline;
        return (
          <section
            key={prompt.key}
            className={cn(
              "space-y-2 rounded-xl border p-4",
              modified && "border-primary/50",
            )}
            aria-label={`提示词 ${prompt.label}`}
          >
            <header className="flex flex-wrap items-center gap-2">
              <h3 className="font-medium">{prompt.label}</h3>
              <Badge variant="outline">{prompt.stage}</Badge>
              <code className="text-muted-foreground text-xs">
                {prompt.key}
              </code>
              {modified && <Badge>已修改</Badge>}
            </header>
            <p className="text-muted-foreground text-xs">
              {prompt.description}
            </p>
            <LongTextField
              label="内容"
              value={value}
              minRows={6}
              disabled={disabled}
              onChange={(next) =>
                onChange(
                  edit(draft, (target) => (target.prompts[prompt.key] = next)),
                )
              }
              headerRight={
                modified && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    disabled={disabled}
                    onClick={() =>
                      onChange(
                        edit(
                          draft,
                          (target) => (target.prompts[prompt.key] = baseline),
                        ),
                      )
                    }
                  >
                    <RotateCcw className="size-3.5" />
                    恢复默认
                  </Button>
                )
              }
            />
          </section>
        );
      })}
    </div>
  );
}
