"use client";

import {
  ArrowRight,
  Check,
  Download,
  FlaskConical,
  Loader2,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { researchApi } from "@/core/deepresearch/api";
import {
  mergeEvents,
  terminal,
  type Budget,
  type Capabilities,
  type Evidence,
  type Plan,
  type ResearchEvent,
  type Run,
  type Segment,
  type Unit,
} from "@/core/deepresearch/types";

import { ResearchTracePanel } from "./trace-panel";

const input =
  "w-full rounded-xl border border-border bg-transparent px-3 py-2 text-sm outline-none focus:border-black/40  dark:focus:border-white/40";
const button =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-border px-3 py-2 text-sm transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-40  dark:hover:bg-background/10";
const primary = `${button} bg-neutral-900 text-white hover:bg-neutral-700 dark:bg-neutral-100 dark:text-foreground dark:hover:bg-background`;
const labels: Record<string, string> = {
  CREATED: "已创建",
  PLANNING: "正在规划",
  AWAITING_PLAN_CONFIRMATION: "等待你确认计划",
  RESEARCHING: "正在研究",
  VALIDATING: "检查研究覆盖",
  GAP_FOUND: "发现待补充证据",
  RESEARCH_COMPLETE: "研究资料就绪",
  SYNTHESIZING: "综合研究结论",
  CITATION_BINDING: "绑定引用",
  FINAL_VALIDATING: "校验报告",
  RENDERING: "生成报告",
  COMPLETED: "已完成",
  FAILED: "执行失败",
  CANCELLED: "已取消",
};
const eventLabels: Record<string, string> = {
  "run.created": "研究已创建",
  "plan.created": "计划已生成",
  "plan.updated": "计划已更新",
  "plan.waiting_confirmation": "等待确认",
  "research.source_policy": "已应用来源优先级",
  "research.unit.started": "开始研究单元",
  "research.unit.completed": "研究单元完成",
  "research.tool.started": "正在检索",
  "research.tool.completed": "检索已完成",
  "evidence.pool.updated": "证据池已更新",
  "validator.gap_found": "发现证据缺口",
  "validator.passed": "研究校验通过",
  "report.synthesizing": "正在综合报告",
  "citation.bound": "引用已绑定",
  "report.completed": "报告已完成",
  "run.failed": "执行失败",
  "run.cancelled": "已取消",
};

export function ResearchWorkbench({ apiBase = "" }: { apiBase?: string }) {
  const api = useMemo(() => researchApi(apiBase), [apiBase]);
  const [cap, setCap] = useState<Capabilities | null>(null);
  const [history, setHistory] = useState<Run[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [query, setQuery] = useState("");
  const [constraints, setConstraints] = useState("");
  const [budget, setBudget] = useState<Budget | null>(null);
  const [sourceNames, setSourceNames] = useState<string[]>([]);
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [selected, setSelected] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState("");
  const [streamEpoch, setStreamEpoch] = useState(0);
  const planKey = useRef("");
  const selectedId = useRef(runId);
  selectedId.current = runId;
  const creationKey = useRef<{ body: string; key: string } | null>(null);
  const loadHistory = useCallback(
    async () => setHistory(await api.list()),
    [api],
  );

  useEffect(() => {
    let alive = true;
    void Promise.all([api.capabilities(), api.list()])
      .then(([c, h]) => {
        if (!alive) return;
        setCap(c);
        setBudget(c.budget_ceiling);
        setHistory(h);
        const saved = new URLSearchParams(window.location.search).get("run");
        if (saved) setRunId(saved);
      })
      .catch((e: unknown) => {
        if (alive) setError(String(e));
      });
    return () => {
      alive = false;
    };
  }, [api]);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let source: EventSource | undefined;
    setEvents([]);
    setSelected(null);
    setConnection("连接中");
    const refresh = async () => {
      const value = await api.get(runId);
      if (alive) setRun(value);
      return value;
    };
    void refresh()
      .then(() => {
        if (!alive) return;

        source = new EventSource(
          `${api.root}/${encodeURIComponent(runId)}/events`,
          { withCredentials: true },
        );
        source.onopen = () => {
          if (alive) setConnection("实时连接");
        };
        source.onmessage = (event: MessageEvent<string>) => {
          if (!alive) return;
          let item: ResearchEvent;
          try {
            item = JSON.parse(event.data) as ResearchEvent;
          } catch {
            return;
          }
          if (item.run_id !== runId) return;
          setEvents((old) => mergeEvents(old, item));
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => {
            void refresh()
              .then((current) => {
                if (alive && terminal.has(current.status)) {
                  source?.close();
                  setConnection("");
                }
              })
              .catch((e: unknown) => alive && setError(String(e)));
          }, 100);
          if (
            ["report.completed", "run.failed", "run.cancelled"].includes(
              item.type,
            )
          ) {
            void loadHistory().catch(() => undefined);
          }
        };
        source.onerror = () => {
          if (alive) setConnection("连接中断，正在自动重连");
        };
      })
      .catch((e: unknown) => {
        if (alive) {
          setError(String(e));
          setConnection("");
        }
      });
    return () => {
      alive = false;
      source?.close();
      if (timer) clearTimeout(timer);
    };
  }, [api, runId, streamEpoch, loadHistory]);

  useEffect(() => {
    if (!run?.plan) return;
    const key = `${run.run_id}:${run.plan.plan_version}`;
    if (key !== planKey.current) {
      setPlan(structuredClone(run.plan));
      planKey.current = key;
    }
  }, [run]);

  function choose(id: string) {
    setRunId(id);
    setRun(null);
    setPlan(null);
    setError("");
    planKey.current = "";
    const url = new URL(window.location.href);
    url.searchParams.set("run", id);
    window.history.replaceState(null, "", url);
  }
  async function create() {
    if (!budget || query.trim().length < 3) return;
    setBusy(true);
    setError("");
    try {
      const body = {
        query: query.trim(),
        constraints: constraints
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
        source_names: sourceNames,
        budget,
      };
      const encoded = JSON.stringify(body);
      if (creationKey.current?.body !== encoded)
        creationKey.current = { body: encoded, key: crypto.randomUUID() };
      const value = await api.create(body, creationKey.current.key);
      creationKey.current = null;
      choose(value.run_id);
      setRun(value);
      await loadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function act(action: string, body: unknown = {}) {
    if (!runId) return;
    const id = runId;
    setBusy(true);
    setError("");
    try {
      const value = await api.action(id, action, body);
      if (selectedId.current === id) {
        setRun(value);
        setStreamEpoch((x) => x + 1);
      }
      await loadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function updateUnit(index: number, patch: Partial<Unit>) {
    setPlan((p) =>
      p
        ? {
            ...p,
            research_units: p.research_units.map((u, i) =>
              i === index ? { ...u, ...patch } : u,
            ),
          }
        : p,
    );
  }
  const researchSkills = Object.entries(cap?.skills ?? {}).filter(
    ([name]) => !["deepresearch", "report-synthesis"].includes(name),
  );
  const active = Boolean(
    run &&
    !terminal.has(run.status) &&
    run.status !== "AWAITING_PLAN_CONFIRMATION",
  );
  const report = run?.report;
  function renderSegments(items: Segment[]) {
    return items.map((s, index) => (
      <p className="mb-4 text-[15px] leading-8" key={index}>
        {s.text}
        {s.evidence_ids.map((eid) => {
          const ref = report?.citations.find((c) => c.evidence_id === eid);
          return ref ? (
            <Button
              variant="ghost"
              type="button"
              key={eid}
              className="dark:bg-background/10 mx-0.5 h-5 min-w-5 rounded bg-black/5 px-1.5 align-super text-xs hover:bg-black/15"
              aria-label={`查看引用 ${ref.number}`}
              onClick={() => setSelected(ref)}
            >
              {ref.number}
            </Button>
          ) : null;
        })}
      </p>
    ));
  }

  return (
    <section className="bg-background text-foreground h-full min-h-0 w-full overflow-auto">
      <div className="mx-auto max-w-[1500px] px-4 py-6 md:px-8">
        <header className="border-border/60 mb-6 flex items-center justify-between gap-4 border-b pb-5">
          <div className="flex items-center gap-3">
            <div className="rounded-2xl bg-neutral-100 p-3 dark:bg-neutral-800">
              <FlaskConical size={22} />
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight">
                DeepResearch
              </h1>
              <p className="text-muted-foreground mt-1 text-xs">
                先确认方向，再用证据回答问题
              </p>
            </div>
          </div>
          <span className="border-border rounded-full border px-3 py-1 text-xs">
            {cap
              ? cap.mode === "demo"
                ? "合成数据演示"
                : "DeerFlow · MCP"
              : "连接服务中"}
          </span>
        </header>
        {cap?.mode === "demo" && (
          <div
            role="note"
            className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
          >
            演示模式：用于验证规划、审批、研究状态和引用功能。所有研究资料均为合成测试数据，不代表真实结论。
          </div>
        )}
        {error && (
          <div
            role="alert"
            className="mb-5 flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900"
          >
            <span>{error}</span>
            <Button
              variant="ghost"
              aria-label="关闭错误提示"
              onClick={() => setError("")}
            >
              <X size={16} />
            </Button>
          </div>
        )}
        <div className="grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <Button
              variant="ghost"
              className={`${primary} w-full`}
              onClick={() => {
                setRunId(null);
                setRun(null);
                setPlan(null);
                setSelected(null);
                const url = new URL(window.location.href);
                url.searchParams.delete("run");
                window.history.replaceState(null, "", url);
              }}
            >
              <Plus size={16} />
              新建研究
            </Button>
            <div className="flex items-center justify-between">
              <h2 className="text-muted-foreground text-xs font-medium">
                最近研究
              </h2>
              <Button
                variant="ghost"
                aria-label="刷新任务列表"
                className="text-muted-foreground p-1"
                onClick={() => {
                  void loadHistory().catch((e: unknown) => setError(String(e)));
                }}
              >
                <RefreshCw size={14} />
              </Button>
            </div>
            <nav
              aria-label="研究历史"
              className="max-h-28 space-y-1 overflow-auto lg:max-h-[65vh]"
            >
              {history.map((h) => (
                <Button
                  variant="ghost"
                  key={h.run_id}
                  className={`dark:hover:bg-background/5 h-auto w-full min-w-0 flex-col items-start rounded-xl p-3 text-left whitespace-normal transition hover:bg-black/5 ${h.run_id === runId ? "dark:bg-background/5 bg-black/5" : ""}`}
                  onClick={() => choose(h.run_id)}
                >
                  <span className="block truncate text-sm">{h.query}</span>
                  <span className="text-muted-foreground mt-1 block text-xs">
                    {labels[h.status] ?? h.status}
                  </span>
                </Button>
              ))}
              {history.length === 0 && (
                <p className="text-muted-foreground py-4 text-sm">
                  还没有研究任务
                </p>
              )}
            </nav>
          </aside>
          <section className="min-w-0">
            {!runId ? (
              <div className="mx-auto max-w-3xl py-6 md:py-12">
                <h2 className="text-3xl font-semibold tracking-tight">
                  你想深入了解什么？
                </h2>
                <p className="text-muted-foreground mt-3 text-sm leading-6">
                  描述问题、需要做的决策和关注范围。系统生成计划后，由你确认开始研究。
                </p>
                <form
                  className="mt-8 space-y-5"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void create();
                  }}
                >
                  <label
                    className="block text-sm font-medium"
                    htmlFor="research-query"
                  >
                    研究问题
                  </label>
                  <Textarea
                    id="research-query"
                    className={`${input} min-h-36 resize-y text-base leading-7`}
                    placeholder="例如：研究大型开发者平台的 AI 化技术路线，比较工程落地条件、风险与演进策略。"
                    required
                    minLength={3}
                    maxLength={12000}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                  <details className="border-border rounded-xl border p-4">
                    <summary className="cursor-pointer text-sm">
                      范围、来源偏好与预算
                    </summary>
                    <div className="mt-4 space-y-4">
                      <label className="block text-sm">
                        约束条件（每行一项）
                        <Textarea
                          className={`${input} mt-2`}
                          rows={3}
                          value={constraints}
                          onChange={(e) => setConstraints(e.target.value)}
                        />
                      </label>
                      <fieldset>
                        <legend className="mb-2 text-sm">
                          优先来源（不取消内外双源）
                        </legend>
                        <div className="flex flex-wrap gap-3">
                          {cap?.sources.map((s) => (
                            <label className="text-sm" key={s.name}>
                              <input
                                type="checkbox"
                                className="mr-2"
                                checked={sourceNames.includes(s.name)}
                                onChange={(e) =>
                                  setSourceNames((v) =>
                                    e.target.checked
                                      ? [...v, s.name]
                                      : v.filter((n) => n !== s.name),
                                  )
                                }
                              />
                              {s.name} · {s.origin}
                            </label>
                          ))}
                        </div>
                      </fieldset>
                      {budget && (
                        <div className="grid grid-cols-2 gap-3">
                          {(
                            [
                              ["max_iterations", "最多补研轮次"],
                              ["max_units", "最多研究单元"],
                              ["max_tool_calls", "最多工具调用"],
                              ["max_elapsed_seconds", "执行时间上限（秒）"],
                              ["max_model_tokens", "模型预算单位"],
                            ] as const
                          ).map(([k, name]) => (
                            <label
                              key={k}
                              className="text-muted-foreground text-xs"
                            >
                              {name}
                              <Input
                                aria-label={name}
                                type="number"
                                min={k === "max_iterations" ? 0 : 1}
                                max={cap?.budget_ceiling[k]}
                                className={`${input} mt-1`}
                                value={budget[k]}
                                onChange={(e) =>
                                  setBudget({
                                    ...budget,
                                    [k]: Number(e.target.value),
                                  })
                                }
                              />
                            </label>
                          ))}
                        </div>
                      )}
                    </div>
                  </details>
                  <div className="flex justify-end">
                    <Button
                      variant="ghost"
                      className={primary}
                      type="submit"
                      disabled={busy || !cap?.ready || query.trim().length < 3}
                    >
                      {busy ? (
                        <Loader2 size={16} className="animate-spin" />
                      ) : (
                        <ArrowRight size={16} />
                      )}
                      生成研究计划
                    </Button>
                  </div>
                </form>
              </div>
            ) : !run ? (
              <div
                role="status"
                className="text-muted-foreground flex items-center gap-2 p-10"
              >
                <Loader2 size={18} className="animate-spin" />
                正在加载研究任务
              </div>
            ) : (
              <>
                <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="max-w-3xl text-xl leading-8 font-semibold">
                      {run.query}
                    </h2>
                    <div className="text-muted-foreground mt-2 flex items-center gap-2 text-sm">
                      {active && <Loader2 className="animate-spin" size={14} />}
                      <span role="status">
                        {labels[run.status] ?? run.status}
                      </span>
                      <span className="text-xs">{connection}</span>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="ghost"
                      className={button}
                      aria-label="刷新任务"
                      onClick={() => setStreamEpoch((n) => n + 1)}
                    >
                      <RefreshCw size={15} />
                    </Button>
                    {!terminal.has(run.status) && (
                      <Button
                        variant="ghost"
                        className={button}
                        disabled={busy}
                        onClick={() => {
                          void act("cancel");
                        }}
                      >
                        <Square size={14} />
                        取消
                      </Button>
                    )}
                    {run.status === "FAILED" && run.error?.recoverable && (
                      <Button
                        variant="ghost"
                        className={primary}
                        disabled={busy}
                        onClick={() => {
                          void act("retry");
                        }}
                      >
                        <RefreshCw size={14} />
                        从检查点恢复
                      </Button>
                    )}
                  </div>
                </div>
                <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
                  {[
                    ["证据", run.evidence_count],
                    [
                      "工具调用",
                      `${run.usage.tool_calls} / ${run.budget.max_tool_calls}`,
                    ],
                    ["补研轮次", run.iteration],
                    ["有效执行", `${Math.round(run.usage.elapsed_seconds)} 秒`],
                  ].map(([name, value]) => (
                    <div
                      key={name}
                      className="bg-muted/40 rounded-2xl p-4 dark:bg-neutral-900"
                    >
                      <div className="text-muted-foreground text-xs">
                        {name}
                      </div>
                      <div className="mt-2 text-lg font-medium tabular-nums">
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
                {run.error && (
                  <div
                    role="alert"
                    className="mb-5 rounded-xl border border-red-200 p-4 text-sm"
                  >
                    <strong>{run.error.code}</strong>
                    <p className="mt-2">{run.error.message}</p>
                  </div>
                )}
                {run.status === "AWAITING_PLAN_CONFIRMATION" && plan && (
                  <div className="border-border mb-6 rounded-2xl border p-5">
                    <div className="mb-5 flex items-center justify-between">
                      <h3 className="font-semibold">审核研究计划</h3>
                      <span className="text-muted-foreground text-xs">
                        版本 {run.plan?.plan_version}
                      </span>
                    </div>
                    <label className="block text-sm">
                      研究目标
                      <Textarea
                        className={`${input} mt-2 mb-4`}
                        value={plan.goal}
                        onChange={(e) =>
                          setPlan({ ...plan, goal: e.target.value })
                        }
                      />
                    </label>
                    <div className="space-y-4">
                      {plan.research_units.map((u, i) => (
                        <div
                          key={u.id}
                          className="bg-muted/40 rounded-xl p-4 dark:bg-neutral-900"
                        >
                          <div className="mb-3 flex items-center gap-2">
                            <span className="text-muted-foreground font-mono text-xs">
                              {u.id}
                            </span>
                            <select
                              aria-label={`${u.id} 研究技能`}
                              className={`${input} flex-1`}
                              value={u.skill}
                              onChange={(e) =>
                                updateUnit(i, { skill: e.target.value })
                              }
                            >
                              {researchSkills.map(([name, value]) => (
                                <option key={name} value={name}>
                                  {value.description}
                                </option>
                              ))}
                            </select>
                            <Button
                              variant="ghost"
                              className="p-2"
                              aria-label={`删除 ${u.id}`}
                              disabled={plan.research_units.length <= 1}
                              onClick={() =>
                                setPlan({
                                  ...plan,
                                  research_units: plan.research_units.filter(
                                    (_, index) => index !== i,
                                  ),
                                })
                              }
                            >
                              <Trash2 size={15} />
                            </Button>
                          </div>
                          <Textarea
                            aria-label={`${u.id} 研究目标`}
                            className={`${input} leading-6`}
                            rows={3}
                            value={u.objective}
                            onChange={(e) =>
                              updateUnit(i, { objective: e.target.value })
                            }
                          />
                          <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            <label className="text-muted-foreground text-xs">
                              优先级（数字越小越优先）
                              <Input
                                type="number"
                                min={0}
                                max={1000}
                                className={`${input} mt-1`}
                                value={u.priority}
                                onChange={(e) =>
                                  updateUnit(i, {
                                    priority: Number(e.target.value),
                                  })
                                }
                              />
                            </label>
                            <label className="text-muted-foreground text-xs">
                              依赖单元（逗号分隔）
                              <Input
                                className={`${input} mt-1`}
                                value={u.depends_on.join(",")}
                                onChange={(e) =>
                                  updateUnit(i, {
                                    depends_on: e.target.value
                                      .split(",")
                                      .map((x) => x.trim())
                                      .filter(Boolean),
                                  })
                                }
                              />
                            </label>
                          </div>
                          <div className="mt-3 flex flex-wrap gap-3">
                            {cap?.sources.map((source) => (
                              <label key={source.name} className="text-xs">
                                <input
                                  type="checkbox"
                                  className="mr-1"
                                  checked={u.source_strategy.source_names.includes(
                                    source.name,
                                  )}
                                  onChange={(e) =>
                                    updateUnit(i, {
                                      source_strategy: {
                                        ...u.source_strategy,
                                        source_names: e.target.checked
                                          ? [
                                              ...u.source_strategy.source_names,
                                              source.name,
                                            ]
                                          : u.source_strategy.source_names.filter(
                                              (n) => n !== source.name,
                                            ),
                                      },
                                    })
                                  }
                                />
                                {source.name}
                              </label>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                    <Button
                      variant="ghost"
                      className={`${button} mt-4`}
                      disabled={
                        plan.research_units.length >= run.budget.max_units
                      }
                      onClick={() => {
                        const skill = researchSkills[0]?.[0];
                        if (skill)
                          setPlan({
                            ...plan,
                            research_units: [
                              ...plan.research_units,
                              {
                                id: `U${Date.now()}`,
                                skill,
                                objective: "新增研究角度",
                                priority: 10,
                                depends_on: [],
                                parent_gap_id: null,
                                source_strategy: {
                                  source_names: [],
                                  required_origins: ["internal", "external"],
                                  max_results: 12,
                                  not_before: null,
                                },
                              },
                            ],
                          });
                      }}
                    >
                      <Plus size={14} />
                      增加研究角度
                    </Button>
                    <label className="mt-4 block text-sm">
                      期望产物
                      <Input
                        className={`${input} mt-2`}
                        value={plan.expected_output}
                        onChange={(e) =>
                          setPlan({ ...plan, expected_output: e.target.value })
                        }
                      />
                    </label>
                    <label className="mt-4 block text-sm">
                      计划约束（每行一项）
                      <Textarea
                        className={`${input} mt-2`}
                        value={plan.constraints.join("\n")}
                        onChange={(e) =>
                          setPlan({
                            ...plan,
                            constraints: e.target.value.split("\n"),
                          })
                        }
                      />
                    </label>
                    <p className="text-muted-foreground mt-4 text-xs leading-5">
                      修改后先保存规范化，再确认新版本。未保存的编辑不会随“确认”偷偷提交。
                    </p>
                    <div className="mt-4 flex flex-wrap justify-end gap-2">
                      <Button
                        variant="ghost"
                        className={button}
                        disabled={busy}
                        onClick={() => {
                          void act("plan/reject", {
                            plan_version: run.plan?.plan_version,
                          });
                        }}
                      >
                        拒绝计划
                      </Button>
                      <Button
                        variant="ghost"
                        className={button}
                        disabled={busy}
                        onClick={() => {
                          void act("plan/edit", {
                            plan_version: run.plan?.plan_version,
                            plan,
                          });
                        }}
                      >
                        保存修改并重新审核
                      </Button>
                      <Button
                        variant="ghost"
                        className={primary}
                        disabled={
                          busy ||
                          JSON.stringify(plan) !== JSON.stringify(run.plan)
                        }
                        onClick={() => {
                          void act("plan/approve", {
                            plan_version: run.plan?.plan_version,
                          });
                        }}
                      >
                        <Check size={15} />
                        确认并开始研究
                      </Button>
                    </div>
                  </div>
                )}
                {run.units.length > 0 && !report && (
                  <section className="mb-6 space-y-2" aria-label="研究单元状态">
                    {run.units.map((u) => (
                      <div
                        className="border-border/60 flex items-center justify-between gap-4 rounded-xl border px-4 py-3"
                        key={u.id}
                      >
                        <div className="min-w-0">
                          <div className="text-muted-foreground text-xs">
                            {u.id} · {u.skill}
                            {u.parent_gap_id ? " · 定向补研" : ""}
                          </div>
                          <p className="mt-1 text-sm leading-6">
                            {u.objective}
                          </p>
                        </div>
                        <span className="text-muted-foreground shrink-0 text-xs">
                          {labels[run.unit_statuses[u.id] ?? ""] ?? "等待调度"}
                        </span>
                      </div>
                    ))}
                  </section>
                )}
                {run.gaps.length > 0 && (
                  <details className="border-border mb-5 rounded-xl border p-4">
                    <summary className="cursor-pointer text-sm">
                      证据缺口（{run.gaps.length}）
                    </summary>
                    {run.gaps.map((g) => (
                      <p
                        key={g.gap_id}
                        className="text-muted-foreground mt-3 text-sm"
                      >
                        {g.description}
                      </p>
                    ))}
                  </details>
                )}
                {report && (
                  <article className="border-border rounded-2xl border p-5 md:p-8">
                    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
                      <h3 className="text-muted-foreground text-xs tracking-widest uppercase">
                        研究报告 · v{report.version}
                      </h3>
                      <div className="flex flex-wrap gap-2">
                        {(["md", "html", "docx"] as const).map((format) => (
                          <Button
                            variant="ghost"
                            key={format}
                            className={button}
                            onClick={() => {
                              void api
                                .download(run.run_id, format)
                                .catch((e: unknown) => setError(String(e)));
                            }}
                          >
                            <Download size={14} />
                            {format.toUpperCase()}
                          </Button>
                        ))}
                      </div>
                    </div>
                    <h2 className="mb-8 text-2xl leading-10 font-semibold">
                      {report.report.title}
                    </h2>
                    {report.limitations.length > 0 && (
                      <aside className="mb-6 rounded-xl bg-amber-50 p-4 text-sm text-amber-900">
                        <strong>研究限制</strong>
                        {report.limitations.map((l, i) => (
                          <p key={i} className="mt-2">
                            {l}
                          </p>
                        ))}
                      </aside>
                    )}
                    <h3 className="mb-4 text-lg font-semibold">执行摘要</h3>
                    {renderSegments(report.report.executive_summary)}
                    {report.report.sections.map((s, i) => (
                      <section key={i} className="mt-8">
                        <h3 className="mb-4 text-lg font-semibold">
                          {s.heading}
                        </h3>
                        {renderSegments(s.segments)}
                      </section>
                    ))}
                    <h3 className="mt-8 mb-4 text-lg font-semibold">结论</h3>
                    {renderSegments(report.report.conclusion)}
                    <h3 className="mt-8 mb-4 text-lg font-semibold">
                      参考资料
                    </h3>
                    <div className="grid gap-2 md:grid-cols-2">
                      {report.citations.map((c) => (
                        <Button
                          variant="ghost"
                          key={c.evidence_id}
                          onClick={() => setSelected(c)}
                          className="border-border hover:bg-muted/40 h-auto w-full min-w-0 flex-col items-start rounded-xl border p-3 text-left whitespace-normal dark:hover:bg-neutral-900"
                        >
                          <span className="text-sm">
                            [{c.number}] {c.title}
                          </span>
                          <span className="text-muted-foreground mt-2 block text-xs">
                            {c.origin} · {c.source_level} · {c.publisher}
                          </span>
                        </Button>
                      ))}
                    </div>
                  </article>
                )}
                <ResearchTracePanel
                  key={run.run_id}
                  api={api}
                  runId={run.run_id}
                />
                <details
                  className="border-border mt-6 rounded-xl border p-4"
                  open={!report}
                >
                  <summary className="cursor-pointer text-sm">
                    执行记录{" "}
                    <span className="text-muted-foreground">
                      ({events.length}，保留最近 100 条)
                    </span>
                  </summary>
                  <div
                    className="mt-4 max-h-64 space-y-3 overflow-auto"
                    aria-live="polite"
                  >
                    {events.map((e) => (
                      <div key={e.seq} className="flex gap-3 text-xs">
                        <time className="text-muted-foreground shrink-0 tabular-nums">
                          {new Date(e.at).toLocaleTimeString()}
                        </time>
                        <span>
                          {eventLabels[e.type] ?? e.type}{" "}
                          {typeof e.data.unit_id === "string"
                            ? e.data.unit_id
                            : ""}{" "}
                          {typeof e.data.source_origin === "string"
                            ? `· ${e.data.source_origin}`
                            : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                </details>
              </>
            )}
          </section>
        </div>
      </div>
      <Sheet
        open={selected !== null}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <SheetContent
          aria-label="引用来源详情"
          className="w-full overflow-y-auto p-6 motion-reduce:animate-none sm:max-w-lg"
        >
          <SheetHeader className="p-0 pr-6">
            <SheetTitle>{selected?.title ?? "引用来源详情"}</SheetTitle>
            <SheetDescription>
              {selected?.evidence_id} · {selected?.origin} ·{" "}
              {selected?.source_level}
            </SheetDescription>
          </SheetHeader>
          {selected && (
            <>
              <p className="text-muted-foreground text-xs">
                {selected.publisher} · 发布日期：
                {selected.published_at ?? "未提供"}
              </p>
              <h3 className="mt-4 text-sm font-medium">证据摘要</h3>
              <p className="text-sm leading-7 whitespace-pre-wrap">
                {selected.snippet}
              </p>
              {selected.canonical_url ? (
                <a
                  className="text-sm break-all underline"
                  href={selected.url ?? selected.canonical_url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  查看原始来源
                </a>
              ) : (
                <div className="bg-muted rounded-lg p-4 text-sm">
                  <p className="text-muted-foreground mb-2 text-xs">
                    原生工具调用记录，可通过 Trace 调查；不代表原文已验证
                  </p>
                  <code className="break-all">{selected.source_uri}</code>
                </div>
              )}
              <Button
                variant="outline"
                aria-label="关闭来源详情"
                onClick={() => setSelected(null)}
              >
                关闭来源详情
              </Button>
            </>
          )}
        </SheetContent>
      </Sheet>
    </section>
  );
}
