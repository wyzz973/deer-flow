"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CircleAlert,
  Loader2,
  RotateCcw,
  Save,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { BreadcrumbItem, BreadcrumbPage } from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { researchApi } from "@/core/deepresearch/api";
import {
  carryMeta,
  changedSections,
  draftProblems,
  edit,
  mergeSecrets,
  SECTION_FIELDS,
  SECTION_LABELS,
  type SettingsSection,
} from "@/core/deepresearch/settings";
import type { EditableSettings, SettingsView } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

import { InvalidFields } from "./settings/fields";
import { ModelsSection } from "./settings/models-section";
import { NodesSection } from "./settings/nodes-section";
import {
  McpSection,
  RuntimeSection,
  SecretsSection,
} from "./settings/other-sections";
import { PromptsSection } from "./settings/prompts-section";
import { RolesSection } from "./settings/roles-section";
import { SourcesSection } from "./settings/sources-section";

const SECTIONS = Object.keys(SECTION_LABELS) as SettingsSection[];

type SaveFailure = {
  conflict: boolean;
  message: string;
  errors: { field: string; message: string }[];
};

function failureOf(error: unknown): SaveFailure {
  const failure = error as Error & { status?: number; detail?: unknown };
  const detail =
    failure.detail && typeof failure.detail === "object"
      ? (failure.detail as {
          message?: string;
          errors?: { field: string; message: string }[];
        })
      : null;
  return {
    conflict: failure.status === 409,
    message: detail?.message ?? failure.message ?? String(error),
    errors: detail?.errors ?? [],
  };
}

/** Settings page for everything DeepResearch runs with: models, roles,
 * prompts, sources, MCP servers and research behavior. Saved settings apply
 * to research created afterwards; each run keeps the version it started with. */
export function ResearchSettings({
  apiBase = "",
  backHref = "/workspace/deepresearch",
}: {
  apiBase?: string;
  backHref?: string;
}) {
  const api = useMemo(() => researchApi(apiBase), [apiBase]);
  const client = useQueryClient();
  const queryKey = useMemo(() => ["research-settings", api.root], [api.root]);
  const loaded = useQuery({
    queryKey,
    queryFn: api.settings,
    retry: false,
    refetchOnWindowFocus: false,
  });
  // The page follows the server until the first edit; from then on the draft
  // is based on a pinned version, so a save can never silently overwrite a
  // newer version (the server answers 409 instead).
  const [pinned, setPinned] = useState<SettingsView | null>(null);
  const [edited, setEdited] = useState<EditableSettings | null>(null);
  const [section, setSection] = useState<SettingsSection>("models");
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<SaveFailure | null>(null);
  // Text a field shows but the draft cannot hold (see InvalidFields).
  const [invalid, setInvalid] = useState<Record<string, string>>({});
  const reportInvalid = useCallback((id: string, problem: string | null) => {
    setInvalid((current) => {
      if ((current[id] ?? null) === problem) return current;
      const next = { ...current };
      if (problem) next[id] = problem;
      else delete next[id];
      return next;
    });
  }, []);
  // Bumped whenever the form is replaced from outside (discard, reload, reset,
  // restore): the section remounts, so text typed into a field, test results
  // and fetched tool lists of the previous draft do not outlive it.
  const [epoch, setEpoch] = useState(0);
  const view = pinned ?? loaded.data ?? null;
  const draft = edited ?? view?.settings ?? null;
  const setDraft = useCallback(
    (next: EditableSettings | null) => {
      setPinned((current) => current ?? view);
      setEdited(next);
    },
    [view],
  );
  const health = useQuery({
    queryKey: ["research-provider-health", api.root],
    queryFn: api.providerHealth,
    enabled: Boolean(view) && section === "sources",
    refetchInterval: section === "sources" ? 15_000 : false,
    retry: false,
  });
  const shown = useMemo(
    () =>
      view && health.data ? { ...view, health: health.data.providers } : view,
    [health.data, view],
  );

  const changed = useMemo(
    () => (view && draft ? changedSections(draft, view.settings) : []),
    [draft, view],
  );
  const problems = useMemo(
    () => [
      ...new Set([
        ...(draft && view ? draftProblems(draft, view) : []),
        ...Object.values(invalid),
      ]),
    ],
    [draft, invalid, view],
  );
  const dirty = changed.length > 0;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  if (loaded.isError && !view)
    return (
      <Frame backHref={backHref}>
        <div className="text-destructive flex items-center gap-2 p-8 text-sm">
          <CircleAlert className="size-4" />
          {loaded.error instanceof Error
            ? loaded.error.message
            : "无法读取研究设置"}
        </div>
      </Frame>
    );
  if (!view || !draft || !shown)
    return (
      <Frame backHref={backHref}>
        <div className="text-muted-foreground flex items-center gap-2 p-8 text-sm">
          <Loader2 className="size-4 animate-spin" />
          正在读取研究设置…
        </div>
      </Frame>
    );

  const disabled = !view.editable || saving;
  // The server's answer replaces the form: version, settings and no draft.
  const adopt = (next: SettingsView, remount = true) => {
    setPinned(next);
    setEdited(null);
    setFailure(null);
    if (remount) setEpoch((current) => current + 1);
    client.setQueryData(queryKey, next);
  };
  // A secrets call changes no settings. Taking its version or settings would
  // re-pin an unsaved draft to the newest version, and the next save would
  // overwrite another administrator's work instead of answering 409.
  const adoptSecrets = (next: SettingsView) => {
    setPinned((current) => (current ? mergeSecrets(current, next) : current));
    client.setQueryData<SettingsView>(queryKey, (current) =>
      current ? mergeSecrets(current, next) : current,
    );
  };
  const discard = () => {
    setEdited(null);
    setFailure(null);
    setEpoch((current) => current + 1);
  };
  const save = () => {
    setSaving(true);
    setFailure(null);
    void api
      .saveSettings(view.version, draft)
      .then((next) => {
        // The saved lists are the draft's lists: cards keep their keys, so a
        // save does not remount them or drop their test results.
        carryMeta(draft, next.settings);
        adopt(next, false);
        toast.success(
          `已保存为第 ${next.version} 版，之后新建的研究使用这些设置`,
        );
      })
      .catch((error: unknown) => setFailure(failureOf(error)))
      .finally(() => setSaving(false));
  };
  const reload = () => {
    void api
      .settings()
      .then((next) => adopt(next))
      .catch((error: unknown) => setFailure(failureOf(error)));
  };
  const serverReset = (fields?: string[]) => {
    setSaving(true);
    void api
      .resetSettings(view.version, fields)
      .then((next) => {
        adopt(next);
        toast.success("已恢复为配置文件的设置");
      })
      .catch((error: unknown) => setFailure(failureOf(error)))
      .finally(() => setSaving(false));
  };
  const sectionFields = SECTION_FIELDS[section];
  const sectionDiffersFromDefaults = changedSections(
    draft,
    view.defaults,
  ).includes(section);
  const props = { view: shown, draft, onChange: setDraft, api, disabled };

  return (
    <Frame backHref={backHref}>
      <div className="flex min-h-0 w-full flex-1 flex-col md:flex-row">
        <nav
          aria-label="设置分类"
          className="flex shrink-0 gap-1 overflow-x-auto border-b p-2 md:w-52 md:flex-col md:overflow-visible md:border-r md:border-b-0 md:p-3"
        >
          {SECTIONS.map((item) => (
            <button
              key={item}
              type="button"
              aria-current={section === item ? "page" : undefined}
              onClick={() => setSection(item)}
              className={cn(
                "hover:bg-accent flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-left text-sm whitespace-nowrap",
                section === item && "bg-accent font-medium",
              )}
            >
              {SECTION_LABELS[item]}
              {changed.includes(item) && (
                <span
                  className="bg-primary size-1.5 rounded-full"
                  aria-label="有未保存的修改"
                />
              )}
            </button>
          ))}
        </nav>
        {/* Keyed by section so each section opens at its top, and by epoch so
            a replaced form starts clean. */}
        <div
          key={`${section}:${epoch}`}
          className="min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
          <div className="mx-auto w-full max-w-4xl space-y-5 px-4 py-6 md:px-8">
            {!view.editable && (
              <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
                只有管理员可以修改研究设置，当前为只读。
              </p>
            )}
            {view.error && (
              <p className="border-destructive/40 bg-destructive/5 rounded-lg border p-3 text-sm">
                已保存的设置与当前配置文件不兼容，研究暂时使用配置文件的设置：
                {view.error}
              </p>
            )}
            <header className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold">
                {SECTION_LABELS[section]}
              </h2>
              {changed.includes(section) && <Badge>未保存</Badge>}
              {sectionFields.length > 0 && sectionDiffersFromDefaults && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="ml-auto"
                  disabled={disabled}
                  title="只修改页面上的草稿，保存后生效"
                  onClick={() => {
                    setDraft(
                      edit(draft, (next) => {
                        for (const field of sectionFields)
                          (next as Record<string, unknown>)[field] =
                            structuredClone(view.defaults[field]);
                      }),
                    );
                    setEpoch((current) => current + 1);
                  }}
                >
                  <RotateCcw className="size-3.5" />
                  本节恢复为配置文件的设置
                </Button>
              )}
            </header>
            <InvalidFields.Provider value={reportInvalid}>
              {section === "models" && <ModelsSection {...props} />}
              {section === "nodes" && <NodesSection {...props} />}
              {section === "roles" && <RolesSection {...props} />}
              {section === "prompts" && <PromptsSection {...props} />}
              {section === "sources" && <SourcesSection {...props} />}
              {section === "mcp" && <McpSection {...props} />}
              {section === "runtime" && <RuntimeSection {...props} />}
            </InvalidFields.Provider>
            {section === "secrets" && (
              <SecretsSection
                view={shown}
                api={api}
                disabled={disabled}
                dirty={dirty}
                onSecrets={adoptSecrets}
                onRestored={adopt}
                onReset={serverReset}
              />
            )}
          </div>
        </div>
      </div>
      {(dirty || failure) && (
        <div
          role="region"
          aria-label="保存设置"
          className="bg-background/95 w-full shrink-0 border-t px-4 py-3 backdrop-blur md:px-8"
        >
          <div className="mx-auto flex max-w-5xl flex-col gap-2">
            {failure && (
              <div
                role="alert"
                className="border-destructive/40 bg-destructive/5 rounded-lg border p-3 text-sm"
              >
                <p className="font-medium">
                  {failure.conflict
                    ? "设置已被其他人修改。载入最新版本后再修改（会放弃这里未保存的修改）。"
                    : `保存失败：${failure.message}`}
                </p>
                {failure.errors.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-xs">
                    {failure.errors.map((item) => (
                      <li key={`${item.field}:${item.message}`}>
                        <code>{item.field}</code>：{item.message}
                      </li>
                    ))}
                  </ul>
                )}
                {failure.conflict && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={reload}
                  >
                    载入最新版本
                  </Button>
                )}
              </div>
            )}
            {problems.length > 0 && (
              <details className="text-sm">
                <summary className="text-destructive cursor-pointer">
                  还有 {problems.length} 个问题需要处理后才能保存
                </summary>
                <ul className="text-destructive mt-1 list-disc pl-5 text-xs">
                  {problems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              </details>
            )}
            {dirty && (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-muted-foreground text-sm">
                  未保存的修改：
                  {changed.map((item) => SECTION_LABELS[item]).join("、")}
                </span>
                <div className="ml-auto flex gap-2">
                  <Button variant="ghost" disabled={saving} onClick={discard}>
                    <Undo2 className="size-4" />
                    放弃修改
                  </Button>
                  <Button
                    disabled={disabled || problems.length > 0}
                    onClick={save}
                  >
                    {saving ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Save className="size-4" />
                    )}
                    保存
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </Frame>
  );
}

function Frame({
  backHref,
  children,
}: {
  backHref: string;
  children: React.ReactNode;
}) {
  return (
    <WorkspaceContainer>
      <WorkspaceHeader>
        <BreadcrumbItem>
          <BreadcrumbPage>研究设置</BreadcrumbPage>
        </BreadcrumbItem>
      </WorkspaceHeader>
      <WorkspaceBody className="min-h-0">
        <div className="flex w-full items-center gap-2 border-b px-4 py-2">
          <Button variant="ghost" size="sm" asChild>
            <Link href={backHref}>
              <ArrowLeft className="size-4" />
              返回研究
            </Link>
          </Button>
          <span className="text-muted-foreground text-xs">
            研究使用的模型、角色、提示词和数据源都在这里配置，与 DeerFlow
            自身的模型和工具设置相互独立。
          </span>
        </div>
        {children}
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
