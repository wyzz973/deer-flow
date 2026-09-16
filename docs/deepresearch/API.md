# DeepResearch API

Base: `/api/deepresearch`，由 DeerFlow Gateway 扩展路由提供。生产请求使用宿主登录身份及 CSRF；run owner 不匹配返回 404，可选 access_policy 支持读取时 ACL 复核。演示后端仅接受回环请求。

## 接口目录

| Method | Path | 结果 |
|---|---|---|
| GET | `/capabilities` | mode、ready、Skill、来源、部署预算和 plan_countdown_seconds |
| POST | `/`（实际无尾斜杠亦可） | 202，创建 run/thread；支持 Idempotency-Key |
| GET | `/` | 当前用户最近任务；limit 1–100 |
| GET | `/{id}` | 当前快照、计划、单元状态、预算与报告 |
| POST | `/{id}/plan/approve` | 202，确认指定计划版本 |
| POST | `/{id}/plan/pause` | 暂停指定版本的自动启动，进入 EDITING_PLAN |
| POST | `/{id}/plan/resume` | 放弃编辑并恢复该计划的服务端倒计时 |
| POST | `/{id}/messages` | 202，同一研究中的计划修改、澄清回答或报告追问 |
| POST | `/{id}/plan/edit` | 202，规范化新计划并重新等待审核 |
| POST | `/{id}/plan/reject` | 202，拒绝计划 |
| POST | `/{id}/cancel` | 取消；已完成报告不会被覆盖 |
| POST | `/{id}/retry` | 202，恢复 recoverable FAILED，预算不重置 |
| GET | `/{id}/events` | SSE，after / Last-Event-ID 持久事件游标 |
| GET | `/{id}/evidences` | 证据目录与 lineage |
| GET | `/{id}/sources` | 已发现来源与实际调用，分别返回 sources/calls |
| GET | `/{id}/report?format=json\|md\|html\|docx&version=1` | 指定版本报告/下载；省略 version 为当前完成态报告 |
| GET | `/{id}/trace?after=0&limit=100` | 本地 trace 分页；limit 1–200 |
| GET | `/{id}/trace/export` | owner/ACL 保护的 JSONL 导出 |

## 创建

```json
{
  "query": "比较研发平台的 AI 化路线",
  "constraints": ["区分现状与建议"],
  "source_names": [],
  "budget": {
    "max_iterations": 2,
    "max_units": 12,
    "max_tool_calls": 60,
    "max_elapsed_seconds": 900,
    "max_model_tokens": 120000
  }
}
```

`Idempotency-Key` 同用户同请求可复用，不同请求体复用返回 409。预算不可超过部署上限。`source_names` 表示优先顺序，不代表放开未注册工具或取消来源要求。模型调用先估算预留，再按返回 usage 结算；`model_tokens` 包含已结算用量及未结算/未知用量的预留，`reported_model_tokens` 只累计提供方统计，不承诺精确计费硬限。研究角色使用原生预算中间件提前提醒收尾，不能将全部共享预算视为单个 Researcher 的额度。

## 计划

approve/reject body：`{"plan_version":1}`。

edit body：`{"plan_version":1,"plan":{...}}`；plan 包含 goal、constraints、expected_output、research_units 和 plan_version。单元包含 id、skill、objective、priority、depends_on、source_strategy。依赖必须构成 DAG，Skill/来源必须已注册。保存编辑后重新确认新版本，旧版本返回 409。

新版 UI 使用 `plan/pause` 与 `messages`，不展示 JSON/约束表单；`plan/edit` 保留给显式结构化 API 客户端。pause/resume 的 body 同 approve。快照包括 `server_time`、`auto_start_at`、`auto_start_paused`、`cycle` 和 `conversation`；列表省略完整对话/报告以减少历史加载量。

`AWAITING_PLAN_CONFIRMATION` 的自动启动由服务端触发，与手动批准走同一互斥入口。编辑先清除 deadline；进程重启后暂停尚未批准的计划，要求新的请求上下文，不恢复旧凭据。

## 同一研究中的消息

```json
{"text":"只关注并发与运维","client_message_id":"message-uuid","plan_version":1}
```

编辑/澄清必须携带当前计划版本。相同消息 ID 和内容重试不会重复生成计划，不同内容复用 ID 返回 409。必要澄清使用 `AWAITING_CLARIFICATION`，没有自动启动期限。回答后生成新版本计划。

完成后的追问先进入 `RESPONDING`：解释直接回答；改写使用已有证据生成新报告版本，不重新调用搜索；新证据需求开启新的 `cycle` 和计划。当前不接受运行中追问，返回 `RUN_BUSY`，不得把未应用的要求显示为已生效。旧报告版本始终保留，读取/导出仍检查当前 owner/ACL。

## 事件与 Trace

事件包含 `seq/type/run_id/at/data`，SSE id 为 seq。Trace 的 data 另包含 trace_id、span_id、parent_span_id、kind、name、status、duration_ms 及可选 payload/error_type。

trace 分页返回 `{"trace_id":"...","items":[...],"next_cursor":123}`。导出每行一个完整事件。started 无对应 ended 可能是活动调用或进程中断，不能推断成功。载荷可能被截断；设置关闭内容采集后仅保存元数据。

普通 SSE 中的 trace 事件不包含 `payload` 明细；只有展开 trace 的请求和受保护导出读取该内容。`activity.tool.started/completed` 用实际调用标识关联 Agent、步骤和来源，不靠前端虚构进度。

`sources` 中的来源包含稳定 ID、URL、domain、connector、`call_ids` 和 `status=discovered`；`calls` 包含实际调用 ID、工具/Agent/单元、状态、耗时与来源 ID。来源分组不改变正文引用顺序。发现链接不等于读取原文，更不等于已被报告引用。

## 证据语义

`E001` 是报告内部 ID，不是外部文档 ID。`provenance=tool_output` 表示真实原生工具调用，`source_uri=mcp-result://...`；原生文件/沙箱等调用为 `origin=runtime`、`tool-result://...`。raw_content_ref 关联 execution/call。receipt/调用证明来源发生过，不自动证明文档日期、独立性或结论正确。前端和导出必须保留该限定。

## 错误

401 未登录；403 ACL 撤销/演示 Origin 拒绝；404 不存在或不属于当前用户；409 计划版本、配置指纹、幂等或状态冲突；422 契约/配置无效；429 容量已满。

后台错误写入 run.error `{code,message,recoverable}` 并发出 run.failed。不可恢复预算/配置/最终校验错误应新建或修正配置后新建任务；不能无限点击 retry。

精确请求 schema 见同目录 `openapi.json`（从实际 FastAPI router 生成）。内部 MCP URL、凭据和 Python 插件配置不接受客户端通过研究 API 注册。

### Unbounded resource ceilings

`ResearchBudget.max_model_tokens`, `max_tool_calls`, and `max_elapsed_seconds`
may be `null`, meaning no ceiling for that resource. Defaults remain finite.
Admission rejects a null request when the deployment has a finite ceiling;
clients cannot disable operator limits. Usage and trace recording are unchanged.
Structural `max_units` and `max_iterations` remain bounded integers.

### Explicit acceptance of report limitations

`POST /api/deepresearch/{run_id}/retry` accepts an optional body:

```json
{"allow_limited_report": true}
```

The existing owner and source ACL checks apply. The choice is persisted for this
run and audited as `report.limitations.policy`; it does not change deployment
configuration or permit unknown citations. It can resume a `RESEARCH_GAPS`
checkpoint that otherwise refuses to render an apparently complete report.
Remaining gaps are passed to the writer and displayed with the final report.
An omitted body preserves the current policy and existing retry behavior.

### Report presentation and source policy

ResearchPlan adds optional/defaulted `report_style` (`brief`, `standard`,
`detailed`) and `source_policy` (`allowed_domains`, `excluded_url_prefixes`,
`require_original`). StructuredReport adds optional `comparison_table` with
`headers` and rows of `{label, cells: Segment[]}`. Cells receive the same
reference and scope validation as paragraphs.

Citation records may contain `evidence_ids` and `excerpts` for multiple evidence
records sharing one visible source number. Therefore `citation_map` values need
not be unique. Source records distinguish `discovered` from `read`; evidence may
use `fetched_document` provenance and an optional document hash. Read provenance
means a native fetch returned an excerpt, not that every page section was read
or that its claims have been independently verified.

### Durable operations and safe failure categories

Message/decision acceptance is journaled without request credentials. Internal
pending-operation fields are not returned by the API. Replaying an idempotent
create does not consume another active slot and still rechecks the configured
access policy; a revoked result cannot be read through POST replay. Reusing a
client message ID with different content/plan version or a system message ID is
an idempotency conflict.

The service reports HTTP 503 while stopping/not ready. Native execution can report
`MODEL_AUTH_REQUIRED`, `MODEL_ACCESS_DENIED`, `MODEL_BILLING_REQUIRED`,
`MODEL_RATE_LIMIT`, `MODEL_TIMEOUT`, `MODEL_UNAVAILABLE`, or
`NATIVE_AGENT_TIMEOUT`. Messages do not echo provider response bodies. Unknown
failures remain generic and their local traces retain safe diagnostic metadata.
