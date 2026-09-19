# DeepResearch 架构与工作流

更新时间：2026-09-18。本文描述当前代码的实际设计，是 DeepResearch 架构的权威说明。
`DESIGN_BASELINE.md`、`RUNTIME.md` 是早期设计记录；`NATIVE_RUNTIME.md`、`REUSE_AUDIT.md`
是原生复用专题。接口契约以 [API.md](API.md) 与 [openapi.json](openapi.json) 为准，交接状态见
[HANDOFF.md](HANDOFF.md)，交互对标依据见 [CHATGPT_BENCHMARK_2026-09-16.md](CHATGPT_BENCHMARK_2026-09-16.md)。

## 1. 定位与原则

DeepResearch 是 DeerFlow 的可选扩展：`backend/deepresearch/extension.py` 向 Gateway 注册一个
`ResearchService` 和 `/api/deepresearch` 路由，普通聊天、全局工具与模型配置不受影响。

| 原则 | 含义 |
| --- | --- |
| 复用原生运行时 | 规划、研究、写报告等角色都通过 `SubagentExecutor` 运行真正的 DeerFlow Agent，复用模型工厂、中间件、Skill、沙箱、授权和生命周期；不另建 Agent loop 或中间件链 |
| 配置与 DeerFlow 独立 | 模型、数据源与供应商、MCP 服务、角色、提示词、引擎工具、压缩策略、预算都是研究自己的配置；宿主 `config.yaml` 只负责 `plugins` 注册。研究模型写进一份**私有** AppConfig 副本，运营方配置对象不被修改 |
| 数据源统一成固定工具 | 每个数据源对模型只暴露一套固定参数（`search`/`read`/`data`），背后的供应商按顺序故障切换；MCP 或宿主工具绑定的来源仍然原样交给模型，返回值不做业务映射 |
| 不依赖 JSON mode | 结构化输出用普通文本解析加有限修复；报告正文直接是 Markdown |
| 引用必须真实 | 报告只能引用本次运行中真正读取过、且有资格引用的证据；编号绑定由代码完成，无法验证的陈述被删除，绝不猜测替换 ID 或 URL |
| 交互对标 ChatGPT 深度研究 | 研究简报式计划、倒计时与编辑、修改即开始、研究中更新、活动时间线、全屏阅读器与来源面板 |
| 本地可观测 | 完整 Trace 与每次模型调用的完整提示词/返回存在本地数据库，受 owner/ACL 保护，不需要 LangSmith |
| 配置可编辑、运行可复现 | 管理员在设置页改配置会产生新版本；每个研究任务保存创建时的完整配置快照并全程使用它 |
| 部署边界明确 | 单 worker、SQLite；外部工具至多保证 at-least-once；不宣称 exactly-once 或事实正确 |

## 2. 系统上下文

```mermaid
flowchart TB
  Browser["浏览器：Next.js 研究页面"] -->|REST + SSE| Proxy["Nginx 或 Next rewrite"]
  Proxy --> Router["api.py：/api/deepresearch 路由"]
  Router --> Service["service.py + conversation.py：准入、倒计时、消息、恢复"]
  Service --> Graph["workflow.py：LangGraph 研究流程"]
  Graph --> Runner["runner.py：DeerFlowRunner / DemoRunner"]
  Runner --> Native["native.py：execute_role"]
  Native --> Executor["DeerFlow SubagentExecutor"]
  Executor --> Model["模型提供方（Chat Completions）"]
  Executor --> Tools["原生工具 web_search / web_fetch / read_file ... 与 MCP"]
  Executor --> Skills["Skills 与沙箱"]
  Service --> Store[("research.sqlite3：运行、事件、证据、报告、缓存")]
  Graph --> Checkpoints[("checkpoints.sqlite3：LangGraph 检查点")]
  Native --> Trace["trace.py：本地 span、用量与活动事件"]
  Trace --> Store
  Router --> Favicons["favicons.py：网站图标代取"]
  Favicons --> Store
```

一次研究对应一个 `run`（`run_id`、`thread_id = dr-<run_id>`）。LangGraph 以 `thread_id` 保存检查点；
业务快照、事件与证据保存在研究数据库。两者职责不同，恢复时都需要。

## 3. 代码地图

### 3.1 后端 `backend/deepresearch/`

| 模块 | 职责 |
| --- | --- |
| `extension.py` | DeerFlow 扩展入口：加载配置、创建服务、注册路由 |
| `api.py` | 认证、owner 与 ACL 校验、公开投影、SSE、报告/Trace 导出、活动与图标接口 |
| `service.py` | 创建与幂等、容量准入、执行驱动 `_drive`、计划决策、重试、取消、启动恢复与停止 |
| `conversation.py` | 服务端倒计时、暂停/恢复、消息路由（修改计划、研究中更新、报告追问） |
| `workflow.py` | LangGraph 节点与边：规划、审核、研究、合并、缺口、补研、写作、绑定、终检、发布、追问 |
| `runner.py` | 角色任务的提示词与载荷；研究结果转换；报告大纲/章节/摘要写作与修订；演示 Runner |
| `native.py` | 把角色交给原生 `SubagentExecutor`：工具候选、私有模型配置、线程 ID、Trace 回调、取消与清理 |
| `structured.py` / `output.py` | 普通文本到契约的解析与有限修复（校验器、最终一次的删除式修复） |
| `observations.py` / `sources.py` | 从原生消息、receipts、`deerflow.web_page.v1` 元数据中观察读取页面、工具记录和发现链接 |
| `evidence.py` | 证据合并：稳定 `E###` 编号、去重、lineage、URL 规范化 |
| `report_policy.py` | 来源策略、引用资格（`citable`）、章节与摘要长度目标 |
| `validators.py` | 缺口识别、补研单元生成、单一来源提示 |
| `report.py` | Markdown 报告：标记解析、清理、句级删除、组装、目录、引用绑定、终检、导出（md/html/docx） |
| `render.py` | 引用元数据分组与历史 AST 报告的 Word 导出 |
| `activity.py` | 把事件与调用投影为面向用户的活动时间线与计数 |
| `metrics.py` | 从模型调用、工具调用、子 Agent 记录与 span 汇总成本与效率；也是跨运行对比的命令行工具 |
| `trace.py` | 本地 span、脱敏、模型/工具回调、用量预留与结算、进展说明事件 |
| `store.py` | SQLite 存储、事务、事件序号、原子发布、缓存、图标缓存 |
| `favicons.py` | 网站图标代取：公网地址校验、位图识别、缓存 |
| `config.py` / `contracts.py` | 配置 schema 与领域契约（计划、单元、结果、证据、大纲、错误、状态） |
| `prompts.py` | 全部模型指令的默认值与 `PromptSet` 契约（含上下文压缩模板） |
| `models.py` | `ModelSpec` → 引擎 `ModelConfig`，私有 AppConfig 副本、角色模型解析、压缩策略 |
| `secrets.py` | `$ENV` / `secret:NAME` 引用解析、只写密钥、Trace 脱敏值 |
| `profile.py` | 可编辑字段、设置页覆盖层、运行快照与还原、旧指纹兼容 |
| `providers.py` / `channels.py` / `extract.py` | 供应商预设与错误分类；固定 schema 的数据源工具、健康度与故障切换；格式无关的结果解析 |
| `mcp.py` | 研究自己的 MCP 服务：连接、工具发现缓存、调用与工具源 |
| `audit.py` | 模型调用审计：消息内容寻址、工具与参数归一、脱敏、OpenAI 请求重建 |
| `catalog.py` | 设置页目录：模型服务类型、供应商预设、引擎工具、固定角色、提示词默认值 |
| `live.py` / `demo.py` / `doctor.py` | 隔离真实验收启动器、合成演示应用、配置检查与模型探测（`--probe-model`） |

### 3.2 前端 `frontend/src/`

| 位置 | 职责 |
| --- | --- |
| `app/workspace/deepresearch/page.tsx`、`[run_id]/page.tsx` | 新研究与研究会话路由 |
| `components/deepresearch/research-conversation.tsx` | 复用原生 `ChatSurface`、`MessageList`、`PromptInput`、`ChatBox`，接线所有研究交互 |
| `components/deepresearch/plan-card.tsx` | 计划卡、倒计时环、编辑/取消/开始、进度卡、更新、失败与重试 |
| `components/deepresearch/plan-pending.tsx` | 提交后到计划卡出现之间的等待态：“正在思考” shimmer 与计划卡占位骨架（`waitingPhase` 决定何时显示） |
| `components/deepresearch/report-view.tsx` / `report-reader.tsx` | 报告卡、正文渲染与引用编号、导出菜单、全屏阅读器与悬停目录 |
| `components/deepresearch/sources-panel.tsx` | “来源”（按域名分组的引用）与“活动”（研究时间线）页签 |
| `components/deepresearch/citation-preview.tsx` | 引用悬浮卡（站点、两行标题、两行摘录，多摘录 ← → 切换，`basis` 为检索摘录时标“摘录”）与网站图标 `SiteIcon` |
| `components/deepresearch/metrics-panel.tsx` | “指标”页签：耗时、Token、费用、调用、子 Agent 与效率 |
| `components/deepresearch/trace-panel.tsx` | 本地 Trace 浏览与导出 |
| `components/deepresearch/llm-calls-panel.tsx` / `llm-call-dialog.tsx` | “LLM 调用”审计：按阶段与轮次分组、逐次查看提示词/返回/工具定义/参数/原始 JSON |
| `components/deepresearch/request-card.tsx` | 改写后的研究请求卡（`rewrite` 消息） |
| `components/deepresearch/research-settings.tsx` + `settings/` | 研究设置页：模型、角色、提示词、数据源与供应商、MCP、运行参数、密钥与历史 |
| `components/deepresearch/research-history.tsx` / `research-gallery.tsx` | 侧栏研究历史；空白页“推荐/报告” |
| `components/deepresearch/workbench.tsx` | 兼容旧入口的别名导出 |
| `core/deepresearch/api.ts` / `hooks.ts` / `events.ts` | 研究 API 客户端、查询与 SSE、事件解析与快照合并 |
| `core/deepresearch/presentation.ts` / `types.ts` / `trace-model.ts` | 展示计算、类型、Trace 模型 |

### 3.3 配置、Skill 与测试

- `deepresearch.example.yaml`：研究配置示例；`examples/deepresearch/`：宿主配置片段、原生 web 来源、Skills。
- `tests/deepresearch/`：后端契约、工作流、恢复、证据、报告、活动、图标等测试。
- `frontend/tests/unit/**/deepresearch/`：前端展示、hooks、计划卡、活动面板、引用预览测试。

## 4. 运行状态

`RunStatus`（`contracts.py`）：

```mermaid
stateDiagram-v2
  [*] --> CREATED
  CREATED --> PLANNING
  PLANNING --> AWAITING_PLAN_CONFIRMATION
  PLANNING --> AWAITING_CLARIFICATION
  AWAITING_PLAN_CONFIRMATION --> EDITING_PLAN: 点击编辑
  EDITING_PLAN --> AWAITING_PLAN_CONFIRMATION: 放弃编辑
  AWAITING_PLAN_CONFIRMATION --> PLANNING: 对话修改
  EDITING_PLAN --> PLANNING: 对话修改
  AWAITING_CLARIFICATION --> PLANNING: 回答澄清
  AWAITING_PLAN_CONFIRMATION --> RESEARCHING: 倒计时结束或开始
  PLANNING --> RESEARCHING: 修改后直接开始
  RESEARCHING --> VALIDATING
  VALIDATING --> GAP_FOUND
  GAP_FOUND --> RESEARCHING: 补研
  VALIDATING --> RESEARCH_COMPLETE
  RESEARCH_COMPLETE --> SYNTHESIZING
  SYNTHESIZING --> CITATION_BINDING
  CITATION_BINDING --> FINAL_VALIDATING
  FINAL_VALIDATING --> SYNTHESIZING: 有限重写
  FINAL_VALIDATING --> RENDERING
  RENDERING --> COMPLETED
  COMPLETED --> RESPONDING: 追问
  RESPONDING --> COMPLETED: 直接回答
  RESPONDING --> SYNTHESIZING: 改写报告
  RESPONDING --> PLANNING: 需要新研究
  COMPLETED --> [*]
  FAILED --> RESEARCHING: 可恢复时重试
```

任意非终态都可能进入 `FAILED`（带 `{code, message, recoverable}`）或 `CANCELLED`。

| 集合 | 状态 | 用途 |
| --- | --- | --- |
| `TERMINAL` | `COMPLETED`、`FAILED`、`CANCELLED` | 事件流在终态且无活动任务时结束 |
| `REVIEW_STATUSES` | `AWAITING_PLAN_CONFIRMATION`、`EDITING_PLAN`、`AWAITING_CLARIFICATION` | 计划决策与计划修改消息的前提 |
| `STEERABLE_STATUSES` | `RESEARCHING`、`VALIDATING`、`GAP_FOUND`、`RESEARCH_COMPLETE`、`SYNTHESIZING` | 消息作为研究中更新（steering） |

`CITATION_BINDING`、`FINAL_VALIDATING`、`RENDERING` 期间的新消息返回 `RUN_BUSY`，不假装已采纳。

## 5. LangGraph 工作流

### 5.1 图结构

```mermaid
flowchart LR
  START((START)) -->|input_mode = follow_up| FU[follow_up]
  START -->|其他| RW[rewrite]
  RW --> PL[planner]
  FU -->|answer| E((END))
  FU -->|revise| SY[synthesis]
  FU -->|research| RW
  PL --> RV[plan_review]
  RV -->|edit| RW
  RV -->|approve| DI[dispatch]
  RV -->|reject| RJ[rejected] --> E
  DI --> ME[evidence_merge] --> VA[validator]
  VA -->|GAP_FOUND| SU[supplement] --> DI
  VA -->|RESEARCH_COMPLETE| SY
  SY --> CB[citation_binder] --> FV[final_validator]
  FV -->|有错误且未超修复次数| SY
  FV -->|通过| RE[renderer] --> E
```

每个节点都包在 `traced()` 中，产生 `node` 类型的本地 span。`service._drive` 以
`recursion_limit=200` 调用 `graph.ainvoke`，并关闭 LangSmith 自动追踪。

### 5.2 节点说明

| 节点 | 状态 | 做什么 | 关键事件 | 持久化与缓存 |
| --- | --- | --- | --- | --- |
| `rewrite` | `PLANNING` | 把整段对话改写成一条完整的研究请求 `ResearchRequest`（`user_query`、可选 `acknowledgement`、至多 3 个澄清问题），对标 ChatGPT 调用 Deep Research App 时的 `user_query`。修改计划或追问触发新研究时，从上一版请求出发合并本次修改并写确认话术。模型未按契约输出时退化为纯文本请求并发 `research.request.prose`（回复是残缺 JSON 时只取其中的 `user_query`）。`nodes.rewrite.enabled: false` 时不调用模型：直接用用户原话，修改计划时把改动追加到上一版请求 | `research.request.rewritten` | 写入 `request`；追加 `request-N` 对话消息（`kind: rewrite`） |
| `planner` | `PLANNING` | 调用 `deepresearch` 角色把改写后的请求变成 `ResearchPlan`：研究简报 `brief`（即改写后的请求）、短标题、`plan_min_units`–`plan_max_units` 个带短标题的单元（只规划 `available_sources` 能回答的内容）、前提假设、报告风格、来源策略；计划不合规（未知角色、未知或已停用的数据源、超出单元上限）时带原因重试，最后一次由 `runner.fit_plan` 修正而不是失败；对话修改时写 `acknowledgement` 并设置 `auto_start`；`require_dual_source: false` 时由 `Settings.fit_origins` 去掉没有来源可用的 `required_origins` | `plan.created` / `plan.updated` | 缓存键 `plan:`；写入 `plan` 与 `units`；追加 `ack-N` 与 `plan-N` 对话消息 |
| `plan_review` | 等待确认 | `auto_start` 时记录 `plan.auto_started`（`source=revision`）并直接批准；否则 `interrupt` 等待决策 | `plan.waiting_confirmation`（由服务写入）、`plan.auto_started` | 中断前无副作用，恢复时节点重跑 |
| `dispatch` | `RESEARCHING` | 按依赖分批并行（`max_concurrency`）运行研究单元；已提交结果直接复用；非致命失败降级为占位结果；致命错误或整批失败使运行失败 | `research.unit.started` / `completed` / `failed` | `research_unit` 结果；`unit_statuses`、`unit_failures` |
| `evidence_merge` | — | `merge_results` 按计划顺序把原始证据合并为稳定的 `E###`，生成 `BoundFinding` 与 lineage | `evidence.pool.updated` | `research_evidence`；`evidence_count` |
| `validator` | `VALIDATING` | 计算可引用证据集合与缺口；无缺口则完成；只有 `supplement_gap_codes` 里的缺口类型会触发补研；补研预算（轮数、单元数、研究 Token、研究时间）耗尽或结果饱和时：没有任何发现引用到可引用证据则报 `NO_EVIDENCE`，否则默认带局限继续。饱和与 `NO_EVIDENCE` 都按“被发现引用的可引用证据数”判断，不看证据池大小 | `validator.passed` / `validator.gap_found` / `report.limitations.auto` | `research_gap`；`gaps`、`limitations` |
| `supplement` | — | 按缺口严重度生成补研单元（`S<iter>-<hash>`，依赖原单元）；截断的缺口发出事件 | `research.supplement.deferred` | `units`、`iteration` |
| `synthesis` | `SYNTHESIZING` | 新报告调用 `write_report`；报告追问的改写调用 `revise_report` | `report.synthesizing`、`report.outline.ready`、`report.section.*`、`report.draft.repair` | 缓存键 `synthesis:…:retry-N` 及大纲/章节/摘要各自的缓存 |
| `citation_binder` | `CITATION_BINDING` | `report.bind` 把标记映射为页面编号 | `citation.bound` | 状态内 `citation_map` |
| `final_validator` | `FINAL_VALIDATING` | `report.validate`：未知或不合格引用、数字引用、URL、无引用、无标题；有错误时回到写作，超过 `max_synthesis_repairs` 报 `FINAL_VALIDATION` | — | `final_errors`、`synthesis_repairs` |
| `renderer` | `RENDERING` | 生成 `markdown-v2` 报告体与统计，原子发布 | `report.completed`（发布事务内） | `research_report` 新版本、对话报告消息、`COMPLETED` |
| `follow_up` | `RESPONDING` | 对完成后的消息调用 `respond`：`answer` 直接回复；`revise` 改写现有文档；`research` 开启新 `cycle` | `conversation.answered`、`research.cycle.started` | 缓存键 `followup:<message_id>`；新 cycle 清空单元、缺口、局限和更新 |

### 5.3 研究单元失败语义

`FATAL_UNIT_ERRORS`：`MODEL_AUTH_REQUIRED`、`MODEL_ACCESS_DENIED`、`MODEL_BILLING_REQUIRED`、
`MODEL_NOT_CONFIGURED`、`BUDGET_EXHAUSTED`、`TIME_BUDGET`、`RUN_CANCELLED`、`AGENT_NOT_CONFIGURED`、
`SKILL_DENIED`、`TOOL_DENIED`、`NATIVE_TOOL_MISSING`、`MCP_TOOL_MISSING`、`DEPENDENCY_EVIDENCE`、
`UNIT_MISMATCH`。非 `Exception`、`OSError` 与 `sqlite3.Error` 也视为致命。

收尾类错误 `RESEARCH_BUDGET_SPENT`（研究 Token 用尽）与 `RESEARCH_TIME_SPENT`（研究时间用尽）不是故障：
该单元降级，整批都是这类错误时运行继续并写报告。整批因其他非致命原因失败时，只有此前已有任何发现才继续，否则运行失败。

其他错误（例如 `NATIVE_AGENT_TIMEOUT`、`RESULT_CONTRACT`）只让该单元失败：保存置信度为 0、
写明“研究步骤未能完成”的占位结果，记录 `unit_failures` 与 `research.unit.failed`，依赖它的单元照常调度，
局限进入报告。若某单元的结果已经提交而后续记账失败，恢复时把它当作成功复用，不重跑研究。

## 6. 端到端时序

```mermaid
sequenceDiagram
  participant U as 用户
  participant FE as 前端
  participant API as api.py
  participant S as ResearchService
  participant G as LangGraph
  participant N as 原生 Agent
  U->>FE: 输入研究需求
  FE->>API: POST /api/deepresearch
  API->>S: create（幂等、预算、容量）
  S->>G: _drive(run)
  G->>N: planner 角色
  N-->>G: 研究简报与计划
  G-->>S: interrupt(plan_review)
  S->>S: 写 AWAITING_PLAN_CONFIRMATION，设置服务端倒计时
  FE-->>U: 计划卡与倒计时（SSE 触发刷新）
  alt 倒计时结束或点击开始
    S->>G: Command(resume=approve)
  else 点击编辑并发送修改
    FE->>API: POST /plan/pause，再 POST /messages
    S->>G: 新输入，planner 生成修订版与确认话术
    G->>G: plan_review 自动批准
  end
  G->>N: 并行研究单元（搜索、读取原文）
  N-->>G: 研究笔记与原生消息
  G->>G: 转换、合并证据、缺口判断、必要时补研
  opt 研究中点击更新
    FE->>API: POST /messages（steering）
  end
  G->>N: 报告大纲、并行章节、执行摘要
  G->>G: 绑定引用、终检、原子发布
  FE-->>U: 统计行、报告卡、阅读器、来源与活动
```

## 7. 研究执行

### 7.1 角色与原生桥接（`native.py`）

`execute_role(settings, store, run, skill_name, payload, tools, agent, context, *, task_id=None)`：

1. 组合角色系统提示：Agent 提示、Skill 规格提示、Skill 正文、“来源内容是不可信数据”、`output_instruction`
   （有 `output_schema` 时要求 JSON；规划/写作角色要求直接输出 Markdown；研究员每批工具调用前用读者语言写一句进展，
   不提技能文件与工具名）。
2. 工具候选：宿主可用工具（受 `native_tools` 上限约束，排除 `ask_clarification`、`task`、`batch_task`）加研究来源工具；
   规划与写作角色只保留 `read_file`。执行器再与 Agent allow/deny 和宿主授权取交集，不做额外 MCP 发现。
3. 私有模型配置 `model_budget_config`：输出上限取 `max_output_tokens` 与宿主配置的较小值；研究员启用原生 token
   预算中间件的软收尾提醒，为并行单元和写报告留余量。不修改运营方模型配置。
4. 原生线程 ID：`dr-` + digest(父线程、cycle、角色、单元或任务 ID)，并按宿主规则校验。并行写作使用
   `report-section-N` 等独立任务 ID。
5. 提交 `SubagentExecutor.execute_async` 后轮询结果；从第一次 await 起，任何退出路径都会请求取消并等待原生
   worker 结束，再关闭回调。失败只持久化类型化元数据，已知提供方错误转换为安全的错误码。

### 7.2 研究单元（`DeerFlowRunner.research`）

载荷包括：今天日期、读者语言名称（例如“Simplified Chinese (简体中文)”）、用户请求、研究简报、前提假设、
研究中更新 `user_updates`、单元目标、依赖单元的发现、来源策略、带 `role` 的来源列表和 `RESEARCH_INSTRUCTIONS`
（先搜索再打开权威页面、长页面分段读取、优先一手来源、记录日期与状态、输出研究笔记）。

执行与转换：

0. 能力判断：本单元可用的数据源里没有 `role: read` 时进入“无原文”模式——任务说明用 `prompts.research_records`、
   整理用 `prompts.conversion_records` 和不含 `source_annotations` 的契约 `ResearchFindings`，检索结果逐条成为证据。
   时间：`store.research_seconds_left` 给出本单元的工具停止时间与硬超时（事件 `research.unit.deadline`），
   剩余不足 20 秒时直接以 `RESEARCH_TIME_SPENT` 降级。
1. 缓存键 `native-unit:` + digest(cycle、单元、依赖)。成功的原生执行先缓存，格式修复不会重跑研究。
2. `research_observations` 把消息与 receipts 投影为 `RawEvidence` 和观察目录。
3. 补研单元只导入父单元已保存发现所引用的证据，并核对依赖发现与保存结果一致。
4. 计算可引用 ID：未被取代、满足来源策略、`citable` 为真；结论写回证据（`RawEvidence.citable`），
   缺口判断、写作和终检沿用这个结论而不再按部署级设置重新判断。
5. `convert_answer` 把笔记转为 `ResearchAnalysis`：校验器拒绝未知 ID 并给出精确反馈；最后一次尝试时删除无法验证的引用
   （`research.output.pruned`），不猜测替换。发现条数受 `max_findings_per_unit` 约束（提示词要求 + 超出截断）；
   回复被输出上限截断时，重试消息要求“缩短”，而不是原样重试。
6. 注解引文必须在原始工具输出中出现（`ground_source_annotations`）。
7. `bound_evidences` 把结果控制在 500 条原始证据内：保留被引用的证据、已读取页面和工具记录，先裁掉多余的发现链接，
   再裁已被取代的运行时副本（`research.evidence.trimmed`）。
8. 构造 `ResearchResult`；契约错误区分 `EVIDENCE_REFERENCE`（引用了未观察的证据）与 `RESULT_CONTRACT`（超出字段限制）。

### 7.3 观察到的证据（`observations.py`）

| provenance | 来源 | 默认可引用 |
| --- | --- | --- |
| `fetched_document` | 声明为 `read` 的原生抓取返回的页面（`deerflow.web_page.v1`）；被 `read_file` 续读的外置化长页面；确认导航后的 `browser_get_text` | 是 |
| `tool_output` | 声明为 `data` 的来源工具（例如 MCP 业务记录）返回 | 是 |
| `tool_output`（search 角色） | 搜索工具的结果正文 | 仅 `cite_search_results: true` |
| `observed_source` | 任意工具输出中出现的链接（每个输出最多 100 条） | 仅 `cite_search_results: true` |
| 运行时工具输出 | 未声明的工具（文件、命令行、未确认页面的浏览器输出） | 否 |

宿主 `ToolOutputBudgetMiddleware` 会把超长工具输出外置到 `/mnt/user-data/outputs/.tool-results/*.log`；
研究员随后用 `read_file` 读取该文件时，读取内容登记为原网页（URL、标题、文档哈希），匿名文件副本标为已取代。
不会从文件内容推断 URL。

### 7.4 证据合并（`evidence.py`）

`merge_results` 按计划顺序处理结果，身份键为（origin、source_name、canonical_url 或 source_uri、内容哈希）。
同一身份复用已有 `E###` 并追加单元 ID；相同的有序输入总是得到相同编号。URL 规范化去掉跟踪参数与片段，保留业务参数。

### 7.5 缺口与补研（`validators.py`）

缺口类型与优先级：`coverage`（单元没有可引用发现）、`unsupported`、`missing-internal` / `missing-external`
（部署要求双来源时）、`date`、`open-questions`（研究员列出的可公开检索的问题）。用户私有背景
（`assumptions_needed`）只成为报告假设，不算缺口；单一站点支持的高风险结论只要求写作时加限定（`single_source`）。

补研单元按缺口严重度排序并受 `max_units` 限制，被截断的缺口发出 `research.supplement.deferred`。
补研单元按 `depends_on[0]` 归属原单元；`open-questions` 每个原单元只触发一轮补研（研究员总能再想出一个问题，
否则补研永远跑满轮数）；`supplement_gap_codes` 决定哪些缺口类型值得补研，其余直接成为局限。
达到 `max_iterations`、单元上限、研究 Token 或研究时间用尽，或缺口签名不变且被引用的可引用证据不再增加时停止补研：
有可引用证据时默认写带局限的报告（`allow_limited_report: true`，事件 `report.limitations.auto`）；
严格部署需 owner 通过重试同意；没有可引用证据时失败为 `NO_EVIDENCE`。

## 8. 引用资格与来源策略

- `SourceSpec.role`（`search` / `read` / `data`）由运营方声明，`report_policy.citable` 据此判断。
- `SourcePolicy` 来自用户计划：`allowed_domains`、`excluded_url_prefixes`、`require_original`（只允许读取过的原文）。
- `eligible_evidence(pool, policy, settings)` 同时约束研究转换、缺口判断、写作输入和终检。
- 检索结果能否引用只有一条规则（`report_policy.results_citable`）：运营方打开 `cite_search_results`，或者没有任何启用的
  `role: read` 数据源（拿不到原文时，摘录就是证据）。研究单元没有读取工具时同样成立，并写入 `RawEvidence.citable`。
  这类引用在参考资料里标注“检索摘录，未读取原文”（`citations[].basis`：`page` / `record` / `search excerpt`），
  写作模型收到的证据目录也带这个 `kind`。此时 `require_original` 无法满足，改为在局限里说明而不是 `NO_EVIDENCE`。
- 页面身份保留单页应用的路由片段（`#/doc/5`、`#!/x`），普通锚点仍然忽略；没有 URL 的记录按
  `(来源, document_hash)` 归并，参考资料里显示来源名而不是内部的 `tool-result://`。
- 发现链接、读取原文、报告引用是三件不同的事，界面和计数都分开，不把发现当成“已核实”。

## 9. 报告生成

### 9.1 写作流水线（`DeerFlowRunner.write_report`）

```mermaid
flowchart LR
  F["发现与可引用证据"] --> O["大纲 ReportOutline"]
  O --> S1["章节 1"]
  O --> S2["章节 2"]
  O --> Sn["章节 N"]
  S1 --> SUM["执行摘要"]
  S2 --> SUM
  Sn --> SUM
  SUM --> A["组装 Markdown 文档"]
```

1. **大纲**：输入研究简报、原单元、单元摘要、编号发现（含 `high_risk`、`single_source`）、前提假设、原始局限、章节数量范围。
   校验要求每个原单元都被覆盖、章节数不超过 `max_report_sections`，且不得规划执行摘要、研究范围与局限、参考来源这类由系统组装的章节；
   最后一次尝试由 `tidy` 删除这类章节并把单元覆盖并入其他章节。标题去掉“一、”“1.”等编号。缓存键 `report-outline:`。
2. **章节**：按 `writer_concurrency`（未设置时为 `max_concurrency`）并行，每节只拿到本节单元（补研映射回原单元）的发现与证据目录，使用独立原生任务 ID，
   缓存键 `report-section:`。写作要求：先给判断、跨来源综合、事实句紧跟 `[[E012]]` 标记、保留状态与日期、
   区分厂商宣称与独立证据、需要时使用表格或 Mermaid。
3. **摘要**：基于章节草稿写执行摘要，只复制草稿中已有的标记。缓存键 `report-summary:`。
   `nodes.summary.enabled: false` 或摘要为空时，用大纲的关键结论作为摘要。
4. **修复与清理**（`_markdown`）：先用精确反馈修复一次（`report.draft.repair`），再 `sanitize`：删除含未知或不合格标记的句子、
   列表项或表格单元格内容，去掉链接、裸 URL 与数字引用，并在 `audit` 中计数。`clean_answer` 去掉包裹代码块、重复标题、
   首行写作元话语和描述生成过程的段落（例如“未新增证据 ID”；带有效标记的段落视为正文保留）。校验前先归一化：
   只有闭合标签 `</think>` 的推理内容、写法接近的标记（`[[e001]]`、`[[E001 E002]]`、`【E001、E002】`、`（E001）` 改写为标准形式，
   `[[E12]]` 这类没有三位编号的算畸形标记并报错）、被输出上限截断而未闭合的代码围栏（Mermaid 丢弃，其他补齐，
   `audit.cut_off_answers` 计数）、控制字符，以及任何不指向文档内部的链接和图片（包括 `//host/x.png`、引用式链接定义、`mailto:`）。
   删除句子时不按分号切分（分号前半句共用同一个引用），并配平被拆开的 `**`。
5. **组装**：`# 标题`、`## 执行摘要`、各章节、`## 研究范围与局限`（前提假设与最多 5 条合并局限）。长度只是目标，不因超长失败。
   大纲模型写的标题、关键结论、假设与局限在组装前用 `plain_text` 去掉链接、URL、数字引用和标记
   （否则一句“未能打开 https://…”就会让终检失败并整份重写）。局限由 `runner.reader_caveats` 保证非空：
   大纲没给局限时回退到原始局限；单元失败、提前收尾这类系统记录的局限始终保留；修复后仍为空的章节不进入报告并写成一条局限，
   全部章节都为空时报 `REPORT_EMPTY`。

对话式改写调用 `revise_report`，在现有文档上最小修改，标记同样校验与清理；不会为改写重新搜索。
整篇重写可能被输出上限截断：改写结果丢失两个以上二级章节且长度不足原文六成（而用户并没有要求删减）时，
拒绝发布并保留原报告（`REPORT_REVISION_TRUNCATED`，事件 `report.revision.rejected`）。

### 9.2 绑定、终检与导出（`report.py`）

- `bind(document, pool)`：按首次出现给页面编号。同一页面（忽略协议、`www.` 与结尾斜杠，查询参数不同视为不同页面）
  共享编号；没有 URL 的记录各自编号。未知 ID 抛错，交给终检重写。
- `validate`：检查未知或不合格引用、数字引用和 URL、存在可引用证据却没有引用、缺少标题。
- `display_markdown`：`[n](#citation-E012)` 链接，前端变成引用编号；`$` 转义，避免被当成公式。
- `citations`：每个编号包含域名、`evidence_ids` 与每条摘录（清理后的文本和各自的 `document_hash`）；
  标题取组内第一个真实标题，否则显示去掉协议的 URL。
- 导出：Markdown（`[n](#ref-n)` 与参考文献）、HTML（markdown-it 渲染，引用上标，导出响应带严格 CSP）、Word（python-docx，
  标题、列表、表格、代码块与参考文献）。历史 AST 报告由 `render.docx_report` 导出。

### 9.3 发布内容

`renderer` 发布 `format: "markdown-v2"` 报告：`title`、`document`、`display_markdown`、`markdown`、`html`、`toc`、
`citations`、`citation_map`、`limitations`、`assumptions`、`audit`（原始局限、缺口 ID、修复与删除计数）、
`stats`（用时、搜索、读取页面、引用数）、`demo`。发布键不包含 `stats`。报告版本、对话中的报告消息、`COMPLETED`
和 `report.completed` 在同一 SQLite 事务写入。

## 10. 对话与控制面

### 10.1 计划确认

- 倒计时由服务端拥有：进入 `AWAITING_PLAN_CONFIRMATION` 时写 `auto_start_at` 并挂起定时任务；到期后通过与手动批准相同的互斥入口启动。
  浏览器不启动研究，刷新与多标签页不会获得新的倒计时。
- `plan/pause` 进入 `EDITING_PLAN` 并清除期限；`plan/resume` 恢复倒计时；`plan/approve` / `plan/reject` / `plan/edit` 需要匹配 `plan_version`。
- 对话中的计划修改视为批准：planner 生成修订版与确认话术后直接开始；仍有澄清问题时继续等待。结构化 `plan/edit` 保持重新审核的语义。

### 10.2 消息路由（`conversation.message`）

| 当前状态 | 行为 |
| --- | --- |
| `REVIEW_STATUSES` | 持久化用户消息与 `pending_operation`，进入 `PLANNING`，以修订输入重新运行图 |
| `STEERABLE_STATUSES` | 追加用户消息与确认消息 `update-<id>`，写入 `steering`，发出 `conversation.steering`；不启动新执行、不需要凭据 |
| `COMPLETED` | 进入 `RESPONDING`，由 `follow_up` 决定回答、改写或开启新研究 |
| 其他 | `RUN_BUSY` |

同一 `client_message_id` 与相同内容的重试是幂等的；相同 ID 不同内容返回冲突。系统消息 ID 不能作为客户端幂等键。

### 10.3 重试、取消、重启

- `retry`：只接受可恢复失败、owner 明确同意带局限（`allow_limited_report: true` 且错误为 `RESEARCH_GAPS`）或
  `FINAL_VALIDATION`。终检重试重置修复次数并推进草稿缓存代数，避免复用被拒绝的草稿。普通重试不得发送 `false`。
- `cancel`：先持久化取消请求作为屏障，再取消并等待原生 worker 与数据库 I/O 收尾，单元状态标为已取消；已完成报告不会被覆盖。
- 启动恢复：带取消请求的非完成运行标为 `CANCELLED`；待确认计划暂停倒计时（不保存旧凭据）；其他非终态标为
  `FAILED/PROCESS_INTERRUPTED`（可恢复）。停止服务时拒绝新执行并等待活动任务结束。
- 已接受但尚未执行的决策与消息以不含凭据的 `pending_operation` 记录；恢复时比较检查点中的 `operation_id`，
  只重放未应用的输入。新 cycle 由消息身份约束，节点重放不会重复推进。

## 11. 持久化

### 11.1 研究数据库 `research.sqlite3`

| 表 | 内容 |
| --- | --- |
| `research_run` | 运行 JSON 快照：owner、请求幂等键与哈希、状态、计划、单元、对话、用量、报告、错误、待执行操作 |
| `research_unit` | 单元结果（按 cycle 与单元 ID，带输入哈希） |
| `research_evidence` / `research_gap` | 证据池与缺口 |
| `research_report` | 按版本的不可变报告 |
| `research_event` | 单调 `seq` 的事件与幂等键（SSE 与活动的来源） |
| `research_cache` | 计划、原生执行、转换、大纲、章节、摘要、写作、追问等阶段缓存 |
| `research_source` / `research_tool_call` | 发现的来源与原生工具调用（状态、耗时、角色、查询词或 URL） |
| `research_favicon` | 网站图标缓存（按域名，与运行无关） |
| `research_model_call` | 每次模型调用的阶段、用途、角色、单元、Token 明细、耗时、状态与上下文大小 |
| `research_agent_run` | 每次原生子 Agent 执行的状态、耗时、模型与工具调用计数、Token 汇总 |

LangGraph 检查点在 `checkpoints.sqlite3`。运营日志轮转写入数据目录。进程锁 `worker.lock` 阻止多个 worker 使用同一目录。
备份与迁移需要同时考虑两个数据库；多 worker 与分布式执行不在当前边界内。

### 11.2 幂等与原子性

- 创建请求按（owner、Idempotency-Key）去重，重放仍重新检查访问策略，不占用新的执行名额。
- 单元结果、原生执行和各写作阶段都有内容寻址的缓存键，重试不会重复完成过的工作。
- 报告发布是单事务；事件键包含 cycle，避免新一轮研究与旧事件冲突。
- 外部工具至多保证 at-least-once：进程可能在远端成功后、结果落库前崩溃。

## 12. 可观测性

### 12.1 本地 Trace（`trace.py`）

workflow、节点、原生 Agent、模型调用、工具调用、结构化转换都有 span（`trace.started` / `trace.ended`），
带父子关系、耗时、状态与有界且脱敏的载荷（`trace_capture_content`、`trace_max_chars`）。普通 SSE 中不包含载荷；
`GET /{id}/trace` 分页读取，`/trace/export` 导出 JSONL，均受 owner/ACL 保护。回调与原生子循环无关；
缺失终态的历史子 span 仅在祖先已结束时补记中断，不伪造成功。

模型用量先按估算预留（`usage.reserved`），拿到提供方用量后结算（`usage.settled`）；未知用量保留预留。
工具与 token 超出预算时报 `BUDGET_EXHAUSTED`。

### 12.1.1 模型调用审计（`audit.py`）

`llm_audit: true`（且 `trace_capture_content` 开启）时，每次模型调用都在 `on_chat_model_start` 记下归一化后的请求：
消息按 sha256 内容寻址并 zlib 压缩存进 `research_llm_blob`，工具定义单独哈希，参数按白名单保留；
返回（含推理内容、工具调用、finish reason、用量）在 `on_llm_end` 补齐，失败时记错误类别。
每条记录带 `node`（langgraph 节点，例如上下文压缩中间件）与 `group`（执行 ID），读取时与同组上一次调用做差集，
给出 `repeated_prefix` 与 `new_message_indexes`，界面据此显示“只看本轮新增”。
已知凭据值、`Bearer` 令牌和 URL 中的密钥参数在存储前脱敏。
接口：`GET /{id}/llm-calls`（合并指标）、`/{id}/llm-calls/{call_id}`（含可重建的 OpenAI 请求）、`/{id}/llm-calls/export`。

### 12.2 活动时间线（`activity.py`）

`GET /{id}/activity` 从本轮 cycle 的事件与调用投影出结构化条目：`plan`、`step`、`note`（研究员进展说明）、
`search`（同一单元连续搜索合并，带查询词与结果域名）、`read`、`tool`、`step_done`、`step_failed`、`gap`、
`limited`、`update`、`writing`、`outline`、`section`、`done`、`failed`、`cancelled`，以及 `current`
（正在规划、搜索、阅读或写作的内容）和计数（搜索、读取页面、计划步骤及已结束步骤）。进展说明只展示读者语言，
描述技能文件或工具名的句子只留在 Trace。查询词与 URL 只从声明为 `search` / `read` 的工具参数提取。

### 12.3 事件流

`GET /{id}/events` 以 `after` 或 `Last-Event-ID` 为游标回放持久事件，约每 0.5 秒拉取、每 10 秒心跳，
在终态且无活动任务时结束。响应头 `Cache-Control: no-store, no-transform` 防止压缩代理缓冲进度事件。

主要事件：`run.created`、`plan.created`、`plan.updated`、`plan.waiting_confirmation`、`plan.editing`、
`plan.countdown_resumed`、`plan.auto_started`、`conversation.message`、`conversation.steering`、`conversation.answered`、
`research.cycle.started`、`research.unit.started/completed/failed`、`research.agent.started`、`research.source_policy`、
`research.output.retry/pruned`、`research.evidence.trimmed`、`research.supplement.deferred`、`evidence.pool.updated`、
`validator.passed`、`validator.gap_found`、`report.limitations.auto/policy`、`report.synthesizing`、`report.outline.ready`、
`report.section.started/completed`、`report.draft.repair`、`citation.bound`、`report.validation.retry`、`report.completed`、
`run.failed`、`run.cancelled`、`activity.tool.started/completed`、`activity.note`、`usage.reserved/settled`、`trace.started/ended`。

### 12.4 成本与效率指标（`metrics.py`）

记录在研究执行时同步写入，读取时再汇总；指标写入失败只记日志，绝不改变研究结果。

| 记录 | 来源 | 字段 |
| --- | --- | --- |
| 模型调用 `research_model_call` | `trace.model_callbacks` | `cycle`、`phase`（workflow 节点）、`purpose`（`agent` / `conversion`）、`skill`、`agent_name`、`unit_id`（写作为 `report-section-N` 等任务 ID）、`execution_id`、`contract`、`model`、`response_model`、开始/结束时间、`duration_ms`、`status`（ok / error / interrupted）、`error_code`、`input_tokens`、`output_tokens`、`cache_read_tokens`、`reasoning_tokens`、`total_tokens`、`usage_reported`、`estimated_tokens`、`finish_reason`、`prompt_messages`、`prompt_chars`、`output_chars`、`tool_calls` |
| 工具调用 `research_tool_call` | 同上 | 原有字段加 `cycle`、`phase`、`skill`、`purpose`、`output_chars`（返回给模型的字符数）、`request_key`（脱敏后参数的 SHA-256 前 16 位，不保存参数）、`error_type`（异常类名；工具返回错误结果时由 `returned_error_type` 粗分为 `HTTP nnn`、异常名、`EmptyContent`、`ToolReturnedError`） |
| 子 Agent `research_agent_run` | `native.execute_role` | 作用域字段、`model`、`thread_id`、开始/结束时间、`duration_ms`、`status`（completed / failed / cancelled）、`error_code`、`stop_reason`、`receipts`、`model_calls`、`model_errors`、`unreported_model_calls`、`tool_calls`、`tool_errors`、各类 Token 合计、`max_input_tokens` |
| 事件 | workflow / runner | `research.unit.started` 与 `report.section.started` 带 `queued_ms`（等待并发名额）；`metrics.cache_hit`（`plan`、`unit-result`、`native-unit`、`report-outline`、`report-section`、`report-summary`、`synthesis`） |

阶段来自 `trace.metric_scope`：`traced()` 在执行 workflow 节点时写入 `{phase, cycle}`，原生执行与格式转换在构建回调时复制，
因此在原生子循环中的回调也能归属到正确阶段。Token 用量由 `usage_details` 归一化：优先 LangChain `usage_metadata`，
其次原始 `token_usage` 中的 OpenAI 兼容字段、DeepSeek `prompt_cache_hit_tokens` 与 Anthropic `cache_read_input_tokens`；
提供方未上报时字段为空、`usage_reported=false`，只保留预算预留估算，不计入费用。

`summarize` 输出：

- `time`：总时长（创建到发布）、实际计算（workflow span 合计）、等待（两者之差，含计划确认与停机）、模型/工具/排队耗时、按阶段耗时。
- `tokens` 与 `cost`：输入、输出、缓存命中、推理、合计、未上报调用；按 `pricing` 估算费用（缓存命中按缓存单价，未配置则按普通输入价），
  列出未定价模型；多币种时只给分币种合计。
- `model_calls`：次数、错误码、finish reason、延迟 p50/p95、最大上下文。
- `breakdown.by_node`：按可调节点（`rewrite`、`plan`、`research`、`conversion`、`outline`、`section`、`summary`、`revision`、
  `follow_up`，外加引擎的 `compaction`）汇总：使用的模型、调用数、错误、未上报用量的调用、各类 Token、最大输入、平均输出、
  `truncated`（`finish_reason=length`，该调大这个节点的 `max_tokens`）、`retries`（契约或引用修复次数，该降温度或换模型）、
  累计耗时与 P50/P95/最大延迟、费用。各节点合计与总调用数、总 Token 一致。模型调用记录带 `config_node` 与 `engine_node`；
  早于这两个字段的历史记录按 `purpose`、角色和写作任务 ID 归类。
- `tools`：次数、错误率与错误类型、延迟、返回字符、搜索次数（`search` 与 `data` 两类查询；被预算拒绝的调用记为 `budget_stops`，不算搜索）、读取页面数、按工具明细；重复调用（同一工具同一参数、在一次成功之后，
  区分同一子 Agent 内与跨 Agent；分页与失败重试不算）；读取失败最多的 10 个站点；
  `failovers` 与 `by_provider`（每个数据源供应商的尝试、应答、空结果、错误、冷却跳过、缓存命中、错误类别与延迟）。
- `agents`：完成/失败/取消、失败码、最大并行数、按角色汇总、每次执行明细与费用。
- `units`：按计划顺序列出每个研究单元（含补研）的耗时、模型调用、Token、费用、工具调用、搜索、读取页面、原始证据、
  被引用页面数（报告引用的 `unit_ids`）与每条引用 Token，用来找出高消耗、低产出的单元。
- `budget`：Token、工具调用、时长上限与已用比例；上限为空表示不限。时长按已结算用量与运行中 workflow span 取大，
  因此运行中的任务也能看到时间预算消耗。预算按任务计：报告完成后的追问会把当前用量归档到 `usage_history` 并从零开始，
  `budget.earlier_tasks` 列出同一会话里已结束的任务。
- 没有任何调用上报用量时，Token 合计与由它推导的比率为 `null`（未知），不显示 0；`estimated_unreported` 保留账本估算。
- `metered`：早于逐次调用计量的任务（有预算账本但没有调用记录）为 `false`，未测量的字段一律为 `null`，不以 0 充数。
- `research`、`report`、`cache`：计划与补研单元、失败单元、原始证据、裁剪与剪除引用、转换重试；报告字符、章节、表格、图、引用、域名、修复与删句；缓存复用。
- `efficiency`：每条引用 Token/费用/计算时长、读取页面与引用的比例、每个研究单元（含补研）搜索次数、重复工具调用占比、每报告字符 Token、格式转换 Token 占比、失败子 Agent Token 占比、缓存命中率。
- `breakdown`：按阶段、用途、角色、模型、单元、轮次的调用、Token、耗时与费用。

入口：`GET /{id}/metrics`、`GET /{id}/metrics/export`（JSONL：先汇总，再逐条原始记录），前端“指标”页签，
以及离线命令 `python -m deepresearch.metrics --data-dir <数据目录>/research [--config research.yaml] [--run ID] --format table|csv|json|jsonl`。

## 13. HTTP 接口摘要

所有接口位于 `/api/deepresearch`，详细契约见 [API.md](API.md)。

| 分类 | 接口 |
| --- | --- |
| 能力与创建 | `GET /capabilities`、`POST /`、`GET /`（历史） |
| 运行与计划 | `GET /{id}`、`POST /{id}/plan/approve`、`pause`、`resume`、`edit`、`reject` |
| 对话与控制 | `POST /{id}/messages`、`POST /{id}/cancel`、`POST /{id}/retry` |
| 过程 | `GET /{id}/events`（SSE）、`GET /{id}/activity`、`GET /{id}/sources`、`GET /{id}/evidences` |
| 成本与效率 | `GET /{id}/metrics`、`GET /{id}/metrics/export` |
| 报告与审计 | `GET /{id}/report?format=json\|md\|html\|docx&version=N`、`GET /{id}/trace`、`GET /{id}/trace/export`、`GET /{id}/llm-calls`、`GET /{id}/llm-calls/{call_id}`、`GET /{id}/llm-calls/export` |
| 设置（读取需登录，修改需管理员） | `GET/POST /settings`、`POST /settings/reset`、`/settings/restore`、`GET /settings/history`、`POST /settings/secrets`、`/settings/test-model`、`/settings/test-provider`、`/settings/mcp-tools`、`GET /settings/health` |
| 资源 | `GET /favicon?domain=`（网站图标） |

错误映射：`SERVICE_STOPPING` → 503；`RUN_BUSY`、`PLAN_VERSION`、`NOT_RETRYABLE`、`IDEMPOTENCY_CONFLICT`、`CONFIG_CHANGED`、
`PROFILE_VERSION`（设置版本冲突）→ 409；`CAPACITY` → 429；设置校验失败 → 422（只返回字段路径与消息，不回显提交值）；
其他研究错误 → 422。owner 不匹配与不存在同样返回 404；非管理员修改设置 → 403。

## 14. 前端架构

### 14.1 数据流

`useResearchConversation`（`core/deepresearch/hooks.ts`）：

- React Query 维护 `capabilities`、`run`、`sources`、`activity`、`history` 查询。
- 非终态运行建立 `EventSource(/events?after=<seq>)`；收到事件、连接建立或出错时让运行、来源和活动查询失效重取，
  以覆盖进程重启这类不产生新事件的状态变化。
- 操作（创建、消息、暂停/恢复、批准、取消、重试）走认证的研究 API；迟到响应按会话选择与请求时间隔离，
  网络不确定时保留消息幂等键。
- 倒计时剩余时间基于服务端快照与本地单调时钟计算，浏览器不发起批准。

### 14.2 界面与 ChatGPT 对照

| ChatGPT 深度研究 | DeerFlow 实现 |
| --- | --- |
| 研究简报与计划卡、圆环倒计时、编辑/取消/开始 | `plan-card.tsx` 等待状态 |
| 编辑时输入框上方引用计划，修改后回复确认并直接开始，旧卡折叠为“计划已更新” | `research-conversation.tsx` 引用条、`ack-N` 消息、折叠计划 |
| 研究中步骤状态、实时状态行、搜索计数、进度条、停止、更新 | `plan-card.tsx` 运行状态与 `liveStatus` |
| “研究完成情况：用时 · 引用 · 搜索”与报告卡 | `reportSummaryLine`、`ResearchReportCard` |
| 全屏阅读器、悬停目录、滚动高亮 | `report-reader.tsx` |
| 来源按域名分组（网站图标）、活动时间线（网站标签） | `sources-panel.tsx`、`SiteIcon` |
| 正文上标引用与来源卡片 | `report-view.tsx` 引用编号（选中变实心）与 `CitationPreview` 悬浮卡（两行摘录） |
| 空白页“推荐”“报告” | `research-gallery.tsx` |

研究页向原生 `MessageList` 传 `runDurationEnabled={false}`（单条消息耗时不是研究用时）。报告渲染使用 GFM，不启用数学公式。
移动端的来源与活动使用原生 Sheet 抽屉。

### 14.3 网站图标

`SiteIcon` 只请求 `/api/deepresearch/favicon?domain=`，失败时显示首字母，并在 4 秒后以 `retry=1` 重试一次。
Gateway 依次尝试主机与上级站点的 `/favicon.ico`、首页声明的最多 3 个非 SVG 图标；每一跳做公网地址校验；
单个响应不超过 256 KB；只按文件头接受 ico/png/gif/jpeg/webp；命中缓存 7 天、未命中 1 天；
首次超过 2 秒时返回 `no-store` 的 404 并在后台继续。`favicons: false` 关闭所有外部请求。

## 15. 配置

### 15.1 研究配置（`Settings`）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `runner` / `runner_factory` | `demo` / 无 | `deerflow` 为真实执行；工厂由管理员指定 |
| `data_dir` | `.deerflow/deepresearch` | 数据库、检查点、日志 |
| `models` / `default_model` / `rewrite_model` / `extraction_model` | `[]` / 无 | 研究自己的模型；注入私有 AppConfig 副本，宿主 `models` 不受影响。`stream_usage`、`max_tokens_param` 适配只支持 Chat Completions 的模型网关 |
| `nodes` | `{}` | 按节点（rewrite / plan / research / conversion / outline / section / summary / revision / follow_up）指定模型、温度、top_p、输出上限、超时、修复次数、JSON 模式与 `extra_body`；`rewrite`、`summary` 可关闭 |
| `skills` | 必填 | 必须包含 `deepresearch` 与 `report-synthesis`；`methodology` 或 `path`、`model`、`system_prompt`、`tools`、`enabled`、`max_turns`、`timeout_seconds`（`agent` 是旧版绑定） |
| `sources` | `[]` | `name`、`tool`、`role`（search / read / data）、`origin`、`level`、`enabled`、`providers`（按序故障切换）；`kind: mcp` 直接暴露 MCP 工具，`kind: native` 是旧版宿主工具绑定 |
| `mcp_servers` / `engine_tools` | `{}` / `[read_file]` | 研究自己的 MCP 服务（`allowed_tools` 白名单、请求头/环境变量支持 `${ENV}` 与 `${secret:NAME}` 插值）；研究员可用的引擎工具（不继承宿主工具表） |
| `prompts` | 默认见 `prompts.py` | 20 条指令全部可覆盖，含 `rewrite`、`compaction` 以及无原文时使用的 `research_records` / `conversion_records` |
| `compaction` | 见 `CompactionSpec` | 上下文压缩：按角色模型上下文比例触发、按 token 保留、研究自己的摘要提示词 |
| `llm_audit` | `true` | 是否保存每次模型调用的完整提示词与返回 |
| `max_concurrency` / `writer_concurrency` / `max_active_runs` | 3 / 无 / 8 | 研究并行度、章节写作并行度与全局容量 |
| `plan_min_units` / `plan_max_units` | 3 / 6 | 计划的研究步骤数范围 |
| `max_searches_per_unit` / `max_seconds_per_unit` / `max_findings_per_unit` | 30 / 无 / 12 | 每个研究单元的检索次数、软时限与交给写作的发现条数 |
| `supplement_gap_codes` / `report_time_reserve_seconds` | 全部 / 无 | 哪些缺口触发补研；时间预算里留给写报告的秒数 |
| `plan_countdown_seconds` | 45 | 计划倒计时 |
| `max_output_tokens` / `output_retries` / `extraction_model` | 8192 / 2 / 无 | 单次输出上限与转换修复（节点可各自覆盖） |
| `native_tools` | 无 | 宿主工具候选上限 |
| `allow_limited_report` / `cite_search_results` | true / false | 带局限写报告；搜索结果是否可引用 |
| `max_synthesis_repairs` / `max_report_sections` | 1 / 8 | 终检重写次数、章节上限 |
| `favicons` | true | 网站图标代取 |
| `pricing` | 空 | 模型名到单价（每百万 Token 的输入、缓存命中、输出价格与币种），只用于估算费用，不参与配置指纹 |
| `require_dual_source` | true | 是否要求内外部来源并存 |
| `budget_ceiling` | 有限值 | 部署级预算上限；客户端不能用 `null` 取消有限上限 |
| `trace_capture_content` / `trace_max_chars` | true / 16000 | Trace 内容采集 |
| `local_secret_env` / `request_secret_headers` / `access_policy` | 空 | 本地密钥环境变量名、请求头到凭据键的映射、读取时 ACL 钩子 |

示例见仓库根 `deepresearch.example.yaml`、`examples/deepresearch/host-config.fragment.yaml`（只有 `plugins`）、
`examples/deepresearch/web-sources.fragment.yaml`（公网搜索/阅读的供应商链）与
`examples/deepresearch/mcp-sources.fragment.yaml`（研究自己的 MCP 服务与按请求凭据）。
离线模板在 `examples/deepresearch/offline/`（本地模型、内网知识库、`LocalSandboxProvider`），由 `test_offline_config.py` 校验。
逐字段说明见 [RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)，模型与宿主配置见
[MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md)、[DEERFLOW_CONFIGURATION.md](DEERFLOW_CONFIGURATION.md)。
`python -m deepresearch.doctor --config <研究配置> [--probe-model <模型名>] [--probe-mcp] [--probe-sources]` 检查配置与研究角色，
并输出每个节点实际生效的模型与参数（`nodes`）、检索能力判定（`retrieval`：有没有读取工具、检索结果是否可引用）；
`--probe-model` 对该模型发一次普通请求、一次工具调用和一次“只返回 JSON”的请求，报告工具调用、JSON 契约、输出速度、
用量与上下文告警；`--probe-mcp` 连接每个 MCP 服务、列出工具并核对研究用到的工具与白名单。失败时退出码为 1。

### 15.1.1 设置页与运行快照

管理员在 `/workspace/deepresearch/settings` 修改的字段（`profile.EDITABLE`）以覆盖层存进 `research_profile`，
每次保存产生新版本并记入 `research_profile_history`；运维字段（`profile.OPERATOR_ONLY`）只能改文件。
创建研究任务时把生效配置写成内容寻址的快照（`research_profile_snapshot`，方法论正文内联），运行全程用它，
所以保存设置只影响之后新建的研究。快照与覆盖层在反序列化时会丢弃当前 schema 不认识的字段并告警，
保证升级后旧任务仍可恢复。更早期没有快照的任务继续沿用配置指纹校验。

### 15.2 Skills

`examples/deepresearch/skills/`：`deepresearch`（规划规则）、`report-synthesis`（大纲、章节、摘要规则）、
`industry-trend`、`technical-route`、`product-benchmark`（研究方法与笔记格式）。Skill 正文参与配置指纹；
配置或 Skill 变化后，旧运行的恢复会被 `CONFIG_CHANGED` 拒绝。

### 15.3 验收启动器

`python -m deepresearch.live --allow-live --model <宿主模型名>` 在隔离目录 `.deerflow/deepresearch/live-*` 生成私有配置，
复用真实 Gateway。可选 `--jina-no-key`、`--unlimited-budget`、`--max-output-tokens`、`--resume-dir`、`--port`、`--frontend-port`。
它不修改运营方文件，凭据只在进程环境中，不能在 CI 中调用真实提供方。

## 16. 安全边界

- 身份：生产由宿主 `resolve_principal` 提供；owner 不匹配与不存在同样 404；可选 `access_policy` 在读取、历史、导出和幂等重放时复核。
  演示模式只接受回环地址和允许的 Origin。
- 凭据：只通过显式请求头映射或本地环境变量进入运行上下文，不写入消息、检查点、缓存或 `pending_operation`，执行结束后清除。
  提供方错误正文不回显。
- 内容：来源文本是不可信数据；报告 HTML 导出带 `default-src 'none'` CSP；前端 Markdown 清理保持开启。
- 出站：研究工具出站由原生工具与宿主策略控制；网站图标是唯一的研究外出站，使用与原生抓取相同的公网地址校验，
  存在同样的 DNS 重绑定限制，只返回按文件头识别的位图。
- Trace：本地、脱敏、受访问控制；脱敏不等于可以公开上传运行目录。

## 17. 失败与恢复速查

| 代码 | 含义 | 可恢复 | 处理 |
| --- | --- | --- | --- |
| `PROCESS_INTERRUPTED` | 进程重启或停止 | 是 | 页面重试，从检查点继续 |
| `NATIVE_AGENT_TIMEOUT` / `MODEL_TIMEOUT` / `MODEL_RATE_LIMIT` / `MODEL_UNAVAILABLE` | 超时或上游问题 | 是 | 单元级降级；整体失败时重试 |
| `MODEL_AUTH_REQUIRED` / `MODEL_ACCESS_DENIED` / `MODEL_BILLING_REQUIRED` | 提供方认证、权限或计费 | 是（运行整体失败，不做单元降级） | 修正凭据或账户后从检查点重试 |
| `NO_EVIDENCE` | 没有任何发现引用到可引用证据 | 是 | 检查搜索/读取工具与整理模型输出后重试 |
| `RESEARCH_BUDGET_SPENT` / `RESEARCH_TIME_SPENT` | 研究 Token / 研究时间用尽，余量留给报告 | 单元级 | 该单元降级、停止补研，报告照常生成 |
| `REPORT_EMPTY` / `REPORT_REVISION_TRUNCATED` | 章节全部为空；改写结果丢失多个章节 | 是 / 否 | 调大写作节点的 `max_tokens`、超时后重试；改写被拒绝时原报告保留 |
| `TIMEOUT` | 超过“时间上限 + 一份报告预留”仍未完成 | 是 | 调大 `max_elapsed_seconds` 或 `report_time_reserve_seconds` |
| `RESEARCH_GAPS` | 严格部署下缺口未闭合 | 否（owner 可同意带局限） | 重试并传 `allow_limited_report: true` |
| `FINAL_VALIDATION` | 报告多次未通过引用校验 | 是 | 重试获得新的有限重写机会 |
| `EVIDENCE_REFERENCE` / `RESULT_CONTRACT` | 单元结果引用或容量问题 | 单元级 | 该单元降级，局限进入报告 |
| `BUDGET_EXHAUSTED` / `TIME_BUDGET` / `UNIT_BUDGET` | 预算耗尽 | 否 | 调整预算后新建 |
| `CONFIG_CHANGED` | 配置或 Skill 已变 | 否 | 恢复原配置或新建 |
| `RUN_BUSY` / `PLAN_VERSION` / `CAPACITY` | 并发或版本冲突 | — | 刷新后再操作 |

## 18. 扩展点

- 新研究角度：新增 Skill 与 Agent，在配置中登记，planner 可选用（详见 [EXTENDING.md](EXTENDING.md)）。
- 新来源：在研究配置的 `sources` 里声明 `origin`、`level` 和正确的 `role`，背后挂供应商：内置预设、`http`（自定义接口模板）
  或 `mcp`（`mcp_servers` 里某个服务的工具，建议配 `allowed_tools`）。不为 MCP 写返回字段映射：`extract.py` 按通用结构识别记录。
  `kind: mcp` 把 MCP 工具原样暴露给研究员，每次调用整体作为一条证据。
- 自定义 Runner：`runner_factory` 指向管理员控制的工厂，需实现 `AgentRunner` 协议。
- 企业访问控制：`access_policy` 指向 `callable(request, run) -> bool`。

## 19. 测试

后端（`backend/`）：

```bash
uv run --no-sync python -m pytest ../tests/deepresearch \
  tests/test_subagent_executor.py tests/test_jina_client.py \
  tests/test_web_fetch_paging.py tests/test_remote_list_dir.py \
  tests/test_aio_sandbox.py -q
uv run --no-sync ruff check deepresearch ../tests/deepresearch
uv run --no-sync ruff format --check deepresearch ../tests/deepresearch
```

前端（`frontend/`）：

```bash
python3 ../scripts/pnpm.py rstest run deepresearch message-list
python3 ../scripts/pnpm.py check
```

| 测试 | 覆盖 |
| --- | --- |
| `test_workflow.py` / `test_conversation.py` / `test_stability.py` | 状态流转、倒计时、修改即开始、研究中更新、单元降级、带局限报告、终检重试、恢复与幂等 |
| `test_runner.py` / `test_structured_role.py` / `test_dependency_evidence.py` | 研究载荷、证据裁剪、结果错误分类、写作流水线、转换修复 |
| `test_report_quality.py` / `test_core.py` | 引用资格、页面级编号、清理、导出、大纲规则、外置化页面关联 |
| `test_activity.py` / `test_favicons.py` / `test_api.py` | 活动投影、图标代取与安全头、接口权限 |
| `test_metrics.py` | Token 用量归一化、模型/工具/子 Agent 记录、成本与效率汇总、指标接口权限、命令行工具 |
| `test_native_bridge.py` / `test_native_cleanup.py` / `test_metering.py` / `test_trace_finalization.py` | 原生桥接、取消清理、用量计量、Trace 终态 |
| 前端 `presentation`、`hooks`、`plan-card`、`activity-panel`、`citation-preview` | 展示计算、SSE 与重连、计划卡、活动跟随、引用悬浮与网站图标 |

单元测试使用伪造提供方，只能证明适配与生命周期行为；真实研究质量需要用真实模型与浏览器验收（见 [HANDOFF.md](HANDOFF.md)）。
