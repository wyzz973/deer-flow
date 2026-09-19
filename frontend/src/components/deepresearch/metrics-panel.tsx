"use client";

import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import type { researchApi } from "@/core/deepresearch/api";
import {
  formatCost,
  formatDuration,
  formatPercent,
  formatTokens,
  nodeLabel,
  phaseLabel,
} from "@/core/deepresearch/presentation";
import type { ResearchMetrics } from "@/core/deepresearch/types";
import { cn } from "@/lib/utils";

type MetricsApi = Pick<
  ReturnType<typeof researchApi>,
  "root" | "metrics" | "downloadMetrics"
>;

const STATUS: Record<string, string> = {
  completed: "完成",
  failed: "失败",
  cancelled: "取消",
};

function seconds(value: number | null | undefined) {
  return formatDuration(value) || "0s";
}

/** Like `seconds`, but an unmeasured value stays unknown instead of 0s. */
function measuredSeconds(value: number | null | undefined) {
  return value == null ? "—" : seconds(value);
}

function milliseconds(value: number | null | undefined) {
  if (value == null) return "—";
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`;
}

/** A failure count with its most common causes (HTTP status, exception, empty page). */
function Failures(props: { count: number; causes: Record<string, number> }) {
  const causes = Object.entries(props.causes)
    .slice(0, 2)
    .map(([name, count]) => `${name} ${count}`)
    .join("、");
  return (
    <div>
      <div>{props.count}</div>
      {props.count > 0 && causes && (
        <div className="text-muted-foreground">{causes}</div>
      )}
    </div>
  );
}

/** A count that signals a tuning problem whenever it is above zero. */
function Flag(props: { value: number; tone?: "warn" | "error" }) {
  const raised = props.value > 0;
  return (
    <span
      data-flagged={raised || undefined}
      className={cn(
        raised && "rounded px-1 py-0.5 font-medium",
        raised &&
          (props.tone === "error"
            ? "bg-destructive/10 text-destructive"
            : "bg-amber-500/15 text-amber-700 dark:text-amber-300"),
      )}
    >
      {props.value}
    </span>
  );
}

function Stat(props: { label: string; value: string; detail?: string }) {
  return (
    <div className="bg-muted/40 rounded-xl p-3">
      <p className="text-muted-foreground text-[11px]">{props.label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums">{props.value}</p>
      {props.detail && (
        <p className="text-muted-foreground mt-0.5 text-[11px] leading-4">
          {props.detail}
        </p>
      )}
    </div>
  );
}

function Section(props: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-muted-foreground text-xs font-medium">
        {props.title}
      </h3>
      {props.children}
    </section>
  );
}

function Table(props: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[11px] tabular-nums">
        <thead className="text-muted-foreground">
          <tr>
            {props.head.map((cell) => (
              <th
                key={cell}
                className="py-1 pr-3 font-normal whitespace-nowrap"
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {props.rows.map((row, index) => (
            <tr key={index} className="border-border/60 border-t align-top">
              {row.map((cell, column) => (
                <td key={column} className="py-1.5 pr-3">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Summary({ metrics }: { metrics: ResearchMetrics }) {
  const { time, tokens, cost, tools, agents, metered } = metrics;
  const calls = metrics.model_calls;
  // Runs from before per-call metering only have the budget ledger total.
  const unmetered = "早于逐次计量，无明细";
  const costDetail = !metered
    ? unmetered
    : cost.total == null
      ? "在研究配置 pricing 中填写模型单价后显示"
      : cost.unpriced_models.length
        ? `未定价：${cost.unpriced_models.join("、")}`
        : `${cost.priced_calls} 次调用计价`;
  return (
    <div className="grid grid-cols-2 gap-2">
      <Stat
        label="实际计算"
        value={seconds(time.active_seconds)}
        detail={`总时长 ${seconds(time.wall_seconds)} · 等待 ${seconds(time.waiting_seconds)}`}
      />
      <Stat
        label="Token"
        value={formatTokens(tokens.total)}
        detail={
          metered
            ? `输入 ${formatTokens(tokens.input)} · 输出 ${formatTokens(tokens.output)} · 缓存命中 ${formatPercent(tokens.cache_read_ratio)}`
            : "早于逐次计量，仅有预算账本合计"
        }
      />
      <Stat
        label="预估费用"
        value={formatCost(cost.total, cost.currency)}
        detail={costDetail}
      />
      <Stat
        label="模型调用"
        value={String(calls.count ?? "—")}
        detail={
          metered
            ? `${calls.running ? `进行中 ${calls.running} · ` : ""}失败 ${calls.errors} · p95 ${milliseconds(calls.latency_ms.p95)} · 最大上下文 ${formatTokens(calls.max_input_tokens)}`
            : unmetered
        }
      />
      <Stat
        label="工具调用"
        value={String(tools.count)}
        detail={`搜索 ${tools.searches} · 读取页面 ${tools.pages_read} · 失败率 ${formatPercent(tools.error_rate)}`}
      />
      <Stat
        label="子 Agent"
        value={String(agents.count ?? "—")}
        detail={
          metered
            ? `失败 ${agents.failed} · 取消 ${agents.cancelled} · 最大并行 ${agents.max_parallel}`
            : unmetered
        }
      />
    </div>
  );
}

function Phases({ metrics }: { metrics: ResearchMetrics }) {
  const usage = new Map(
    metrics.breakdown.by_phase.map((row) => [row.key, row]),
  );
  const { time } = metrics;
  const longest = Math.max(1, ...time.phases.map((row) => row.seconds));
  return (
    <>
      <ul className="space-y-2">
        {metrics.time.phases.map((phase) => {
          const row = usage.get(phase.phase);
          // Phases without model calls (checks, binding) show time only.
          const usageText = row
            ? ` · ${formatTokens(row.total_tokens)} tokens${row.cost == null ? "" : ` · ${formatCost(row.cost, metrics.cost.currency)}`}`
            : "";
          return (
            <li key={phase.phase} className="space-y-1">
              <div className="flex justify-between gap-2 text-xs">
                <span>{phaseLabel(phase.phase)}</span>
                <span className="text-muted-foreground tabular-nums">
                  {phase.seconds < 1 ? "<1s" : seconds(phase.seconds)}
                  {usageText}
                </span>
              </div>
              <div className="bg-muted h-1.5 overflow-hidden rounded-full">
                <div
                  className="bg-foreground/60 h-full rounded-full"
                  style={{
                    width: `${Math.max(2, (phase.seconds / longest) * 100)}%`,
                  }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="text-muted-foreground text-[11px] leading-4">
        并行累计：模型 {measuredSeconds(time.model_seconds)} · 工具{" "}
        {seconds(time.tool_seconds)} · 排队{" "}
        {measuredSeconds(time.queue_seconds)}
      </p>
    </>
  );
}

function budgetText({ budget }: ResearchMetrics) {
  const used = [
    ["Token", budget.max_model_tokens, budget.model_tokens_used],
    ["工具", budget.max_tool_calls, budget.tool_calls_used],
    ["时长", budget.max_elapsed_seconds, budget.elapsed_used],
  ] as const;
  const limited = used.filter(([, limit]) => limit != null);
  return limited.length
    ? limited
        .map(([label, , ratio]) => `${label} ${formatPercent(ratio)}`)
        .join(" · ")
    : "不限";
}

function Efficiency({ metrics }: { metrics: ResearchMetrics }) {
  const value = metrics.efficiency;
  const currency = metrics.cost.currency;
  const rows: [string, string][] = [
    ["预算使用", budgetText(metrics)],
    ["每条引用 Token", formatTokens(value.tokens_per_citation)],
    ["每条引用费用", formatCost(value.cost_per_citation, currency)],
    [
      "每条引用计算时长",
      value.active_seconds_per_citation == null
        ? "—"
        : seconds(value.active_seconds_per_citation),
    ],
    [
      "每个读取页面产出引用",
      value.citations_per_page_read == null
        ? "—"
        : `${value.citations_per_page_read} 条`,
    ],
    [
      "每个研究单元搜索",
      value.searches_per_unit == null ? "—" : `${value.searches_per_unit} 次`,
    ],
    [
      "重复工具调用",
      metrics.tools.repeat_calls == null
        ? "未记录"
        : `${metrics.tools.repeat_calls} 次（${formatPercent(value.repeat_tool_call_ratio)}）· 同一 Agent ${metrics.tools.repeat_calls_same_agent}`,
    ],
    ["输入 Token 缓存命中", formatPercent(value.cache_read_ratio)],
    [
      "可复用前缀（缓存命中上限）",
      formatPercent(metrics.tokens.prefix_reuse_ratio),
    ],
    ["格式转换 Token 占比", formatPercent(value.conversion_token_share)],
    ["失败子 Agent Token 占比", formatPercent(value.failed_agent_token_share)],
    ["缓存复用", `${metrics.cache.hits} 次`],
    [
      "重试与修复",
      `转换 ${metrics.research.conversion_retries} · 报告 ${metrics.report.draft_repairs} · 终检 ${metrics.report.validation_retries}`,
    ],
    [
      "报告",
      `${metrics.report.citations} 条引用 · ${metrics.report.sections} 章 · ${metrics.report.tables} 张表`,
    ],
  ];
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
      {rows.map(([label, text]) => (
        <div key={label} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="text-right tabular-nums">{text}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Model work per graph node: where output is cut off or fails validation. */
function Nodes({ metrics }: { metrics: ResearchMetrics }) {
  const currency = metrics.cost.currency;
  return (
    <>
      <Table
        head={[
          "节点",
          "模型",
          "调用",
          "错误",
          "Token",
          "缓存命中 / 可复用",
          "平均输出",
          "被截断",
          "格式重试",
          "P50 / P95 延迟",
          "耗时",
          "费用",
        ]}
        rows={(metrics.breakdown.by_node ?? []).map((node) => [
          <span key="node" className="whitespace-nowrap">
            {nodeLabel(node.node)}
          </span>,
          node.models.length ? node.models.join("、") : "—",
          node.model_calls,
          <Flag key="errors" value={node.model_errors} tone="error" />,
          <div
            key="tokens"
            title={`输入 ${formatTokens(node.input_tokens)} · 输出 ${formatTokens(node.output_tokens)} · 缓存命中 ${formatTokens(node.cache_read_tokens)} · 最大上下文 ${formatTokens(node.max_input_tokens)}`}
          >
            <div>{formatTokens(node.total_tokens)}</div>
            {node.unreported_usage > 0 && (
              <div className="text-muted-foreground whitespace-nowrap">
                {node.unreported_usage} 次未上报用量
              </div>
            )}
          </div>,
          <span
            key="cache"
            className="whitespace-nowrap"
            title="缓存命中：提供方上报的已缓存输入占比。可复用：同一会话里与上一次请求从头相同的提示词占比，是前缀缓存能命中的上限；它低说明请求开头被改写，它高而命中低说明模型服务没有缓存或没有会话粘性。"
          >
            {formatPercent(node.cache_read_ratio)} /{" "}
            {formatPercent(node.prefix_reuse_ratio)}
          </span>,
          formatTokens(node.avg_output_tokens),
          <Flag key="truncated" value={node.truncated} />,
          <Flag key="retries" value={node.retries} />,
          <span key="latency" className="whitespace-nowrap">
            {milliseconds(node.latency_ms.p50)} /{" "}
            {milliseconds(node.latency_ms.p95)}
          </span>,
          milliseconds(node.model_ms),
          formatCost(node.cost, currency),
        ])}
      />
      <p className="text-muted-foreground text-[11px] leading-4">
        被截断：输出碰到上限，调大该节点
        max_tokens；格式重试：JSON/引用未通过校验，降低该节点温度或换模型
      </p>
    </>
  );
}

/** Tasks that already ended in this conversation. Budgets are per task. */
function EarlierTasks({ metrics }: { metrics: ResearchMetrics }) {
  return (
    <>
      <Table
        head={["任务", "结束时间", "Token", "工具调用", "用时"]}
        rows={(metrics.budget.earlier_tasks ?? []).map((task) => [
          task.cycle == null ? "—" : `第 ${task.cycle + 1} 个`,
          task.closed_at
            ? new Date(task.closed_at).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
              })
            : "—",
          formatTokens(task.model_tokens),
          task.tool_calls ?? "—",
          measuredSeconds(task.elapsed_seconds),
        ])}
      />
      <p className="text-muted-foreground text-[11px] leading-4">
        预算按任务计：报告完成后的追问会开启新预算，上方“预算使用”只统计当前任务。
      </p>
    </>
  );
}

/** Cost, time and efficiency of one research run, for tuning and cost control. */
export function ResearchMetricsPanel({
  api,
  runId,
  active,
}: {
  api: MetricsApi;
  runId: string;
  active: boolean;
}) {
  const query = useQuery({
    queryKey: ["research-metrics", api.root, runId],
    queryFn: () => api.metrics(runId),
    refetchInterval: active ? 5000 : false,
  });
  const metrics = query.data;
  if (!metrics) {
    return (
      <p className="text-muted-foreground p-4 text-xs">
        {query.isError ? "暂时无法读取研究指标。" : "正在统计研究成本…"}
      </p>
    );
  }
  const currency = metrics.cost.currency;
  return (
    <div className="space-y-6 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-muted-foreground text-xs">
          成本与效率{metrics.time.in_progress ? " · 统计中" : ""}
        </h3>
        <Button
          variant="ghost"
          size="sm"
          className="text-xs"
          aria-label="导出指标"
          onClick={() => void api.downloadMetrics(runId)}
        >
          <Download className="size-3" />
          JSONL
        </Button>
      </div>
      <Summary metrics={metrics} />
      <Section title="各阶段耗时与 Token">
        <Phases metrics={metrics} />
      </Section>
      {(metrics.breakdown.by_node?.length ?? 0) > 0 && (
        <Section title="按节点">
          <Nodes metrics={metrics} />
        </Section>
      )}
      <Section title="效率">
        <Efficiency metrics={metrics} />
      </Section>
      {(metrics.budget.earlier_tasks?.length ?? 0) > 0 && (
        <Section title="此前任务用量">
          <EarlierTasks metrics={metrics} />
        </Section>
      )}
      {metrics.units.length > 0 && (
        <Section title="研究单元产出">
          <Table
            head={[
              "单元",
              "Token",
              "搜索 / 读取",
              "证据",
              "引用",
              "每条引用 Token",
            ]}
            rows={metrics.units.map((unit) => [
              <div key="unit" className="max-w-56">
                <div className="truncate" title={unit.title ?? unit.unit_id}>
                  {unit.title ?? unit.unit_id}
                </div>
                <div className="text-muted-foreground">
                  {[unit.supplement ? "补充" : "计划", unit.failed && "失败"]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </div>,
              formatTokens(unit.total_tokens),
              `${unit.searches} / ${unit.pages_read}`,
              unit.evidence,
              metrics.report.versions ? unit.citations : "—",
              formatTokens(unit.tokens_per_citation),
            ])}
          />
        </Section>
      )}
      <Section title="子 Agent">
        <Table
          head={["角色 / 单元", "状态", "耗时", "模型 / 工具", "Token", "费用"]}
          rows={metrics.agents.runs.map((run) => [
            <div key="role">
              <div>{run.skill}</div>
              <div className="text-muted-foreground">{run.unit_id}</div>
            </div>,
            `${STATUS[run.status] ?? run.status}${run.error_code ? ` · ${run.error_code}` : ""}`,
            measuredSeconds(run.seconds),
            // Unmetered is unknown, not zero.
            `${run.model_calls ?? "—"} / ${run.tool_calls ?? "—"}`,
            formatTokens(run.total_tokens),
            formatCost(run.cost, currency),
          ])}
        />
      </Section>
      <Section title="工具">
        <Table
          head={["工具", "次数", "失败", "重复", "p95", "返回字符"]}
          rows={metrics.tools.by_tool.map((tool) => [
            tool.role ? `${tool.tool} · ${tool.role}` : tool.tool,
            tool.count,
            <Failures
              key="errors"
              count={tool.errors}
              causes={tool.error_types}
            />,
            tool.repeats ?? "—",
            milliseconds(tool.latency_ms.p95),
            formatTokens(tool.output_chars),
          ])}
        />
      </Section>
      {metrics.tools.failing_domains.length > 0 && (
        <Section title="读取失败最多的站点">
          <Table
            head={["站点", "读取", "失败"]}
            rows={metrics.tools.failing_domains.map((site) => [
              site.domain,
              site.reads,
              <Failures
                key="errors"
                count={site.errors}
                causes={site.error_types}
              />,
            ])}
          />
        </Section>
      )}
      <Section title="模型">
        <Table
          head={["模型", "调用", "输入", "输出", "缓存命中", "费用"]}
          rows={metrics.breakdown.by_model.map((model) => [
            model.key,
            model.model_calls,
            formatTokens(model.input_tokens),
            formatTokens(model.output_tokens),
            formatTokens(model.cache_read_tokens),
            formatCost(model.cost, currency),
          ])}
        />
      </Section>
    </div>
  );
}
