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
| POST | `/{id}/messages` | 202，同一研究中的计划修改（修改后直接开始）、澄清回答、研究中的更新或报告追问 |
| POST | `/{id}/plan/edit` | 202，规范化新计划并重新等待审核 |
| POST | `/{id}/plan/reject` | 202，拒绝计划 |
| POST | `/{id}/cancel` | 取消；已完成报告不会被覆盖 |
| POST | `/{id}/retry` | 202，恢复 recoverable FAILED，预算不重置 |
| GET | `/{id}/events` | SSE，after / Last-Event-ID 持久事件游标；`Cache-Control: no-store, no-transform` |
| GET | `/{id}/evidences` | 证据目录与 lineage |
| GET | `/{id}/sources` | 已发现来源与实际调用，分别返回 sources/calls |
| GET | `/{id}/activity` | 面向用户的研究时间线、实时状态、搜索/阅读计数和用时 |
| GET | `/favicon?domain=example.com` | 网站图标：需登录；200 返回图片，404 表示没有图标（缓存 1 天）或仍在获取（`no-store`），422 表示域名无效 |
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

edit body：`{"plan_version":1,"plan":{...}}`；plan 包含 goal、title、brief、constraints、assumptions、expected_output、research_units 和 plan_version。单元包含 id、skill、title（短步骤名）、objective、priority、depends_on、source_strategy。依赖必须构成 DAG，Skill/来源必须已注册。保存编辑后重新确认新版本，旧版本返回 409。

新版 UI 使用 `plan/pause` 与 `messages`，不展示 JSON/约束表单；`plan/edit` 保留给显式结构化 API 客户端。pause/resume 的 body 同 approve。快照包括 `server_time`、`auto_start_at`、`auto_start_paused`、`cycle` 和 `conversation`；列表省略完整对话/报告以减少历史加载量。

`AWAITING_PLAN_CONFIRMATION` 的自动启动由服务端触发，与手动批准走同一互斥入口。编辑先清除 deadline；进程重启后暂停尚未批准的计划，要求新的请求上下文，不恢复旧凭据。

## 同一研究中的消息

```json
{"text":"只关注并发与运维","client_message_id":"message-uuid","plan_version":1}
```

编辑/澄清必须携带当前计划版本。相同消息 ID 和内容重试不会重复生成计划，不同内容复用 ID 返回 409。必要澄清使用 `AWAITING_CLARIFICATION`，没有自动启动期限。

审核中的消息修改计划：planner 生成新版本和 `acknowledgement`（对话中显示为 `ack-N` 文本消息），然后直接开始研究，并记录 `plan.auto_started` 事件（`source=revision`）。修订后如仍有澄清问题，则继续等待。

研究中的消息（`RESEARCHING`、`VALIDATING`、`GAP_FOUND`、`RESEARCH_COMPLETE`、`SYNTHESIZING`）作为更新保存到 `steering`，同时追加 `update-<id>` 确认消息，并发出 `conversation.steering` 事件。它不启动新的执行，也不需要凭据；尚未开始的研究单元和报告写作会读取它。进入引用绑定、校验或排版阶段后返回 `RUN_BUSY`。

完成后的追问先进入 `RESPONDING`：解释直接回答；改写只修改现有 Markdown 文档并生成新版本，不重新调用搜索；新证据需求开启新的 `cycle` 和计划。旧报告版本始终保留，读取/导出仍检查当前 owner/ACL。

## 事件与 Trace

事件包含 `seq/type/run_id/at/data`，SSE id 为 seq。Trace 的 data 另包含 trace_id、span_id、parent_span_id、kind、name、status、duration_ms 及可选 payload/error_type。

trace 分页返回 `{"trace_id":"...","items":[...],"next_cursor":123}`。导出每行一个完整事件。started 无对应 ended 可能是活动调用或进程中断，不能推断成功。载荷可能被截断；设置关闭内容采集后仅保存元数据。

普通 SSE 中的 trace 事件不包含 `payload` 明细；只有展开 trace 的请求和受保护导出读取该内容。`activity.tool.started/completed` 用实际调用标识关联 Agent、步骤和来源，不靠前端虚构进度。

`activity` 返回 `{status, started_at, finished_at, elapsed_seconds, counts:{searches,pages_read,steps,steps_done}, current, items}`。`items` 是结构化条目：`plan`、`step`、`note`（研究员给出的可见进展说明；中文请求只展示含中文的说明，提到技能文件或工具名的说明只保留在 Trace 中）、`search`（同一单元连续搜索合并，带查询词与结果域名）、`read`（打开的页面标题/域名）、`tool`、`step_done`（单元摘要）、`step_failed`（非致命失败的步骤）、`gap`、`limited`、`update`、`writing`、`outline`、`section`、`done`、`failed`、`cancelled`。只展示本轮 `cycle`，不包含 Trace 载荷。`counts.steps`/`steps_done` 只统计计划中的原始步骤（完成或失败都算结束），补研步骤不计入。搜索/读取的查询词或 URL 只从运营方声明为 `search`/`read` 角色的工具参数中提取。

`sources` 中的来源包含稳定 ID、URL、domain、connector、`call_ids` 和 `status=discovered`；`calls` 包含实际调用 ID、工具/Agent/单元、状态、耗时与来源 ID。来源分组不改变正文引用顺序。发现链接不等于读取原文，更不等于已被报告引用。

## 证据语义

`E001` 是报告内部 ID，不是外部文档 ID。`provenance=tool_output` 表示真实原生工具调用，`source_uri=mcp-result://...`；原生文件/沙箱等调用为 `origin=runtime`、`tool-result://...`。raw_content_ref 关联 execution/call。receipt/调用证明来源发生过，不自动证明文档日期、独立性或结论正确。前端和导出必须保留该限定。

## 网站图标

`GET /favicon?domain=` 由 Gateway 代取被引用网站的图标，浏览器不直接访问第三方网站。域名必须是普通主机名（不接受 IP、端口、路径），`www.` 会被去掉。依次尝试该主机和上级站点的 `/favicon.ico`，再尝试首页 `<link rel=icon>` 声明的最多 3 个非 SVG 图标。每一次请求和重定向都先做公网地址校验（与原生 web 工具相同，DNS 由 HTTP 客户端再次解析，存在同样的重绑定限制），单个响应不超过 256 KB，只按文件头识别 ico/png/gif/jpeg/webp，SVG、HTML 等一律视为没有图标。命中缓存 7 天，未命中缓存 1 天。首次获取超过 2 秒时返回 `no-store` 的 404，后台继续获取，前端 4 秒后重试一次。响应带 `X-Content-Type-Options: nosniff` 和 `Content-Security-Policy: default-src 'none'`。配置 `favicons: false` 时不发起任何外部请求。

## 步骤失败

单个研究单元的非致命失败（例如 `NATIVE_AGENT_TIMEOUT`）不会让整轮研究失败：快照的 `unit_failures` 记录 `{unit_id: code}`，该单元保存一个置信度为 0、带局限说明的占位结果，并发出 `research.unit.failed`（`unit_id`、`code`、`title`）。依赖它的单元照常调度，局限进入报告。单元结果最多保存 500 条原始证据：超出时先裁掉多余的发现链接和已被替代的运行时副本，保留被结论引用的证据、已读取页面和工具记录，并发出 `research.evidence.trimmed`（`unit_id`、`dropped`、`kept`）；发现链接仍保留在来源表中。结果契约错误区分为 `EVIDENCE_REFERENCE`（引用了未观察到的证据）与 `RESULT_CONTRACT`（超出字段限制，只返回字段路径）。以下情况仍使 run 失败：本批单元全部失败；或出现致命错误——模型认证/权限/计费/未配置、预算或时间耗尽、取消、Agent/Skill/工具未授权或缺失、依赖证据或单元不匹配，以及存储错误。新一轮 `cycle` 清空 `unit_failures`。

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
configuration or permit unknown citations. By default (`allow_limited_report:
true` in settings) exhausted gaps already produce a report with disclosed
limitations and a `report.limitations.auto` event, so this body is needed only
for strict deployments or historical `RESEARCH_GAPS` checkpoints.
An omitted body preserves the current policy. Clients must omit the field for
ordinary retries rather than sending `false`, which records a refusal.
A run without any citable evidence fails with recoverable `NO_EVIDENCE`.

### Report presentation and source policy

ResearchPlan adds `title`, `brief`, `assumptions`, `acknowledgement`,
`report_style` (`brief`, `standard`, `detailed`) and `source_policy`
(`allowed_domains`, `excluded_url_prefixes`, `require_original`). Source
configuration adds `role` (`search`, `read`, `data`). Only fetched pages, records
from non-search tools and legacy document evidence are citable unless the
operator enables `cite_search_results`.

New report versions have `format: "markdown-v2"` and contain:

| Field | Meaning |
|---|---|
| `title` | Report title (also mirrored in `report.title` for older clients) |
| `document` | Verified Markdown with `[[E012]]` evidence markers |
| `display_markdown` | Client rendering with `[n](#citation-E012)` citation links |
| `markdown` / `html` | Exports with numbered references |
| `toc` | Level 2/3 headings in document order |
| `citations` / `citation_map` | Numbered cited pages with `domain`, `evidence_ids` and cleaned `excerpts` |
| `limitations` / `assumptions` | At most five reader-facing caveats and stated assumptions |
| `audit` | Raw limitations, gap IDs and writer repair/removal counts |
| `stats` | `elapsed_seconds`, `searches`, `pages_read`, `citations` |

`GET /report?format=docx` exports markdown-v2 reports from `document` and older
StructuredReport versions from their AST. Several evidence records from one page (URL ignoring scheme, `www.` and a trailing slash) share a visible number, so `citation_map` values need not be unique;
each excerpt keeps its `evidence_id` and `document_hash`. Titles fall back to the first real title in the group, then to the URL.
An externalized native fetch (the host saved a long output under `/mnt/user-data/outputs/.tool-results/`) that the researcher continues with `read_file` is registered as the same page (URL, title, document hash); `browser_get_text` counts as a page read only when the preceding successful `browser_navigate` of that turn identifies the page. Undeclared runtime tool output is never citable on its own. Excerpts in `citations` omit the externalization notice and fetch headers. `display_markdown` and exports escape `$` so prices are not rendered as math.
Read provenance means a native fetch returned an excerpt, not that every page
section was read or that its claims have been independently verified.

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
