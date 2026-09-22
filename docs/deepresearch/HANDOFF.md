# DeepResearch 交接文档

更新时间：2026-09-21。本文面向继续开发本项目的工程师或 Agent，记录当前状态、验收证据、运行方式、
风险和下一步。架构与工作流细节见 [ARCHITECTURE.md](ARCHITECTURE.md)，接口契约见 [API.md](API.md)。
本文不是产品宣传，也不是全量安全认证。

## 1. 先看这里

| 项目 | 当前状态 |
| --- | --- |
| 仓库 | `https://github.com/wyzz973/deer-flow` |
| 本机目录 | `/Users/sd3/Desktop/project/deer-flow` |
| 本地分支 | `feat/deepresearch` |
| 远端分支 | `origin/feat/deepresearch-v1-fullstack-20260914`（不是同名远端分支） |
| 已推送提交 | `eb3ce656` 对标 ChatGPT 深度研究的完整改造与本文、[ARCHITECTURE.md](ARCHITECTURE.md)；其后的提交依次修复浏览器端到端测试与 CI 依赖、加入成本与效率观测、加入离线交接文档与配置模板。以 `git log` 为准 |
| 更早的基线 | `5fa0299c` `feat(deepresearch): unify native research chat and harden workflow recovery` |
| 功能状态 | 交互、工作流、报告与前端改造完成；五次真实 DeepSeek 研究端到端完成；配置与 DeerFlow 解耦（研究自己的模型、数据源与供应商故障切换、MCP、角色、提示词、上下文压缩）、请求改写节点、设置页、LLM 调用审计均已完成并真实验收 |
| 离线开发 | 不联网的本地 Agent 从 [OFFLINE_AGENT_GUIDE.md](OFFLINE_AGENT_GUIDE.md) 开始；配置见 [MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md)、[DEERFLOW_CONFIGURATION.md](DEERFLOW_CONFIGURATION.md)、[RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)；模板在 `examples/deepresearch/offline/` |
| 最近一轮 | 2026-09-22：Word 导出重做——引用变上标超链接、参考条目带站点与日期、目录页码表头样式、Mermaid 由浏览器渲染成图（第 3.13 节）；数据源可为自己域名声明站点图标、由网关代取（第 3.12 节）；证据带上来源声明的发布日期，参考文献行显示日期，`not_before` 可机械校验（第 3.11 节）。2026-09-21：研究完成后计划卡折叠可回看（第 3.10 节）；日志记全（工具出入参、MCP 协议层、HTTP 供应商层）、整包导出与保留期清理（第 3.9 节）；直接暴露的 MCP 工具读到的页面可被引用（修复只用 MCP 检索时的 `NO_EVIDENCE`，第 3.7 节）；研究步骤接续调度与编辑计划时保留依赖（第 3.8 节）；运行审计的离线 HTML 页面与两项新分析（第 3.6 节技能部分）。此前 2026-09-19：全模块代码审查与修复、按节点调参、仅 MCP / 无原文研究、模型网关兼容、时间预算收尾，见第 3.6 节与第 4 节末尾 |
| 本次回归 | 见第 6 节 |
| 未验证 | 干净克隆部署、生产构建、目标环境的 MCP 与 SSO、真实手机视口、报告事实逐条核验；远端 CI 以 GitHub Actions 结果为准 |

**务必保留用户原有的本地修改：**
`backend/packages/harness/deerflow/community/aio_sandbox/local_backend.py`。
它在接手前就存在，没有纳入任何 feature 提交。不要提交、覆盖、重置或清理；跨环境迁移时单独评估它对 sandbox 启动的影响。

阅读顺序：仓库根 `AGENTS.md` → `backend/AGENTS.md` → `backend/deepresearch/AGENTS.md` → `frontend/AGENTS.md`
→ [ARCHITECTURE.md](ARCHITECTURE.md) → 本文。不要修改 `CLAUDE.md`，它只是导入 `AGENTS.md` 的薄入口。
能力较弱、不联网的本地 Agent 先读 [OFFLINE_AGENT_GUIDE.md](OFFLINE_AGENT_GUIDE.md)，它把规则、命令和排错写成了逐步清单。

## 2. 已确定的产品与架构方向

- 侧栏保留 DeepResearch 入口，页面复用 DeerFlow 原生对话布局与组件，不做第二套工作台。
- 交互对标 ChatGPT 深度研究（观察记录见 [CHATGPT_BENCHMARK_2026-09-16.md](CHATGPT_BENCHMARK_2026-09-16.md)）：
  需求改写为研究简报，极少追问；计划卡带服务端倒计时；点击“编辑”暂停，对话修改后回复确认并直接开始；
  研究中可以“更新”；完成后显示统计行、报告卡、全屏阅读器，右侧为“来源”“活动”。
- 输入框上方不堆研究约束表单；范围、时间、来源限制由意图理解进入计划。
- 每个研究员是真正的 DeerFlow 原生 Agent，复用模型、工具、权限、Skill、沙箱与生命周期。
- MCP 输入 schema 与返回值原样交给模型，不强制统一业务 JSON；支持普通 Chat Completions，不依赖 JSON mode。
- 缺口无法闭合但已有可引用证据时，默认生成带局限说明的报告；绝不放宽引用真实性校验。
- Trace 必须本地可查、可导出，不依赖 LangSmith。
- 用户授权真实 DeepSeek 验收，本地验收不设累计 token、工具次数和总时长上限；单步超时仍保留。

改变上述方向前先与用户确认，不要悄悄改回旧设计。

## 3. 上一轮完成的工作（2026-09-17）

按层概括，细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。本轮（2026-09-18）的工作见第 3.5 节。

**计划与对话**
- planner 输出研究简报、短标题、短步骤、前提假设；澄清只用于找不到研究对象的请求。
- 对话式修改生成确认话术并直接开始（`plan.auto_started`，`source=revision`）；planner 随计划写入 `units`，
  保证直接开始后运行快照与计划一致。
- 研究中的消息作为 `steering` 持久化，后续研究单元与写作读取，不打断执行。

**研究执行**
- 研究员进度说明使用读者语言，不提技能文件与工具名；活动投影再过滤非读者语言或描述内部步骤的句子。
- `SourceSpec.role`（search / read / data）决定引用资格：搜索结果与发现链接默认不可引用；外置化的长网页被
  `read_file` 续读时仍登记为原网页；`browser_get_text` 只在确认导航后算读取；未声明的运行时工具不可引用。
- 非致命单元失败降级为带局限的占位结果（`unit_failures`、`research.unit.failed`）；示例研究角色超时 600 秒。
- 单元结果超过 500 条原始证据时裁剪发现链接（`research.evidence.trimmed`）；`RESULT_CONTRACT` 与
  `EVIDENCE_REFERENCE` 分开报告。
- 缺口耗尽默认写带局限报告（`report.limitations.auto`），没有可引用证据时为 `NO_EVIDENCE`。

**报告**
- Markdown 报告（`format: markdown-v2`）：大纲 → 并行章节 → 执行摘要；`[[E012]]` 标记由代码绑定。
- 一次精确修复后删除无法验证的陈述；长度只是目标；最多 5 条合并局限，原始局限留在 `audit`。
- 大纲不得重复执行摘要、局限、参考来源；标题去编号；清理写作元话语和生成过程说明。
- 引用按页面编号（忽略协议、`www.` 与结尾斜杠），每条摘录保留自己的 `document_hash`；标题缺失时显示 URL。
- 导出 Markdown、HTML、Word；价格中的 `$` 不会被当成公式。

**前端**
- 计划/进度卡、引用条输入框、完成统计与报告卡、全屏阅读器（悬停目录、滚动高亮）、空白页推荐/报告。
- 来源按域名分组并显示网站图标，条目带标题、原文摘要与短链接；活动时间线的搜索与阅读带网站图标，在底部时跟随新条目。
- 正文引用编号与来源条目悬停显示原文片段（`CitationPreview`）。
- 网站图标由 Gateway 代取（`/api/deepresearch/favicon`，`favicons.py`）：公网地址校验、只接受位图、缓存，失败时显示首字母。
- SSE 返回 `Cache-Control: no-store, no-transform`，修复 Next 开发代理 gzip 缓冲导致页面停在“执行中”。

**观测：成本与效率（`metrics.py`，细节见 ARCHITECTURE 12.4、API “成本与效率指标”）**
- 执行时同步写入三类记录：每次模型调用（`research_model_call`：阶段、用途、角色、单元、Token、缓存命中、延迟、
  finish reason、错误码）、每次子 Agent 执行（`research_agent_run`）、每次工具调用（在原记录上补 `output_chars`、
  `request_key`、`error_type`）。写入失败只记日志，不影响研究。
- 读取时汇总：总时长/实际计算/等待、各阶段耗时、并行累计的模型/工具/排队时间；Token 与缓存命中率；按 `pricing` 估算费用；
  工具失败原因（`HTTP 429`、`ConnectError`、`SSLError`、`EmptyContent` 等）、同参数重复调用、读取失败最多的站点；
  子 Agent 明细与最大并行；每个研究单元的 Token、搜索、读取、证据与被引用页面数；预算使用；每条引用的 Token/费用/时长等效率比。
- 入口：侧栏“指标”页签（运行中每 5 秒刷新）、`GET /{id}/metrics`、`/metrics/export`（JSONL 原始记录）、
  离线命令 `python -m deepresearch.metrics`（多次研究对比，table/csv/json/jsonl）。
- 早于逐次计量的历史任务 `metered=false`：只有预算账本合计，其余字段为 `null`，界面显示“—”或“未记录”，不以 0 充数。
- 费用需要在研究配置里填写 `pricing` 单价；当前验收配置没有填写，费用显示“—”。

**离线交接与配置**
- 新文档：[OFFLINE_AGENT_GUIDE.md](OFFLINE_AGENT_GUIDE.md)（给不联网的本地 Agent：铁律、环境检查、三种运行方式、改动定位、测试、排错、提交）、
  [MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md)、[DEERFLOW_CONFIGURATION.md](DEERFLOW_CONFIGURATION.md)、[RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md)。
- 离线模板 `examples/deepresearch/offline/`：`host-config.fragment.yaml`（vLLM 本地模型、RAGFlow 知识库、本机沙箱、四个研究角色、插件）和
  `research.yaml`（单一内部来源、关闭网站图标、弱模型参数）。`test_offline_config.py` 校验两者合法且互相一致。
- `python -m deepresearch.doctor --probe-model 模型名`：对模型发一次普通请求和一次工具调用，报告工具调用、用量和上下文告警，失败时退出码为 1。
  同时修复 doctor 的一个问题：宿主配置尚未加载时，第一个研究角色会被误报为未配置。
- `Settings.fit_origins`：`require_dual_source: false` 时，规划后去掉没有来源可用的 `required_origins`，避免弱模型的计划让研究单元报 `TOOL_DENIED`。
- `research.yaml` 里的 `tool_timeout_seconds`、`tool_retries` 已确认不生效，文档标为旧字段，离线模板不再写它们。

**仓库卫生与 CI**
- `.github/workflows/deepresearch.yml` 的 `setup-uv` 固定为生产使用的 uv `0.11.1`（`test_ci_uv_version_pin.py`）。
- `backend/deepresearch/AGENTS.md` 登记到 `test_agent_guidance_check.py` 的允许清单。
- 浏览器端到端测试（`frontend/tests/deepresearch/workbench.spec.ts`）按新交互改写：编辑状态、对话修改直接开始、
  全屏阅读、引用悬浮原文片段、来源与活动页签、Trace、移动端抽屉与侧栏历史。
- `/deepresearch-demo` 页面补上与工作区相同的限高框架（`SidebarProvider h-screen` + `SidebarInset`）。此前面板组只有约 104 px 高，
  输入框在视口外，这是 09-16 起 CI 端到端测试失败的原因。
- `backend/deepresearch/requirements.txt` 补充 `markdown-it-py`。CI 的演示后端只安装该清单，缺少它时报告渲染失败（已在同等环境复现）。
- 网站图标的首字母徽标与图片标为装饰性（`aria-hidden`），不改变域名标题的可读名称。

## 3.5 本轮完成的工作（2026-09-18）：配置独立、改写节点、设置页、调用审计

用户要求：①搜索经常被免费额度限流，要能接别的搜索接口或用 MCP 顶替（返回格式不固定）；②补上 ChatGPT 深度研究缺失的第一步——改写用户请求；
③subagent / skills / 提示词 / 模型都不要写死，用户自己可配；④Trace 要能看到每次 LLM 调用的完整提示词与返回，界面要好读。
追加要求：**所有研究配置都与 DeerFlow 独立，DeerFlow 只是功能引擎底座。**

**配置独立（新增 `models.py`、`profile.py`、`secrets.py`、`catalog.py`）**
- 研究模型写在研究配置的 `models` 里，注入一份**私有** AppConfig 副本（`private_config` 重建名称索引），宿主 `config.yaml` 不被修改；
  宿主只需要 `plugins` 一段。凭据只写 `$环境变量` 或 `secret:名字`，保存的密钥只写不读。
- 修复一个潜伏缺陷：`model_copy` 不会重建引擎的模型名索引，导致研究的单次输出上限从未真正生效。
- 角色（`skills`）不再必须绑定宿主子 Agent：可以直接写 `methodology` 正文、模型、系统提示词、工具白名单、步数与超时。
  引擎工具由 `engine_tools` 决定（默认只有 `read_file`），不再继承宿主工具表。
- 设置页的修改按字段存成覆盖层（`research_profile`），每次保存产生新版本；**每个研究任务创建时保存完整配置快照并全程使用它**，
  所以改配置不会影响已经在跑的研究。旧版本写入的字段被移除后，快照与覆盖层会丢弃未知字段并告警，不会让旧任务再也跑不起来。

**数据源与供应商故障切换（新增 `providers.py`、`channels.py`、`extract.py`、`mcp.py`）**
- 一个数据源对模型只暴露一套固定参数（search / read / data），背后按顺序尝试多个供应商。限流、额度用尽、鉴权失败、超时、
  网络错误、服务异常让该供应商进入冷却；单个页面打不开、无结果只换下一个供应商重试。
- 预设：tavily、serper、brave、exa、bocha、searxng、jina_search、duckduckgo；jina_reader、tavily_extract、firecrawl、direct；
  ragflow、lightrag；外加 `http`（自定义接口模板）与 `mcp`（按工具 schema 自动对应参数）。
- 返回格式不固定也能用：`extract.py` 从 JSON、Markdown 链接、`Title/URL` 文本块或 HTML 中识别标题、链接与正文，
  并产出与原生工具相同的 `deerflow.web_page.v1` / 记录型证据。
- 指标新增 `tools.failovers` 与 `tools.by_provider`（尝试、应答、错误、跳过、缓存、错误类别、延迟）。

**请求改写（新增 `rewrite` 节点、`ResearchRequest` 契约、`prompts.REWRITE_INSTRUCTIONS`）**
- 图从 `START → rewrite → planner`；计划编辑与“追问触发新研究”都回到 `rewrite`，由它合并上一版请求并写确认话术。
  对话里以 `kind: rewrite` 的卡片展示，`plan.brief` 就是改写后的请求。观察依据见 CHATGPT_BENCHMARK 第 7 节。

**设置页（`/workspace/deepresearch/settings`，管理员）**
- 模型（含真实“测试连接”）、研究角色、提示词（17 条，可搜索、可逐条恢复默认）、数据源与供应商（含单独测试与健康度）、
  MCP 服务（可连接并列出工具）、运行参数与上下文压缩、密钥与历史（只写密钥、凭据解析状态、版本回滚）。
- 保存前在前端做一次校验（`draftProblems`），保存时带版本号，冲突返回 409 并提供“载入最新版本”。

**LLM 调用审计（新增 `audit.py`，`trace.py` 扩展）**
- `llm_audit: true` 时保存每次模型调用的完整请求（消息按 sha256+zlib 内容寻址）、工具定义、参数、返回与推理，
  并记录 langgraph 节点与执行分组；与上一次调用做差集得到 `new_message_indexes`，界面可“只看本轮新增”。
- 界面：研究页顶部“LLM 调用 / Trace 时间线”两个页签，按阶段与执行分组、可搜索、可导出 JSONL、可查看可重建的 OpenAI 请求。

**引擎修复（`packages/harness/deerflow/agents/middlewares/summarization_middleware.py`）**
- 子 Agent 的系统提示词保存在 `messages` 里，上下文压缩会把它一起压缩掉：研究员因此在循环中途丢失方法论与输出要求；
  更糟的是压缩窗口里只剩系统提示词时，LangChain 的 `start_on="human"` 裁剪器把整段研究过程直接丢弃，摘要写成“尚未开始研究”。
  现在压缩会保留开头连续的 `SystemMessage`（`_leading_system_messages`），研究过程才真正进入摘要。
- 研究自己决定何时压缩（`compaction`）：按角色模型声明的上下文长度比例触发，保留量按 **token** 且只占阈值的一部分，
  摘要模板是研究自己的提示词（要求保留网址、原文摘录、数字、日期与回执 ID）。

**按请求传凭据**
- 企业部署的 `request_secret_headers` 现在也覆盖研究自己的 MCP 服务（连接头与环境变量）和 `type: http` 供应商的请求头：
  某次请求带来的凭据只在这次请求内覆盖同名的 `secret:`，不同凭据各自建立连接、各自缓存工具列表，值不入库、不进 Trace。

**只支持流式的模型（2026-09-18 用户报告）**
- 症状：本地模型只支持流式返回时，非流式请求拿到的 `content` 为空，contract 节点判定为失败（`OUTPUT_SCHEMA`）。
- 修复：`models.complete` 统一处理研究的每一次直接调用（笔记整理、请求改写、`doctor` 探测），先流式并聚合，
  服务拒绝流式或流式没有内容时才退回普通请求；只有工具调用没有正文也算有效回答。
  引擎的上下文压缩摘要（`summarization_middleware`）同样改为先流式，否则这类部署的压缩会永远失败。
- 验证：写了一个「非流式返回空、流式返回内容」的本地 OpenAI 兼容服务，通过真实网关验证：
  `POST /settings/test-model` 返回 `ok: true`（普通回复 ready、工具调用 lookup、用量 30 tokens），
  把 `rewrite_model` 指向它后新建研究，改写节点拿到完整契约并进入规划；假服务日志显示全程只有 `stream=True` 请求。

**merge 节点的长标题 / 长网址问题（2026-09-18 用户报告）**
- 根因：`observations.py` 用 `model_copy(update=...)` 生成派生证据，**绕过 pydantic 校验**。超长标题（页面没有标题时用
  整条网址，GitHub 的签名链接能到 1500+ 字符）、无法作为定位符的网址、空正文都会被写进结果，直到 `evidence_merge`
  统一校验时才报错，而且一条坏记录会让整个研究失败（真实运行 `52e358b9` 就是这样挂的）。
- 修复分三层：
  1. 产出端：`observations.derive()` 走完整校验再落库，无法成为证据的记录当场跳过；`observed_sources` 的标题限长 1000。
  2. 契约层：展示性字段（标题、发布方、正文片段、计划与步骤标题）超长时截断而不是拒绝；定位符、ID、结论、简报仍然严格。
  3. merge 节点：`evidence.valid_result()` 逐条校验历史结果，丢掉当前 schema 无法接受的记录并记事件
     `research.evidence.dropped`（只记 raw_id 与字段名），发现仍保留活着的引用，引用全被丢掉的发现才一起丢；
     结构性错误照旧让运行失败。
- 顺带修掉一个弱模型的坑：步骤标题（上限 80）或计划标题（上限 120）写超一点就会让整份计划被拒、进入转换重试甚至失败，现在直接截断。
- 验证：用真实运行 `52e358b9` 存下的 12 份单元结果回放，全部可用；新增测试覆盖超长标题、带凭据的网址、空正文、
  引用修复与结构性错误仍然失败。

**预算不再直接让研究失败（2026-09-18 用户要求）**
- 用户要求：能控制大模型的搜索次数，而不是“搜索次数到上限就报错、整个 workflow 失败”。
- 新增 `max_searches_per_unit`（默认 30，可在设置页改）：这个数字写进研究员的任务载荷（`search_budget`），
  用完后检索工具返回“不能再检索，读完已找到的页面后写笔记”，研究单元正常收尾。**读网页不计入**
  （只有读过的原文能被引用），只计入全局 `max_tool_calls`；全局工具次数用完时所有数据源工具都要求收尾。
  数据源工具自己记账，模型回调不再重复预留（否则一次检索会扣两次，并且会先抛致命错误）。
- 停止提示带 `deepresearch.budget_stop.v1` 标记，`research_observations` 跳过它，不会变成证据。
- 次数是工具端强制执行的，不依赖模型听话：超额的搜索直接返回停止提示，不会调用供应商。模型一轮常并行发 2–3 个搜索，
  所以每次搜索在等待账本之前就先占位；原先先检查、后计数，并行调用会一起越过上限（测试复现：上限 2 时 3 个并行全部放行）。
- 模型遵守情况（4 次真实运行、15 个研究步骤、每步上限 3）：没有步骤实际执行超过 3 次；13 步主动没超，
  2 步尝试了第 4 次，被拒一次后就不再搜索。供应商全部失败的搜索也计入次数（防止失败时反复重试）。
- 模型 token 预算同样改为收尾：
  - 每次调用按真实量级预留（研究回合“输入 + 4096”，写报告“输入 + 8192”），结束后按实际用量结算；
    不上报用量时按完整输出上限记账。原来每次预留整个 `max_output_tokens`（32768），120k 预算在实际只用一半时就显得用完了，
    并发 3 个章节写作也根本放不下。
  - 报告预留改为“上限的 1/5，至少 60000，最多一半”（`REPORT_RESERVE_FLOOR`；实测一份 3 单元报告 8 次调用约 145k）。
  - 每个研究单元分到“研究剩余额度 ÷ 同时进行的单元数 − 整理余量”，通过引擎自带的 `TokenBudgetMiddleware`
    生效：用到一半提醒收尾，用满时去掉工具调用、让它写笔记（`stop_reason=token_capped`，报告局限里用报告语言说明）。
    运行有有限预算时总是生效，宿主为研究角色关掉自身兜底上限时也生效；宿主开启的上限只会让它更严。
  - 整理笔记（conversion）可以动用报告预留；研究调用若仍超出研究额度，抛非致命的 `RESEARCH_BUDGET_SPENT` → 该单元降级；
    `store.research_spent`（已有单元因预算失败，或剩余不足一个回合）为真时不再补研。
- 真实验证（DeepSeek v4 flash，每步 3 次检索，均为 API 创建）：
  - `40e5b85a`（旧逻辑）：读网页被计入检索次数，3 次搜索后一页都没读成，停止提示还被记成证据 → `NO_EVIDENCE`。
  - `1f2eb215`：读网页正常了，但 3 个单元并行读页面，研究额度半路用尽、单元失败、证据全丢 → `NO_EVIDENCE`。
  - `07a0c7a6`：每步份额没生效——在线启动器 `--unlimited-budget` 为研究角色关掉了引擎原生预算，旧代码随之跳过。
  - `83a18759`：份额生效，3 个单元被截停后正常完成；写报告的大纲调用要预留“输入 + 32768”，放不进剩余 30k → `BUDGET_EXHAUSTED`。
  - `0ecd0bcb`（默认 120k tokens / 60 次工具）：**COMPLETED**，83k tokens、11 次工具、62 条证据、2 条引用；
    两个单元降级为“未能完成”并写进局限。报告单薄是 120k 预算本身的结果。
  - `8b16f9ef`（token 不限、全局工具 15 次，即网页端带部署上限的情形）：**COMPLETED**，工具恰好用满 15 次后收尾，
    259 条证据、5 条引用、无失败单元。
- 已知限制：120k 预算能保证出报告，但研究很浅。`deepresearch.example.yaml` 的 `budget_ceiling` 与 API 默认值都是 120k，
  网页端会把部署上限当作预算发出；需要像样的研究时应调大 `max_model_tokens`（一次完整研究实测数百万 token）。

**其他修复**
- 角色方法论不再把 SKILL 文件的 YAML 头信息发给模型（`Settings.methodology`）。
- 证据的展示字段（标题、发布方）超长时截断而不是校验失败：一条 1584 字符的签名图片链接曾让 `evidence_merge` 抛出
  ValidationError，整个研究失败（真实运行 `52e358b9` 中复现）。
- 研究员进度说明的语言要求增加对比强调（“即使这些指令和你读到的网页是别的语言”）。

## 3.6 本轮完成的工作（2026-09-19）：代码审查、节点调参、仅 MCP 研究、网关兼容、时间收尾

用户要求：通读交接文档后对 DeepResearch 模块做代码审查，找潜在错误、漏洞与逻辑错误；并明确了目标——
全部配置独立于 DeerFlow、一个文件配好；搜索既要支持自己的 MCP 也要能扩展其他工具，必要时禁用网页工具只用 MCP，
MCP 要带 Cookie/Token 鉴权、返回格式不统一、多数拿不到原文、要有白名单；首先保证工作流可用，成本/时间/性能可观测；
模型网关只支持 Chat Completions；为了 JSON 输出稳定，每个节点的模型与参数都要能单独调；整条链路
（改写 → 计划 → 并行研究 → 证据检查与补研 → 大纲 → 并行章节 → 输出）都要可调。追加要求：**代码、目录名与配置里不要单独区分
某一种部署，这些都是统一的通用能力**（不要出现 company 之类的变体命名或单独模板）。

做法：我自己通读核心链路（workflow / runner / native / models / structured / channels / providers / mcp / extract /
observations / report_policy / prompts / store 预算 / service 驱动），三个只读审查代理分别审持久化与 API、报告与证据契约、前端，
每条发现都带复现脚本；随后修复，并用真实运行验证。真实运行又暴露了四个单测发现不了的问题（见第 4 节）。

**按节点调参（新增 `config.NodeSpec`、`nodes:`）**
- 九个可调节点：`rewrite`、`plan`、`research`、`conversion`、`outline`、`section`、`summary`、`revision`、`follow_up`。
  每个节点可设 `model`、`temperature`、`top_p`、`max_tokens`、`timeout_seconds`、`output_retries`、`json_mode`（仅直接调用）、
  `extra_body`；`rewrite` 与 `summary` 可以关闭。参数写在该次执行私有的模型副本上（`models.with_node`），由引擎自己的模型工厂生效。
- 工作流调优项：`writer_concurrency`、`plan_min_units` / `plan_max_units`、`max_seconds_per_unit`、`max_findings_per_unit`、
  `supplement_gap_codes`、`report_time_reserve_seconds`；`max_output_tokens` 默认 4096 → 8192；角色超时上限 1800 → 14400 秒。
- 指标新增 `breakdown.by_node`：每个节点的模型、调用、Token、P50/P95、被截断次数、格式重试次数、费用，合计与总数一致。
  `doctor` 输出每个节点实际生效的模型与参数，并新增 JSON 契约探测（含输出速度）。

**仅 MCP、无原文的研究**
- 没有任何启用的 `role: read` 数据源时，检索结果就是证据（`report_policy.results_citable`，全模块唯一规则）：
  研究员用 `prompts.research_records`，整理用 `prompts.conversion_records` 与不含 `source_annotations` 的契约，
  每条检索结果单独成为证据，参考资料标注“检索摘录，未读取原文”，写作模型被要求归因式表述。
  原先提示词和工具说明写死“先打开页面、摘要不可引用”，这类部署要么让模型去找不存在的工具，要么以 `NO_EVIDENCE` 结束。
- 可引用结论随证据保存（`RawEvidence.citable`）：角色白名单只有内部检索、而部署里另有网页读取工具时，缺口判断与写作不再推翻研究单元的结论。
- `sources[].enabled`：保留配置但不提供给规划与研究员（网页工具一键停用）。
- MCP：`mcp_servers.<name>.allowed_tools` 白名单（加载时与解析工具时各查一次）；请求头/环境变量支持
  `Bearer ${ENV}`、`sid=${secret:NAME}` 插值且同样脱敏；连接失败归类为鉴权/超时/网络/服务异常并给出可读提示
  （原先一律是 `network (ExceptionGroup)`，401 与连不上无法区分）；`kind: mcp` 数据源原先完全不计入工具预算，现已计量并有单次调用超时；
  工具发现缓存有上限；`doctor --probe-mcp`。
- 异构返回：只有正文、没有标题与链接的片段（`{content, score, doc_id}`）也逐条成为记录；自身带 `name`/`description` 的信封不再被当成一条记录；
  识别更多日期与 `*_id` 字段。

**模型网关兼容（新增 `chat_completions.py`）**
- `provider: openai` 加非官方 `base_url` 时发送 `max_tokens`（LangChain 总是改名为 `max_completion_tokens`，老协议网关会忽略或拒绝），
  并开启流式用量（LangChain 在设置 `base_url` 后默认关闭，导致所有调用“未上报用量”）。`ModelSpec` 新增 `top_p`、`stream_usage`、`max_tokens_param`。

**可用性**
- 时间预算改为收尾：原先 `max_elapsed_seconds` 到点整个运行失败且不可恢复。现在研究在剩余一份报告预留时收尾，报告照常生成（细节见 RESEARCH_CONFIGURATION 第 11 节）。
- 预算按任务计：原先 `usage` 从不重置，研究用掉大半预算后，任何追问（回答、改写、新一轮研究）都会因预算或时间失败。
- 规划鲁棒性：计划里出现未知角色、未知或已停用的数据源、步骤超限时带原因重试，最后一次自动修正，而不是整个运行失败；规划只安排现有数据源能回答的步骤。
- 补研收敛：`open-questions` 每个步骤只触发一轮；饱和与 `NO_EVIDENCE` 按“被发现引用的可引用证据”判断（原先按证据池大小，
  会补研到上限、写两遍报告再以 `FINAL_VALIDATION` 失败）；补研归属按 `depends_on` 而不是 ID 前缀。
- 弱模型输出：只有 `</think>` 的推理不再进入正文；示例对象在前时取真实对象；尾逗号/单引号可读；被截断的回复得到“请缩短”的反馈；
  改写失败时不再把残缺 JSON 整段当成研究请求。
- 报告：接近的标记写法归一化、畸形标记报错并清除；未闭合代码围栏不再吞掉后续章节；大纲标签先清洗再组装；局限永远不为空；
  空章节写成局限；改写结果丢章节时拒绝发布；非文档内链接与图片（含 `//host`）一律删除；单页应用路由视为不同页面；无 URL 记录去重并显示来源名。

**安全（设置页写入口统一收口在 `profile.guard`）**
- 角色 `path` 可读取宿主任意文件并经 `GET /settings` 回显给所有登录用户 → 设置页只接受方法论正文。
- 设置页可新增 stdio MCP 服务（“列出工具”即在网关主机上执行命令，绕过宿主对 stdio 的加固）→ stdio 只能写在运维配置文件里。
- 自定义模型类、请求头/环境变量里的明文凭据同理拒绝；不能编辑的用户看到的明文凭据显示为 `[hidden]`。
- 自定义 HTTP 供应商的 URL 模板对查询词做 URL 编码；直接抓取改为流式限长（原先先整包下载再截断）。
- 长工具结果归档时保持为文本（原先超过 2 万字符会变成 `{'preview': …}` 字典并进入证据摘录）；数据源 artifact 的归档上限放宽以保留全部记录。

**观测**
- MCP 供应商的内层调用不再被研究回调二次记录与二次计费；知识库查询计入“搜索”；被预算拒绝的调用记为 `budget_stops` 而不算搜索；
  所有调用都未上报用量时 Token 为 `null` 而不是 0；`budget.earlier_tasks`。

**前端**
- 审查修复：已有报告的会话在追问失败或被停止后不再是死路（错误与“从检查点恢复”可见，输入框保持可用，后端接受继续追问；
  没有报告的已停止任务返回 409 `RUN_STOPPED`，与执行中的 `RUN_BUSY` 区分开）；表单内“取消更新 / 停止”按钮不再把草稿当作研究更新提交；
  事件流收到非 200 后按退避重建并在流不健康时轮询；事件持续到达时刷新不再被取消；已完成任务不再从头回放事件，
  运行中的任务从快照里的 `last_event_seq` 接续；
  报告正文不渲染远程图片；页内新建研究后点侧栏入口会回到新研究；目录按渲染后的标题定位；倒计时归零后“开始”可重新点击；
  Trace 时间线分页拉全；未计量的数值显示“—”。
- 设置页：新增“节点调参”一节（按工作流顺序，每个节点一张卡，未配置即继承）、数据源启用开关与“没有读取工具”的提示、
  MCP 工具白名单（列出工具时勾选，失败时显示鉴权/超时/网络等原因）、模型的 `top_p` / 流式用量 / 输出上限参数名、
  运行参数（写作并发、计划步骤数范围、每步软时限与发现条数、补研触发的缺口类型、报告预留时间、报告长度系数）；
  修复：脏草稿下保存密钥绕过 409、模型改名途经同名时劫持对方的引用与单价、恢复版本后表单仍是旧草稿、卡片用数组下标做 key、无效输入不阻止保存；
  请求头/环境变量里疑似明文凭据在前端就会报错。
- 指标页新增“按节点”表（模型、调用、错误、Token、平均输出、被截断、格式重试、P50/P95、耗时、费用）。
- ChatGPT 对标改造（依据 [CHATGPT_BENCHMARK_2026-09-19.md](CHATGPT_BENCHMARK_2026-09-19.md) 的实测数值）：研究页作用域内的冷白配色（`.deepresearch-surface`，不改全站 token）；
  提交后的“正在思考 → 正在制定研究计划”流光文字与 24px 圆角骨架块；纯白计划卡/进度卡（16px 步骤、圆环 spinner、平滑的圆环倒计时、
  带流光的状态行、搜索计数、4px 进度条、圆形停止键）；完成后统计行加白色报告卡（蓝色文档图标、可滚动预览、渐隐遮罩）；
  全屏阅读器（侧栏收成图标轨、624px 正文栏、28/24/20px 标题层级、左侧目录刻度条不再压正文、悬停目录浮层、滚动后才出现的顶栏底线、开合淡入淡出）；
  引用标记、两行摘录的悬停卡、点击后来源面板联动高亮；右侧面板默认 375px、胶囊页签、按域名分组的来源与“已扫描的来源”、统一视觉语言的活动时间线；
  `citations[].basis` 为检索摘录时显示“摘录”标注；`prefers-reduced-motion` 下关闭流光与过渡。
  共享组件只有一处改动：`chat-box.tsx` 的扩展面板新增可选的 `defaultSize`（研究页传 375px，其他页面行为不变）。

**提示词缓存（2026-09-19 晚，用户反馈"输入 69.7k · 缓存命中 27%"）**
- 诊断方法：调用审计里每条消息按哈希存储，对同一会话相邻两次请求求"从头相同的消息数"。结果是研究员循环每一轮只有系统提示词相同：
  引擎的 `ToolReceiptMiddleware`（子 Agent 链固定 `render_mode="always"`）在系统提示词后面插入一份"工具回执清单"，每轮重写、不断变长，
  于是其后的全部历史每一轮都失效。研究节点占输入 Token 的 91%，命中率只有 4–5%；去掉这条消息后同一批录制数据的可复用前缀是 77–82%，
  并且除它以外没有任何其他前缀破坏点。
- 对照 pi（`earendil-works/pi`，原 `badlogic/pi-mono`）的做法：对话只追加、前缀里不放易变内容、会话 ID 写进供应商认识的路由字段、
  对不认识这些字段的后端按兼容开关省略、把未命中显示出来。逐条落地：
  1. `tool_receipt_ledger: false`（新设置，默认关）：`model_budget_config` 在研究私有的引擎配置里关闭 `verification.receipts_enabled`，
     宿主配置不变、不改引擎代码；引擎的开关连打标一起关，所以 `observations.derived_receipts` 从归档的工具消息推导 `r1..rN` 与状态，
     证据目录不变。研究员输出提示词与压缩提示词不再要求引用回执 ID（模型已经看不到它们）。
  2. 载荷顺序从"所有调用相同"到"只有本次调用才有"：研究步骤 `instructions` 在最前、`unit` 在数据源之后、预算计数在最后；
     章节写作把 `findings`/`evidence` 放在 `section` 之前（同一批步骤写出的章节共享整段证据前缀，修复调用只多出草稿）；
     转换请求改为 `{"task", "answer"}`。`digest` 按键排序，缓存键不变。
  3. `ModelSpec.session_param` / `session_header`（默认不发送；OpenAI 官方地址自动带 `prompt_cache_key`）：把会话的摘要 ID 写进请求体字段或请求头，
     供多副本网关做会话粘性；设置页"模型"卡片有对应输入框。`models.merged_overrides` 顺带修了直接调用（改写、转换）里节点 `extra_body`
     会整体替换模型自带 `extra_body` 的问题（引擎的 `model_overrides` 是整字段覆盖）。
  4. 可观测：每次模型调用记录 `prefix_messages` / `prefix_chars`，指标新增 `tokens.prefix_reuse_ratio` 与按节点的
     `cache_read_ratio` / `prefix_reuse_ratio`，指标页"按节点"表新增"缓存命中 / 可复用"列、效率区新增"可复用前缀（缓存命中上限）"。
     可复用低=请求开头被改写；可复用高而命中低=模型服务没缓存或没有会话粘性。它不依赖供应商上报用量，没有 usage 的网关也能看。
- **逐节点核对请求体（同日，用户追问"每次请求都符合前缀匹配吗"）**：第一轮只改了研究、章节、笔记整理三个节点，核对后发现还有三处把"每次都不同的内容"放在最前面，已修：
  追问 `respond` 原来 `message` 在最前、后面才是整份报告（每次追问整份报告都作废），现在是计划 → 报告 → 对话 → 本次消息 → 说明；
  改写报告 `revise_report` 原来 `user_request` 在发现、证据、旧报告之前，现在发现与证据最前、请求最后；
  计划 `plan` 原来请求在最前、5.8k 字符的固定内容（说明、契约、角色、数据源、上限）在最后，现在固定内容最前（`structured_task`：载荷以 `instructions` 开头时，`output_schema` 紧跟其后；写作类长载荷仍把两者留在末尾，保证指令贴近生成位置）。
  对话窗口 `recent_messages` 不再每轮滑动一条（那样每次请求的第一条消息都变），改为每 10 条才移动一次窗口头。
  守卫测试 `test_prompt_cache.py` 直接在"模型收到的文本"上断言：同一节点只在新内容上不同的两次调用，共享前缀分别 >60%（计划）、>90%（追问）。
- 线上字节级的证据来自供应商自己：运行 `244cd6d5` 的 82 个研究后续轮次，DeepSeek 每一轮上报的命中 Token 都 ≥ 上一轮的完整输入（100%），工具定义哈希全程唯一。
  也就是说消息序列化、工具调用参数的重新序列化、工具定义顺序都没有破坏前缀。
- 做不到前缀复用的请求（不是缺陷，是内容本来就新）：每个会话的第一轮（只有系统提示词与固定说明能命中）、大纲（单次调用，内容全新）、
  笔记整理里的笔记与调用目录、每份报告第一次被追问时、上下文压缩发生后的那一轮（历史被摘要替换，pi 同样如此）、并行同时起跑的请求之间。
- 验收启动器：`--resume-dir` 原来把"示例配置里新增了一个等于默认值的键"也判为配置变更，任何新设置都会让旧验收目录无法恢复；
  现在缺失的键按 `Settings` 的默认值比较（`live.setting_defaults`，含 `nodes: {}`、`supplement_gap_codes` 这类工厂默认值），其他差异照旧拒绝。
- 没做的：Anthropic 的 `cache_control` 断点（引擎的中间件链由引擎拥有，未验证不加）；并行扇出的"先预热一个请求再放行其余"；
  让笔记整理（conversion）接在研究员自己的会话后面以复用其整段上下文。

**工程与审计技能，以及用它自测时发现的问题（2026-09-20）**
- 新增 `.agents/skills/deepresearch-engineering/`：给接手的 Agent 用的工作技能（Codex 原生读取 `.agents/skills`；Claude Code 用
  `ln -s ../../.agents/skills/deepresearch-engineering .claude/skills/`；其他 Agent 从 `backend/deepresearch/AGENTS.md` 的指引进入）。
  `SKILL.md` 给方位、任务路线和不能做的事；`references/` 分开发、调试（错误码与定位路径）、测试与真实验收、调优（症状 → 设置）、审计报告模板与读数陷阱。
  - `scripts/audit_run.py --run <id|前缀|页面 URL|thread|latest> [--baseline <run>]`：从研究库生成审计底稿（时间、Token、Agent 循环的上下文增长、
    提示词缓存的供应商逐轮核对与前缀断点、工具与供应商、引擎准入排队、失败工具调用的耗时、补研与重做调用、规则发现及对应设置）；
    带 `--baseline` 时另给两次运行的设置差异、指标对比、同一问题其他运行的自然波动。数字来自产品自己的 `metrics.collect`。
    同一份事实输出三种形态：`audit-<run8>.md`（底稿）、`.json`、`.html`（`scripts/audit_html.py` 渲染的单文件离线页面：时间线、各阶段 / 节点 / 步骤的时间、Token、
    工具与搜索情况，逐条工具调用可筛选；同目录有 `审计报告-<run8>.md` 时结论显示在页面顶部）。2026-09-20 增加两项分析：失败工具调用在时钟上的代价
    （按每轮并行批次重算，推算每轮研究去掉失败调用后的时长）、每个研究轮次的成本与产出（补研轮第一次读到的证据有多少被报告引用）。
  - `scripts/show_call.py`：列出并打开任意一次模型调用（默认只显示相对上一轮新增的消息）、导出成可重放的 Chat Completions 请求体、`--tools` 列出工具调用与各供应商的尝试。
  - 两个脚本经只读连接读库（不拿写锁、不建表、不写字节码），输出默认写到已被 git 忽略的 `.deerflow/deepresearch/audits/`。
    `tests/deepresearch/test_audit_skill.py` 用合成研究库跑它们并断言库文件一个字节不变：改表、记录字段或指标键时要连脚本一起改。
- 自测方式：4 个任务（审计一次慢的研究、排查一次失败、验证一次调参的前后对比、给新增设置写实施计划）各跑两份子 Agent，一份用技能、一份不用。
  29 条客观断言两组都全过（这个模型够强，而且仓库文档本身已经很全）；用技能的一组 Token −15%、用时 −23%。子 Agent 的反馈修掉了技能里的三处错误
  （脚本并非只读、预算份额公式写错、旧超时字段）并补了同步清单、测试文件对应表、预算类失败重试无效等内容。
- **自测顺带查出的产品问题，均已修复并有测试：**
  1. **引擎准入排队（影响最大）。** 引擎的 `subagent_runtime.max_running`（宿主默认 3）小于研究的并发时，多出来的角色在引擎里排队：运行 `244cd6d5`
     配置 6 路并发，实际 3 路，三个步骤空等 203 / 261 / 292 秒（总时长 625 秒里 dispatch 占 564 秒；再多等 8 秒就会撞上 300 秒准入超时而失败），
     而研究侧的“排队秒”显示 0.3 秒。此前所有真实验收都受它影响。修复：验收启动器把 `max_running` 抬到不小于 `max(max_concurrency, writer_concurrency)`，
     已有验收目录在下次启动时刷新这一项（`_write_yaml(..., refresh=("subagent_runtime",))`）；`python -m deepresearch.doctor` 新增 `engine_capacity`，不匹配时给出要改的值；
     审计脚本从“角色提交 → 第一次模型调用”的间隔算出这段等待（规则 `engine-queue`）。**常规部署要在宿主 `config.yaml` 里自己设这个值。**
     产品指标里仍没有这段等待，见第 7 节。
  2. **章节超长返修（我上一轮重排载荷引入的回归）。** 把 `length` 挪到几万字证据之前后，章节初稿超出硬上限的比例从 1/8 升到 5/8（同题 4 次旧运行是 0–1 次重做），
     每次多一次修复调用。已把 `length` 放回任务末尾、紧挨 `instructions`；共享前缀本来就止于证据，缓存不受影响。
  3. **预算太小导致的 `NO_EVIDENCE` 报错误导。** 工具调用为 0 且研究预算已耗尽时，报错现在直接指出是预算、并标为不可重试（重试不改预算也不清已用量）；
     `API.md` 的创建示例原来写的正是会失败的那组小预算，已加说明。
  4. `metrics.summarize` 里 `status` 被供应商循环的局部变量覆盖，汇总与 `python -m deepresearch.metrics` 的 status 列把已完成的运行显示成 `cache` / `ok`。已改名并加测试。

## 3.7 本轮完成的工作（2026-09-21）：直接暴露的 MCP 工具读到的页面可以被引用

**现象（用户在只有 MCP 工具的环境里遇到）**：研究员检索、读取都正常，研究结束时报 `NO_EVIDENCE`“研究没有取得任何有可引用证据支持的发现”。

**根因**：`kind: mcp` 的数据源把 MCP 工具原样交给模型，工具不会产出我们自己的 `deerflow.web_page.v1` 等 artifact。`observations.py` 只认这些 artifact，
于是 MCP 读取工具打开的页面只剩一条匿名的整段工具输出，页面里的链接降级成“发现的来源”（不可引用）。整段输出按 `role: read` 本来可引用，
但转换节点看到的条目只有 `{raw_id, receipt_id, tool_call_id, tool_name, origin}`，**没有地址也没有标题**；研究笔记按 URL 引用页面，对不上。
强模型按调用顺序猜（同题真实运行 `956c1ae2`：能完成，但 21 条引用都没有链接，16 条带数字的结论里 5 条引用的证据里找不到这些数字）；
弱一些的模型猜出不存在的 ID，结论被逐条裁掉（`research.output.pruned`），全部裁完就是 `NO_EVIDENCE`。

**修复**（模型看到的工具 schema 与返回都不变，证据在执行后识别）：

- `sources.opened_pages`：`role: read` 的直通工具，被打开的地址取自**调用自己的参数**（`url`/`uri`/`link`/`urls`，或任何取值为绝对 URL 的参数），
  标题与正文用 `extract.document` 从返回里找；登记为 `fetched_document`，引用带地址。一次打开多个 URL 时逐页登记；按文档 ID 打开的内部文档
  以 `mcp://<数据源>/<ID>` 标识，读几次都是一条引用；少于 40 字符的返回不算页面（与供应商路径的 `_page` 一致）。
- `role: search` / `data` 的直通工具：结构化返回按形状逐条成为记录（`extract.records(from_text=False)`）；记录覆盖了答案 ≥80% 的文字时只引用记录，
  否则整段返回仍可引用（形状只识别了一部分、正文被截断时不丢内容）。纯文本返回里的链接仍是“发现的来源”。
- `extract.py`：穿透适配层和服务端的包装（真实运行里发现 MCP 适配层把字符串答案包成 `structured_content: {"result": "<JSON 字符串>"}`，标题与正文在两层之下）；
  `document()` 多返回 `readable`；供应商路径（`providers.mcp`）原来会在这种情况下拿转义后的 JSON 当页面正文，一并修了；
  中文片段按信息量计长（25 个汉字的句子不再被当成标签丢掉）；补了几个常见字段名（`desc`、`passage`、`page_content`、`web_url`、`doc_url`）。
- 转换节点看到的整段输出条目带上标题（“数据源名: 查询词”）；引用摘录的核对同时对照页面自己的正文（JSON 里的同一句话是转义的，永远对不上）。
- 技能：审计规则 `findings-pruned`，`references/debugging.md` 的 `NO_EVIDENCE` 定位路径。

**验证**：`tests/deepresearch/test_mcp_passthrough.py`（12 项：Markdown / 转义 JSON 信封 / 适配层包装 / 内容块 / 部分正文 / 多 URL / 文档 ID / 搜索记录 / 无标题片段 / 引用摘录 /
端到端 `runner.research`，端到端那项在修复前得到 0 条发现）。真实运行：MCP 桩新增直通的 `find_pages` + `open_page`（`research-overlay-passthrough.yaml`），
同题、同模型，旧代码（临时 worktree）与新代码各一次：

| | 旧代码 `956c1ae2` | 新代码 `1d8426f7` |
| --- | --- | --- |
| 状态 | COMPLETED（强模型靠调用顺序猜） | COMPLETED |
| 引用 / 带链接 | 21 / 0，标题是“mcp-fetch: <url>” | 14 / 14，标题是页面标题 |
| 引用的证据类型 | `tool_output`（转义 JSON 原文） | `fetched_document`（可读正文） |
| 带数字的结论里，引用的证据不含这些数字 | 5 / 16 | 1 / 17 |

只有 MCP 搜索、没有读取工具（`489c56b1`）：COMPLETED，16 条引用里 10 条是带标题和链接的单条结果（此后加了“记录覆盖答案则只引用记录”，未再真实复跑）。
没有在浏览器里看这几份报告；用户环境里的弱模型上的 `NO_EVIDENCE` 是由单测复现的，真实运行复现的是同一根因在强模型上的表现。

## 3.8 本轮完成的工作（2026-09-21）：研究步骤接续调度

**现象（用户反馈）**：并发设为 3、计划 5 步，前两步完成后只剩一步在跑，剩下两步却要等它结束才开始。

**根因**：`workflow.dispatch` 按“波次”调度——把当前依赖已满足的步骤一次 `gather`，整波结束后才计算下一波。计划里没有依赖时 5 步同时入队、
由信号量放行，接续是正常的；只要有步骤带 `depends_on`（规划器有时会给，本机 45 次多步骤运行里 5 次），它就得等当前整波里最慢的那个，
哪怕它依赖的步骤早就完成了。旧运行 `8b16f9ef` 里 `type-and-format` 只依赖 100 秒时完成的 `rules-coverage`，却在整波结束后的 115 秒才开始。

**修复**：改成逐个完成逐个调度（`asyncio.wait(FIRST_COMPLETED)`）：步骤在依赖都结束且有空位时立刻开始，并发上限仍由信号量保证。失败语义保持原样，
只是从“按波判断”改成“按运行判断”：单个步骤失败且已有步骤成功（或已有发现）→ 立刻降级为占位结果，依赖它的步骤继续；能跑的全部失败且一无所获 → 运行失败、
重试时重跑；全部是预算收尾类错误 → 继续写报告；致命错误 → 不再启动新步骤，等运行中的步骤结束（结果已持久化）后失败。提前退出（取消、停止、存储错误）时
取消并等待所有在跑的步骤真正结束，保持原来 `gather` 的 drain 语义。

**验证**：`test_workflow.py` 新增 3 项（接续调度在修复前超时失败；失败步骤之后的接续；致命错误不再启动新步骤且已完成的不重跑），全量连跑 8 次无偶发失败。
真实运行 `062ec4f8`（MCP 桩，5 步、3 并发，规划器给出第 4、5 步依赖第 1、2 步）：`cost-comparison` 在 `quota-sla` 完成的同一时刻（23.3 秒）开始，
此时另两步还在运行（23.8 / 25.8 秒结束）；`onboarding-guide` 在它依赖的 `scaling-migration` 完成时（25.8 秒）开始。各步时长接近，差距只有 2.5 秒，但事件顺序是确定的。

**顺带发现并已修复：编辑计划时手工加的依赖被丢掉。** `POST /plan/edit` 提交的计划要经规划模型规范化，而规划提示词要求“尽量不要依赖”，模型把用户加的
`depends_on` 全删了（真实运行 `a2570913`：v2 计划的依赖为空）。步骤之间谁依赖谁是用户的决定，不该重新规划：`runner.keep_declared_dependencies` 在规范化之后
按步骤 ID 把用户声明的依赖放回去（被删掉的步骤上的依赖随它一起去掉；放回后若成环则保留模型的结果），规划提示词第 7 条也补了“完整计划且没有 revision 时保留
units、id、顺序与 depends_on”。用文字提的修改（`revision`）仍由模型决定。只有结构化 API 客户端走这条路，页面用的是 `plan/pause` + 消息。
真实验证 `de4e545c`：编辑后 v2 为 `cost-model ← quota-sla`、`security-baseline ← scale-out`；运行中 `security-baseline` 在 `scale-out` 完成的同一时刻（25.3 秒）开始，
此时 `quota-sla` 还要再跑 7 秒（32.2 秒结束，`cost-model` 随即开始）。这也是接续调度更清楚的一次实证。

## 3.9 本轮完成的工作（2026-09-21）：日志记全 + 整包导出 + 保留期

**起因**：需要「所有内容、请求、请求的结果、MCP 的请求都记录好」，并能拿到完整日志做分析。

**盘点结果**（基于本机 14 次真实研究的数据库）：模型调用那一层本来就完整（`research_llm_exchange` + `research_llm_blob`，全部消息含 reasoning、工具定义、参数、完整回复，
按内容去重压缩，单条上限 100 万字符）。缺的是另外三层：

1. **工具调用**：`research_tool_call` 不存参数正文也不存返回正文，只有 16 位参数哈希 `request_key`、按 role 抽出的 `query[:300]` / `url[:2000]` 和 `output_chars`。
   正文散在三处、上限各不相同：trace span 事件（16K，静默截断）、执行缓存（20K，且**只有成功完成的步骤才写**）、下一次模型请求的审计副本（100 万）。**错误原文从未落盘**。
2. **MCP 协议层**：完全没有。`mcp.py` 的 `config={"callbacks": []}` 把内层调用对回调隐藏（这是对的，否则工具预算重复计费——真实事故：25 次搜索计成 41 次），
   代价是服务器名、真实远端工具名、实际发出的参数、`isError`、`structured_content`、连接失败原因、工具发现结果全部不可见。
3. **HTTP 供应商层**：完全没有。method、URL、请求体、状态码、响应体、重定向链一个没记；失败时错误体被砍到 300 字，写入调用行时再丢掉。

外加：代码里搜不到任何 TTL、prune 或 VACUUM，本机 14 次研究已占 760MB（`research.sqlite3` 193MB + `checkpoints.sqlite3` 567MB，单次研究约 100–155MB 检查点）。

**做了什么**

- 新表 `research_tool_exchange`（与 `research_tool_call` 同键，存完整参数/返回/artifact/错误原文）、`research_wire_call`（一行一次 MCP 或 HTTP 往返，带 `call_id` 索引）、
  `research_audit_blob`（两者的正文，内容寻址 + zlib 去重）。都是 `CREATE TABLE IF NOT EXISTS`，旧库自动兼容；新读取方法带 `missing_table` 守卫，
  技能脚本只读打开旧库时返回空而不是抛错。
- 新模块 `wire.py`：`WireRecorder` + 一个在**工具协程内部**绑定的 contextvar。不在构造处绑、也不在回调里绑——子 Agent 在自己的事件循环上跑工具（上下文是空的），
  回调处理器又在自己的上下文副本里，两处都够不到发起调用的代码。任务创建时会继承当时的上下文，所以在协程里绑能一路传到 `asyncio.wait_for` / `asyncio.to_thread` 之下的每个叶子。
- **关联方式**：LangChain 会给声明了 `callbacks` 参数的工具协程注入该次工具运行的子回调管理器，它的 `parent_run_id` 正是 `trace.on_tool_start` 用作 `research_tool_call` 行 ID 的那个值。
  比往 artifact 里塞 ID 可靠得多——MCP 适配层经常返回 `None`，根本没有 dict 可塞。包装函数**不能**用 `functools.wraps`：`inspect.signature` 会跟随 `__wrapped__`，
  注入会静默停止；也绝不能把那个管理器往里传，继承了它的调用会被计成第二次工具调用。
- 两个接入点：`providers._http`（覆盖所有预设供应商与自定义 HTTP；`duckduckgo`/`ragflow`/`lightrag` 走自己的 SDK，不在覆盖内）和 `mcp.MANAGER.call` / `MANAGER.tools`。
  签名只新增带默认值的 `recorder=None`，`runner._source_tools` 沿 `budget` 同一条路传下去。
- 去掉静默截断：超过 `audit_max_chars` 的正文显式标 `truncated` 并记原长。
- 补全既有字段：尝试链不再只留前 10 条、补 `http_status`/`cooldown_seconds`/`message`；预算拒绝的 artifact 带 `used`/`limit`/`scope`（以前只有第一条事件里有）。
- 新模块 `logbook.py`：`export` 把一次研究的全部记录合成一个自包含 JSONL（10 种 `record`，正文已还原），`prune` 是唯一的删除路径（删 run 在所有表里的行 + VACUUM）。
  接口 `GET /{id}/log/export`。四个新设置：`tool_audit`、`wire_audit`、`audit_max_chars`、`audit_retention_days`（默认 `null` = 不自动删）。

**验证**：`tests/deepresearch/test_logging.py` 新增 15 项（HTTP 请求/返回、完整错误体、重定向逐跳、MCP 服务器与真实工具名与参数、`isError` 与超时可区分、
工具发现不记凭据、供应商猜中的参数名、`callbacks.parent_run_id` 关联、记录器抛异常不影响研究、无记录器时不写表、超限显式标注、工具层出入参、整包导出、保留期清理）。
全量 `tests/deepresearch` 296 项通过。

真实运行（MCP 桩 + `deepseek-v4-flash`，仅直通 MCP 工具，run `ba344612`）：导出 375 条记录 2.3MB，其中

| 核对项 | 结果 |
| --- | --- |
| 桩自己记录的 MCP 调用 vs 导出里的 `wire_call` | 35 / 35，参数逐条对得上 |
| 带完整参数与返回的 `tool_call` | 39 / 39 |
| 关联到工具调用的 `wire_call` | 35 / 35 |
| 桩的 token / cookie 出现在导出里 | 0 次 |

**没做的**（用户本轮只要整包导出）：`show_call.py` 的逐条打开、`GET /{id}/tools` 接口、前端「工具调用」页签。

## 3.10 本轮完成的工作（2026-09-21）：研究完成后计划卡折叠而不是消失

**现象（用户反馈）**：研究完成后计划卡直接消失了，想回看做了哪些步骤没有入口。

**原来的行为**：对标 ChatGPT——报告出现后由“研究完成情况”统计行加报告卡取代计划卡。`presentation.planCardHidden` 判定隐藏，
`plan-card.tsx` 返回 `null`，同时 `research-conversation.tsx` 把这条 plan 记录整个从消息列表里过滤掉（否则会留下一个空的回合）。
计划只在“活动”面板里还能看到，会话里没有任何回到它的入口。

**改成**：`planCardHidden` → `planCardFolded`，语义从“隐藏”变为“折叠”。完成后计划卡收成一行 `<details>`：
`研究计划 · <标题>`，点开是全部步骤和各自的最终状态（完成 / 未完成）。会话不再过滤 plan 记录——卡片现在总会渲染出东西，不存在空回合。

标签按**是否被替换**判断而不是按是否折叠：`latest ? "研究计划" : "计划已更新"`。这一点是端到端测试抓出来的——
演示流程里用户改写过计划，旧版 v1 和新版 v2 都属于 cycle 0 且都有报告，按“是否折叠”判断会让两张卡都写“研究计划”，
真正跑的是哪一版就分不出来了。失败和被停止的研究不受影响，仍然显示错误卡与“从检查点恢复”。

**验证**：`plan-card.dom.test.tsx` 与 `presentation.test.ts` 改为断言折叠与展开（`<details>` 在 jsdom 里收起时内容仍在 DOM 中，
所以断言的是 `open` 状态与内容归属，不是元素存在与否）；前端全量 1458 项通过；`pnpm check` 通过；
浏览器端到端 `playwright.deepresearch.config.ts` 5 项通过（新增：点开折叠卡后步骤可见）。
真实网关上用浏览器看过已完成、失败、已取消三种运行，以及深色模式。

## 3.11 本轮完成的工作（2026-09-22）：证据带上发布日期，时效要求可机械校验

**起因（用户要求）**：「这是需要加上 Publish date 保证时效性。」

**原来的行为**：日期全程丢失。`observations.py` 的记录循环写死了 `published_at=None`，
读到的页面根本不提取日期。实测本机全部历史运行：**34,053 条证据行，0 条带日期**。
而下游早就在等它——`runner.py` 给写作者的证据目录里有 `published_at` 字段，
`report.py` 的参考文献行有 `· <日期>` 的渲染逻辑，两处都是死代码。
`git log -S 'published_at=None'` 显示这个 `None` 是 `c7be5e96` 那次从 `model_copy` 改成 `derive` 时**顺手带过来的**，不是决定。

**改成**：

- 新增 `extract.published(value)`：防御式日期解析，返回 `datetime` 或 `None`。认 ISO、`2026/07/10`、
  `2026年7月10日`、`Apr 21, 2026`、`21 Apr 2026`、epoch 秒/毫秒；拒绝相对时间（`3 天前`、`2 days ago`）、
  `0000-00-00`、epoch 零、1990 年之前和今天 +400 天之后的值。**解析不了只丢日期，绝不丢证据**——
  `derive()` 走 pydantic 校验，一个非 ISO 字符串会让整条记录返回 `None`，那就等于因为日期格式丢掉了内容。
- 记录循环改成 `published_at=extract.published(record.get("published_at"))`。这一行同时覆盖三条产出记录的路径：
  我们自己的 `deepresearch.records.v1`（`role: data`）、`deepresearch.search.v1` 的逐条结果（开了 `cite_search_results`）、
  以及直通 MCP 按形状识别出的记录。所以自建 MCP 和内置 websearch 一起生效。
- 读到的页面也带日期：`extract.document()` 从返回顶层或 `metadata` 里取日期字段，
  `sources._page()` 带着它，页面证据行 `fetched_document` 上就有了。
- `validators.py` 里那条 `date` 缺口检查原来 gate 在 `provenance == "document"` 上，而研究从不产出这个 provenance，
  所以它是死代码。改成只在**能证明**时报缺口：有日期的证据全部早于 `not_before` → 报缺口并触发补研；
  一条日期都没有 → 不报，因为补研也变不出服务端不给的日期。
- MCP 桩（`examples/deepresearch/mcp-stub/`）的 `find_pages` 与 `open_page` 按格式文档的建议声明 `published_at`。
- **配置出来的读取数据源也补齐了**（第二轮发现的口子）：原来只有直通 MCP 的页面有日期，走 `channels.py` 的
  `role: read` 数据源（Jina、direct、`type: mcp` 供应商）生成 `deerflow.web_page.v1` artifact 时不带日期，
  `sources.fetched_source` 也不读，等于同一份报告里日期有没有取决于数据源怎么接的。现在：供应商把页面声明的日期
  交给 `channels`，由 `_stated()` 统一归一成 ISO 写进 artifact，`fetched_source` 与两条页面证据路径都带上。
  同时补了两处真实世界的覆盖：Jina Reader 的字段名 `publishedTime`，以及 `extract.page_date()` 从 HTML 头部读
  `<meta article:published_time>` / `<meta name="date">` / `<time datetime>` / JSON-LD 的 `datePublished`
  （readability 只保留正文，日期在 head 里，所以要单独读原始 HTML）。
  实测三个真实网页：Wikipedia `2023-06-12`、Qdrant `2024-10-08` 都读到了；Anthropic 新闻页整页没有任何日期声明，
  如实为空而不是编一个。

**真实验收抓到的回归**：第一次真实运行直接 `EXECUTION_FAILED`。堆栈落在
`structured.convert_answer` 的 `json.dumps` —— 页面证据目录是给转换节点的 JSON 载荷，
而我把解析后的 `datetime` 放进了 `address`，`datetime` 不可序列化，整个步骤挂掉。
修法：页面上的日期存 ISO 字符串（pydantic 再解析回 `datetime`），并补一条断言目录可 JSON 序列化的回归测试。
单元测试全绿而真实运行挂掉，这一条值得记住。

**验证**：
- `tests/deepresearch` 302 项通过（新增 4 项：日期进证据与参考文献、九种真实写法的解析、读页日期、目录仍是 JSON）；
  `ruff check` / `ruff format --check` 通过。
- 拿本机历史运行里**真实供应商给过的**日期字符串核对解析器：455 次出现、27 种写法，解析成功 451/455；
  剩下 4 条是从页面正文里误抓的散文（含被正确拒绝的 `8 Nov 2029`）。
- 一次**只有 MCP 搜索 + MCP 读取**的真实研究（网关 8013、桩 9731、`deepseek-v4-flash`，79s / 32 次工具 / 28.3 万 token）：
  **12 条引用 12 条带日期**（改动前同一条链路 0 条）；126 条证据里 18 条带日期，
  带日期的全是 `fetched_document`，信封与只“见过”的链接没有日期——正确，因为没人声明过。

## 3.12 本轮完成的工作（2026-09-22）：数据源可以声明自己站点的图标

**起因（用户）**：自建 MCP 的返回里有 `logo_url`，「需要渲染在页面的」。

**为什么不能直接渲染**：`<img src="来源给的 URL">` 等于读者一打开报告，浏览器就逐个访问被引站点（IP、Referer 交给第三方，
也可以当追踪信标用）。`favicons.py` 存在的原因正是这个——图标一律由网关代取。内网站点的问题则是另一头：
它们通常没有 `/favicon.ico`，网关猜不到，界面上只能显示首字母徽标。

**改成**：数据源可以在结果里为**自己的域名**声明图标，由网关代取。

- `extract.declared_icons(payload)`：从任意信封里找"同一个对象既有地址又有图标"的组合，认 `logo_url`、`logo`、
  `icon_url`、`icon`、`favicon`、`favicon_url`、`site_icon`、`site_logo`（含驼峰）。**同源才认**：
  图标主机必须等于该条结果的主机，否则一个数据源就能让网关去取、缓存并向所有读者展示别人家选的图片。
- 图标不再被当成"见过的链接"记成一条 `observed_source`，也不再压低第四节那个 80% 覆盖率
  （实测同一份返回：0.54 → 0.89，参考列表因此少一条没有链接的条目）。
- `research_icon_hint(domain, url, at)` 新表记下这条声明，随来源行一起写（`store.record_call`）。
  `favicons.py` 取图标时优先用它，再退回原来的 `/favicon.ico` 与首页声明。
- 新增运维设置 `favicon_private_network`（默认 `false`）：允许图标代取访问内网地址。默认关，因为打开意味着
  数据源可以指定一个网关会去取的地址；即使打开，也只取该来源自己引用的那个主机。
- **前端零改动**：`SiteIcon` 本来就是按域名问 `/api/deepresearch/favicon`，能取到就显示图标，取不到显示首字母。

**真实运行里发现的一个坑**：第一次跑完，19 条来源行 0 条带图标。原因是 MCP 适配器把服务器的 JSON **又包成一个字符串**
放进 `structured_content.result`（也可能是 content block 的 `text`），我的遍历只解析了最外层字符串。
补成遍历时遇到像 JSON 的字符串就解析（限 40 次、单串 1MB）。这个形状是从真实运行的 `research_wire_call` 记录里抄出来当测试数据的。

**验证**：
- `tests/deepresearch` 309 项通过（新增：图标读取与同源规则、三种适配器包装形状、声明图标优先于猜测、跨站声明被忽略、
  内网图标需要开关）；`ruff` 通过；指导链回到软上限内。
- 真实研究（仅 MCP 检索 + 读取，桩声明 `logo_url`）：19 条来源行 **13 条带图标**，logo 自身**没有**被记成来源，
  `research_icon_hint` 恰好一条。
- 真实网络验证取图逻辑（qdrant.tech）：声明了图标 → 返回声明的那张 PNG（4977 字节）；没声明 → 退回猜测
  `/favicon.ico`（15086 字节）；声明的是别人家域名 → 忽略，退回猜测。

## 3.13 本轮完成的工作（2026-09-22）：Word 导出按"能发给人看的报告"重做

**起因（用户）**：「导出Word，再优化一下这个Word的具体的格式。引用的链接……而且Mermaid图表也要在Word中显示出来。现在是代码」

**先量了一遍**（真实报告，78 条引用、4 张图）：Markdown 和 HTML 导出其实都不差，Word 是唯一的差生——
整个 docx 里 `w:hyperlink` 出现 **0 次**；参考条目是 `[1] 标题⏎裸URL`，丢了发布日期（新报告 100% 有）、
丢了「检索摘录，未读取原文」、丢了站点名；表头不加粗无底纹；没有页码和目录；4 张 Mermaid 全是等宽代码。

**改成**（`report.docx_document`）：

- 正文引用是上标超链接，跳到文末条目的书签；条目标题是真超链接，后面是站点名 · 发布日期，
  只有检索摘录的标注出来。这几项现在与 Markdown / HTML 一致。
- 文首可点击目录、表头加粗带底纹并跨页重复、页脚页码、标题样式补 `eastAsia` 中文字体回退、
  演示模式提示移到文首、表格单元格不再被正则删掉加粗与行内代码。
- Mermaid 渲染成图片带「图 N」题注。**浏览器出图**：`diagrams.ts` 用页面已有的 mermaid 渲染，
  转 PNG 随 `POST /{id}/report/docx` 回传（内网无渲染服务、无外网也能用，后端零新增依赖）。
  图片按**自身源码**归档，只会落在它自己的位置。没有图的块保留题注、源码进附录，
  `GET ...&format=docx` 走的就是这条降级路径，供脚本与 CLI 用。

**两个只有真跑才会发现的坑**：

1. **canvas 被污染**。Mermaid 默认用 `<foreignObject>` 排标签，这样的 SVG 画进 canvas 后
   `toDataURL` 直接抛 "Tainted canvases may not be exported"，**3 张图一张都出不来**。
   改成导出时 `htmlLabels: false` 渲染，完事恢复页面配置。
2. **图片尺寸只限了宽**。真实的纵向流程图按原比例是 **19.46 英寸高**，Word 一页文字区只有 9 英寸，
   会在分页处被裁掉。补上按页高等比缩放（图 2 从 5.85×19.46 变成 2.41×8.00）。

还有一个值得记的：key 一开始设计成源码的 SHA-256，写完才想起 `crypto.subtle` **在纯 HTTP 的内网源上不可用**，
那样会在你们的部署里静默失效。改成用**源码本身**（折叠空白）当 key——同样安全，浏览器零依赖。

**第二轮（对标 ChatGPT 实测后）**：用户反馈"还是没有图"、要求对标 ChatGPT 的 Word 排版。

先用**本机的 Microsoft Word 把 docx 转成 PDF**（AppleScript，`save as ... format PDF`）看真实版面——
这比任何猜测都快。结论：图**在**（第 13 页），问题是版面：8 英寸高的图把自己挤到下一页，
前一页留了大半页空白；表格跨页时切开一行，续页表头下只剩"迟低"两个字。

然后用 computer use 登录 ChatGPT，导出一份真实深度研究报告的 Word（报告卡片右上角 →「导出到 Word」；
ChatGPT Work 文档只能导出 .md）。拆开对比后发现**它比我们更简单**：不设页面尺寸（用 Word 默认）、
表格 autofit 且没有 cantSplit（照样断行）、没有底纹、没有目录和页码、引用是行内超链接不是上标。
它唯一明显更好的一点是**三线表**——只有顶线/表头线/底线，没有竖线没有底纹，所以断表时接缝不显眼。
它的表格也有问题：一列被 autofit 挤成竖排单字。

据此改了四处：
1. **表格改三线表**（`rules` / `rule_under`），去掉全框线与表头灰底。
2. 页边距改 1 英寸，正文栏宽 6.0 → **6.5 英寸**。
3. 每行 `cantSplit` + 固定列宽均分（这两点比 ChatGPT 好，它两样都没有）。
4. 按页高缩放后宽度不足正文栏 55% 的图**单独起一页**，图与题注 `keep_with_next`。

实测同一份报告：**27 页 → 21 页**，表格续页不再出现孤行，高图独占一页且前页是满的。

**还剩一个物理限制**：一张 1:3.3 的纵向流程图，在竖版页上最多只能 2.7 英寸宽，标签必然小。
这不是导出能解决的，根因在图的比例，所以 `prompts.py` 加了约束：优先 `flowchart LR`、约八个节点、
长链拆成两张图。

**验证**：
- `tests/deepresearch` 334 项通过（新增 25 项：上标链接与书签、条目的链接/站点/日期/摘录标注、
  渲染与未渲染两条路径、只接受真 PNG、表头样式、单元格强调、目录、页码、中文字体、演示提示位置、
  老报告无 basis/日期仍可导出、图片不超页宽页高且保持比例、端点鉴权）；`pnpm check` 通过。
- 真实链路：浏览器（同一个 mermaid 11.12.2）渲染真实报告的 3 张图 → 2416×534 / 562×1868 / 948×1840 的 PNG →
  `POST /report/docx` 返回 200、342 KB → 成品内嵌 **3 张图片**、281 个超链接、68 个外部链接、201 个上标、
  无附录（没有降级）、正文无源码。
- 未走完的一段：React 组件到 `renderDiagrams` 的接线（5 行）只过了 typecheck 与 lint，没在真实页面上点过——
  本机 3100 端口上是用户自己的 dev server，不能占用，也不能在它运行时跑 `pnpm build` 去搅 `.next`。


## 4. 真实验收

使用 DeepSeek `deepseek-v4-flash`、原生 `web_search` / `web_fetch`（Jina 无 key 模式），非演示 Runner。
数据只在本机 `.deerflow/deepresearch/` 下，不在 GitHub。

| 任务 | 数据目录 | 覆盖的流程 | 结果 |
| --- | --- | --- | --- |
| `b96045af-75db-41ae-bec3-c24c91fd661a` | `live-u6st1p0d` | 与 ChatGPT 样本 A 同题（参考 GitHub/GitLab 规划代码仓智能化）；倒计时自动开始；一个补研单元触发旧的 180 秒超时后在页面重试 | `COMPLETED`；890 秒、142 次搜索、90 个页面、69 条引用；8 章、13 张表、3 张 Mermaid 图；5 条局限；md/html/docx 导出与阅读器、引用卡片、来源面板已检查 |
| `82490499-7edd-4763-a276-07a90f8257c0` | `live-m232rndj` | 宽泛题“调研一下最新的人工智能的发展”；倒计时自动开始；研究中“更新”要求加入模型对比表；一个补研单元失败后降级继续 | `COMPLETED`；961 秒、232 次搜索、127 个页面、78 条引用；36,746 字符、13 张表、4 张 Mermaid 图；“更新”要求的模型对比表进入第二章 |
| `68ff862b-20dd-45a8-b7e9-90a254e3d889` | `live-m232rndj`（`--resume-dir` 重启后） | 收敛选型题（Elasticsearch/OpenSearch/Meilisearch）；倒计时中编辑并加入 Typesense → 确认回复 → 约 16 秒后直接开始；带 gzip 的事件流检查 | `COMPLETED`；854 秒、179 次搜索、89 个页面、73 条引用；38,064 字符、10 张表、3 张 Mermaid 图；无重复摘要、标题无编号；明确推荐 Meilisearch 社区版并给出 ES 对照试点、切换条件与路线图；导出 md 89 KB、html 10 表 324 个引用上标、docx 10 表 36 个标题 |

对照：ChatGPT 样本 A 约 6 分钟、41 条引用、381 次搜索。本地耗时约为 2 倍、引用更多；数量不代表质量。

验收中发现并已修复的问题：

1. Next 开发代理对 SSE 做 gzip 缓冲，规划阶段页面不更新。修复后带 gzip 的请求 6 秒收到 5,531 字节明文事件（修复前 10 字节），浏览器中计划卡无需刷新即出现。
2. 研究员进度部分为英文并提到技能文件。提示词写明语言名称并要求包括第一句；活动投影过滤后，两份真实数据中英文与技能文件相关进度均为 0。
3. 补研单元产生 535 条原始证据（401 条发现链接），超出 500 条上限却被误报为 `EVIDENCE_REFERENCE`，约 7 分钟的研究结果丢失。用缓存的真实执行离线复现后修复。
4. 修改计划并直接开始后运行快照仍是 v1 单元，活动时间线漏掉新步骤。planner 现在随计划写入 `units`。
5. 报告出现重复的执行摘要章节、编号标题、一行英文元话语，以及“未新增证据 ID”这类过程说明。已由大纲校验、标题规范化和清理规则修复，68ff862b 报告已不再出现前三类。
6. 73 个引用编号只对应 62 个页面，并有 “Untitled” 标题。改为按页面编号，用真实证据池重算为 63 个编号；前端兜底后历史报告也不再显示 “Untitled”。
7. 活动进度 `steps_done` 计入补研步骤。改为只统计计划步骤。

注意：第 5 项的“过程说明”清理、第 6 项的页面级编号只有单元测试与对已存报告的离线重算，尚未在新的真实报告中出现。

**观测功能的真实验收**（同一数据目录 `live-m232rndj`，指标由新代码计算）：

| 任务 | 内容 | 指标结果 |
| --- | --- | --- |
| `2e5f59eb-438f-490f-b2c7-6ec31c4b9f34` | Redis 与 Valkey 选型；6 个计划单元 + 6 个补研单元 | `COMPLETED`；实际计算 691 秒（研究 596 秒、写报告 67 秒）；模型调用 209 次、Token 4.46M（输入 4.27M、输出 183k、缓存命中 24%）、p95 19.8 秒、最大上下文 44k；工具 382 次（搜索 159、读取页面 103）、失败率 12%（`web_fetch` 30、`web_search` 17 次 `SSLError`）；子 Agent 24 次、最大并行 3、排队累计 12 分 22 秒；71 条引用、每条引用 62.8k Token；单元每条引用 Token 从 14.5k 到 150.7k 不等 |
| `9718f578-7857-4636-85d4-e246d4c1dbab` | 向量数据库选型（Milvus/Qdrant/Weaviate）；API 创建，预算限 4 个单元、1 轮、400 次工具、2400 秒 | `COMPLETED`；约 5 分钟；模型调用 86 次、Token 1.67M（缓存命中 25%）；工具 140 次（搜索 41、读取页面 58）、失败 18 次：`HTTP 429` 13（无 key 的 Jina 限流）、`ForbiddenError` 4（搜索）、`EmptyContent` 1；同参数重复调用 1 次；预算使用：工具 35%、时长 12%；47 条引用、每条引用 35.5k Token |

核对方式：两次任务的 Token 合计与 `run.usage` 一致，工具次数与 `usage.tool_calls` 一致，搜索次数与读取页面数与活动接口一致，
引用数与报告一致，子 Agent 的 Token 合计与 `purpose=agent` 的模型调用合计一致；9718f578 的 140 条工具记录全部带 `request_key`，
18 条失败全部带 `error_type`，按原始记录独立重算的重复次数与汇总一致。浏览器中检查了两次任务与一条历史任务（68ff862b，
`metered=false`）的“指标”页签。命令行 `python -m deepresearch.metrics --data-dir .deerflow/deepresearch/live-m232rndj/research`
能列出该目录全部 5 个任务，历史任务的未测量列为空。费用路径用临时配置（测试单价，不是真实价格）验证过：
`--config` 读取 `pricing` 后 9718f578 的 86 次调用全部计价，总额与按 Token 手算一致，并正确分摊到阶段与单元；临时配置已删除，
私有验收配置没有写入单价。

指标暴露的优化线索（尚未处理）：读取失败主要来自 Jina 无 key 模式的限流与连接失败，而不是目标网站；研究并发只有 3，
2e5f59eb 的单元与章节排队累计超过 12 分钟；2e5f59eb 中补研单元的每条引用 Token 普遍低于计划单元（只有这一次任务有补研，样本很少）。

浏览器检查（1568×691 视口）：计划卡、编辑引用条、确认消息、进度卡与“更新”、统计行、报告卡、阅读器悬停目录、
来源页签（网站图标、摘要）、活动页签（网站标签、跟随）、引用悬浮卡（原文片段）。窄屏只在 Chrome 最小窗口宽度 606 px
检查过（阅读器与来源抽屉无横向滚动），未在 390 px 或真实手机上验证。

### 2026-09-18：配置独立、改写、供应商故障切换与调用审计的真实验收

数据目录 `live-lm9f03p0`，模型 DeepSeek `deepseek-v4-flash`，研究配置由设置页保存的第 1/2 版驱动。
本次验收刻意用**全部付费搜索接口都不可用**的环境跑：Tavily 额度用尽（HTTP 432）、Serper 密钥无效（403）、Jina 密钥无效（401）。

| 任务 | 覆盖的流程 | 结果 |
| --- | --- | --- |
| `52e358b9-5f29-47c7-a6d7-8a514d3caca0` | 推理框架选型；倒计时中“编辑”→ 改写合并 → 确认话术 → 直接开始；6 个单元 + 6 个补研单元；设置页把压缩阈值调到 0.12 的压力设置 | 一次 `evidence_merge` 失败（见下）后从检查点恢复，`COMPLETED`：70 条引用、29.6k 字符、13 张表、2 张图、5 条局限；186 次搜索、132 个页面；模型调用 287 次、Token 5.94M |
| `19f489e5-89b8-481e-b9d9-f9eccfe3e0a2` | uv 与 Poetry 选型；默认压缩设置（阈值 0.6、保留 0.4）下的对照跑；5 个单元 + 2 轮补研 | `COMPLETED`：885 秒、115 次搜索、112 个页面、72 条引用、21.9k 字符、18 张表、1 张图、5 条局限；无单元失败；模型调用 176 次（**压缩 0 次**）、模型耗时 669 秒、Token 4.33M |

**供应商故障切换（`/metrics` 的 `tools.by_provider`，共 132 次切换）**

| 工具 | 供应商 | 尝试 | 应答 | 错误 | 跳过（冷却） | 错误类别 |
| --- | --- | --- | --- | --- | --- | --- |
| `web_search` | tavily | 12 | 0 | 12 | 174 | quota（HTTP 432，账户额度用尽） |
| `web_search` | serper | 12 | 0 | 12 | 174 | auth（密钥无效） |
| `web_search` | duckduckgo | 186 | 185 | 1 | 0 | empty 1 |
| `web_fetch` | jina | 7 | 0 | 7 | 192 | auth（密钥无效） |
| `web_fetch` | direct | 199 | 149 | 50 | 0 | blocked 30、not_found 17、empty 3（都是单个页面的问题，没有冷却整个供应商） |

结论：三个付费接口全部不可用时，研究仍然完成，靠的是自动切换到不需要密钥的兜底供应商；
这正是用户反馈“搜索经常失败”的根因与修复。

**改写链路**：首次改写 3.7 秒产出完整研究请求；倒计时中补充“只看单台 4090 / 单台 A100 80G，并补上 Qwen3 支持情况”后，
改写合并出新请求并写出确认话术“已更新需求：硬件范围限定为……并新增各框架对 Qwen3 系列模型支持情况的对比维度。”，
计划从 5 步变成 6 步并立即开始。

**压缩开销对照（本轮发现的重要问题）**

| | `52e358b9`（压力设置：阈值 0.12、按条数保留 16 条） | `19f489e5`（默认：阈值 0.6、按 token 保留 40%） |
| --- | --- | --- |
| 压缩次数 | 94 | 0 |
| 压缩耗时 / 模型总耗时 | 2188 秒 / 2908 秒（**75%**） | 0 秒 / 669 秒 |
| 单次压缩耗时 | 45–55 秒 | — |

原因：压缩后保留的消息本身仍然超过阈值，于是每一轮都要重新压缩一次。修复是把保留策略改为**按 token 且只占阈值的一部分**
（`keep_fraction`，默认 0.4），压缩后上下文必然降到阈值以下，不可能连续触发。128K 上下文的模型在默认阈值（76.8k）下，
一个研究单元通常根本不会触发压缩。

**故障切换（`19f489e5`，164 次切换）**：tavily 与 serper 各尝试 11 次后进入冷却（各跳过 104 次），
duckduckgo 应答 88/115，direct 应答 115/143（28 次是单页面级错误），jina 5 次鉴权失败后跳过 138 次。

**验收中发现并已修复的问题**

1. 引擎压缩会把子 Agent 的系统提示词一起压缩掉，研究员中途丢失方法论；而且当压缩窗口里只剩系统提示词时，
   LangChain 的人类锚点裁剪器会把整段研究过程丢弃，摘要写成“尚未开始研究、未执行任何操作”。已在引擎修复并加回归测试。
2. 一条 1584 字符的 GitHub 签名图片链接被当作页面标题（页面没有标题时用 URL），超过 `RawEvidence.title` 的 1000 字符上限，
   `evidence_merge` 抛 ValidationError，整个研究失败。展示字段现在截断而不是拒绝，重试后直接复用已完成的单元结果并写出报告。
3. 角色方法论把 SKILL 文件的 YAML 头信息也发给了模型。
4. 上一轮遗留：`model_copy` 不重建引擎模型索引，研究的单次输出上限从未生效。

**语言合规**：`52e358b9`（提示词加强前）的 38 条进度说明中英文各半——英文全部来自同一个研究循环，一旦第一句是英文就一直是英文。
提示词加入对比强调（“即使这些指令和你读到的网页是别的语言”）后，`19f489e5` 的 **132 条进度说明全部是中文**。

历史验收：2026-09-16 的 `fa984b8d-91bd-4dd6-8609-6e88d4660a86`（数据目录 `live-3j8hr_hv`）暴露了线程 ID 超长、AIO `ls`
阻塞等问题，修复后从检查点恢复完成，过程与证据见 [STABILITY_AUDIT_2026-09-16.md](STABILITY_AUDIT_2026-09-16.md)。
同一目录的 `ed5d5da5-f155-4a60-bf1f-00d69b822764` 是 SQLite/PostgreSQL 选型的旧格式报告样本。

### 2026-09-19：仅 MCP、无原文、Chat Completions 网关的真实验收

环境（数据目录 `live-` 前缀，由验收启动器新建；网关端口 8011，与用户正在使用的 8001 互不影响）：
- 模型：DeepSeek `deepseek-v4-flash`，但按 `provider: openai` + `base_url` 接入，即纯 OpenAI Chat Completions，不走 DeepSeek 专用类。
- 数据源：网页搜索与网页读取配置为 `enabled: false`；只有一个本机 MCP 服务（streamable HTTP），同时校验 `Authorization: Bearer …`
  与 `Cookie: sid=…`（配置写成 `Bearer ${ENV}` 与 `sid=${secret:…}`，Cookie 经设置接口保存）；三个检索工具返回三种格式
  （无标题无链接的 JSON 片段、`Title:/URL:` 文本块、带 `name`/`description` 信封的结构化对象），**没有任何工具能打开原文**；
  另有一个危险工具 `delete_page` 用来检验白名单。
- 验收启动器新增 `--research-overlay <yaml>`：用一份 YAML 覆盖生成的研究配置（数据源、`mcp_servers`、`nodes`、预算），不改示例文件。
- 这个 MCP 服务、它的语料和覆盖层已放进仓库：`examples/deepresearch/mcp-stub/`（`server.py`、`corpus.json`、`research-overlay.yaml`、README 里有启动命令；`MCP_STUB_LATENCY` 可模拟慢工具）。

| 运行 | 条件 | 结果 |
| --- | --- | --- |
| `b840e200`（修复前基线） | 每步 8 次检索、1 轮补研 | `COMPLETED` 119 秒、40 条引用、9 章 7 表 1 图；暴露下面四个问题 |
| `2157aede`（修复后，同题） | 同上 | `COMPLETED` 84 秒、25 条引用、9 章 6 表 1 图；整理节点输出 5808 → 2453 token、P50 17.6 → 8.8 秒、0 错误；工具预算 28 = MCP 服务端实际收到的 24 次调用 + 4 次 `read_file`；摘录类引用带“检索摘录，未读取原文” |
| `bd9be64e`（MCP 每次调用 7 秒且串行，时间上限 120 秒） | 第一版时间收尾 | `FAILED NATIVE_AGENT_TIMEOUT`：三个步骤都在研究截止点被硬超时杀掉，读到的内容全部丢失 |
| `5dbeb29d`（MCP 每次 12 秒，时间上限 100 秒，修复后） | 时间收尾 | `COMPLETED` 74 秒、10 条引用；三个步骤在第一批检索后收到 `scope: time` 的停止提示，各自写出笔记并在局限里说明“第二批检索被时间预算终止” |

鉴权与白名单（直接对 MCP 服务验证，亦经设置接口 `POST /settings/mcp-tools` 验证）：错误凭据 → `kind: auth`，
“HTTP 401, the server rejected the credentials…”；保存的 Cookie 过期而请求自带凭据时只在该请求内覆盖；
`delete_page` 不在白名单 → 解析工具时拒绝，列出工具时 `allowed: false`；四次真实运行里 MCP 服务端日志中 `delete_page` 调用为 0。

真实运行暴露、单测此前发现不了的问题（均已修复并补测试）：
1. `type: mcp` 供应商的内层 MCP 调用会触发研究回调：同一次检索被记录两次、工具预算被扣两次（25 次检索记成 41）。
2. 网关忽略 `max_completion_tokens`：上限 4096 的整理调用写出了 7414 个 token；另有一次 `LengthFinishReasonError`。
3. 整理节点单次输出 4–7k token（14–21 条发现，外加每条一份标题与引文副本），是慢模型上最大的时间开销。
4. 时间收尾的硬超时正好等于研究截止点，慢工具场景下三个步骤同时被杀。
另外：规划模型会为“只有内部数据源”的部署安排“核对外部官方文档”的步骤（已在改写与规划提示词里约束）；
知识库查询不计入“搜索次数”、被拒绝的检索反而计入（已修）。

按节点指标示例（`2157aede`）：rewrite 1 次 / 3.3 秒；plan 1 次 / 8.2 秒；research 13 次 P50 1.5 秒；
conversion 3 次 P50 8.8 秒；outline 1 次 / 9.6 秒；section 7 次 P50 5.7 秒；summary 1 次 / 3.6 秒；全部 0 截断、0 重试；
总 30.4 万 token，按测试单价估算 0.32 元。

节点参数确实到达了供应商请求（运行 `8654f210` 的 LLM 调用审计，`params`）：rewrite `temperature 0.2 / max_tokens 1500`、
plan `0 / 3000`、research `0.3`、conversion `0 / 6000`、outline `0.1`、section `0.5 / 6000`，包括走原生子 Agent 执行的节点。

**与 ChatGPT 同题对照（2026-09-19）**：用户授权用其 ChatGPT Pro 账号实测了一次深度研究，界面、动效与时间线的实测数值
（含样式表里的 shimmer 关键帧、各卡片尺寸、阅读器排版、引用悬停卡、来源与活动面板）以及差距清单见
[CHATGPT_BENCHMARK_2026-09-19.md](CHATGPT_BENCHMARK_2026-09-19.md)。同一条请求我方运行 `82225b11`（公网模式、调参后）：
**5 分 39 秒**完成（ChatGPT 9 分 09 秒）、60 条引用（45）、8 表 1 图（9 表 1 图）、来源同样以官方文档为主、0 错误 0 截断 0 格式重试、
190.6 万 token；报告 2.9 万字，约为 ChatGPT 的 5–6 倍（`report_length_scale` 可调）。

### 2026-09-19：提示词缓存的真实验收

同一条请求（四个开源向量数据库选型）、同一模型（`deepseek-v4-flash`）、同样不限预算，8012 网关：

| | 改动前 `8654f210` | 改动后 `244cd6d5` |
| --- | --- | --- |
| 输入 Token / 其中命中缓存 | 226.6 万 / 11.5 万（**5%**） | 342.9 万 / 287.3 万（**84%**） |
| 未命中（按全价计费、需要重新 prefill）的输入 | 215.1 万 | 55.6 万 |
| 检索研究节点 | 62 次，命中 5% | 88 次，命中 **88%**，可复用前缀 87% |
| 章节写作节点 | 命中 6% | 命中 49% |
| 研究节点单次调用耗时 | 3.0 秒（平均上下文 3.3 万） | 2.6 秒（平均上下文 3.6 万） |
| 结果 | COMPLETED | COMPLETED，83 条引用、2.5 万字、0 错误 0 截断 |

两次运行的计划不同（5 步 / 6 步），总时长不能直接比；可比的是命中率与未命中 Token。DeepSeek 官方服务 prefill 本来就快，单次调用只快了约 13%；
自建的慢模型上 prefill 占比更高，收益会更明显（前提是推理服务开了 prefix caching，多副本时配了会话粘性）。
追问路径（同一份报告连续问两个不同的问题）：改动前第二个问题命中 4%（运行 `521ed48d`，`message` 在最前），改动后 **94%**（运行 `244cd6d5`，1.57 万输入里 1.52 万命中）；
第一个问题 2% 是预期的，那是这份报告第一次发给模型。
浏览器（3112）里指标页显示"缓存命中 84%"、检索研究"88% / 87%"，控制台只有网站图标的 404（既有行为）。
第一次验证运行 `431584b1` 失败（`NO_EVIDENCE`）与缓存无关：用接口创建时没带预算，默认 12 万 Token 被 5 个并行步骤平分后，
引擎的预算中间件在第一轮就去掉了工具调用。接口调用方应显式传预算或不限。

### 2026-09-20：引擎并发与章节长度修复的真实验收

同一问题、同一模型、不限预算，8012 网关；用技能的 `audit_run.py --run 8a21a738 --baseline 244cd6d5` 对比（设置差异只有新增的两个空模型字段和压缩提示词）：

| | 修复前 `244cd6d5` | 修复后 `8a21a738` |
| --- | --- | --- |
| 引擎 `subagent_runtime.max_running` | 3（研究配置 6 路并发、8 路写作） | 8（启动器在重启时刷新了已有验收目录的 `host.yaml`） |
| 在引擎里排队 ≥20 秒的角色 | 3 个：203 / 261 / 292 秒 | 0 个 |
| 活跃时长 | 632 秒（研究 564，写作 44） | **239 秒**（研究 207，写作 17） |
| 章节调用 / 其中重做 | 13 / 5（全部因为超长） | 7 / **0** |
| 报告 | 24.8k 字、83 条引用 | 18.7k 字、68 条引用（与更早的基线 18.2k 字一致） |
| 缓存命中 | 84% | 82% |

两次的计划不同（6 步 / 5 步），所以活跃时长的降幅里有一部分来自少一步；可以直接比的是“没有角色再排队”和“重做调用为 0”。
搜索供应商的问题仍在（规则 `provider-failing`、`slow-tools` 照旧命中），这次运行里工具等待仍是研究阶段的大头。

## 5. 本地运行

### 5.1 端口与进程

| 场景 | 端口 | 注意 |
| --- | --- | --- |
| 仓库常规栈 | Nginx `2026`、Gateway `8001`、Frontend `3000` | 以根与模块 `AGENTS.md` 为准 |
| 真实验收 | Gateway `8001`、Frontend `3100` | 页面连接真实后端，不是合成演示 |
| 本机 `3000` | 用户已有的 Docker 服务 | 不要为启动研究前端而停止它 |
| AIO 容器端口 | 动态分配 | 按容器标签与研究线程确认归属，不要硬编码或批量清理 |

接手时先只读确认进程归属，不要直接 `make stop`、杀端口或清理 Docker。

### 5.2 启动真实验收

从仓库根执行（`--resume-dir` 必须是绝对路径；新建隔离目录时去掉该参数）：

```bash
uv run --directory backend --no-sync python -m deepresearch.live \
  --allow-live --model deepseek-v4-flash --jina-no-key \
  --unlimited-budget --max-output-tokens 393216 \
  --resume-dir /Users/sd3/Desktop/project/deer-flow/.deerflow/deepresearch/live-m232rndj \
  --port 8001 --frontend-port 3100
```

前端（从 `frontend/`，默认代理到 `127.0.0.1:8001`）：

```bash
python3 ../scripts/pnpm.py exec next dev --hostname 127.0.0.1 --port 3100
```

- 启动器生成隔离的私有配置，不修改根配置；凭据只在进程环境中。`deepseek-v4-flash` 与 `393216` 是本次使用值，切换模型前核实官方上限。
- `--jina-no-key` 仅让验收进程使用 Jina 公共模式，不修改 `.env`。
- 重启网关会把活动运行标为 `PROCESS_INTERRUPTED`，需要在页面重试；待确认计划的倒计时会暂停。
- 最近的研究页面：`http://127.0.0.1:3100/workspace/deepresearch/9718f578-7857-4636-85d4-e246d4c1dbab`。
- 前端需要 `DEER_FLOW_AUTH_DISABLED=1`，否则重启后会跳到 `/setup`。
- 用 API 创建任务时，`budget` 各项不能超过 `/capabilities` 的 `budget_ceiling`，`max_model_tokens` 最大 2,000,000；
  省略 `budget` 会使用默认的 12 万 Token，而单次调用预留 393,216，会直接 `BUDGET_EXHAUSTED`。不限 Token 时传 `null`。
- `--resume-dir` 要求生成的研究配置与数据目录中的 `research.yaml` 一致（`pricing` 除外）；示例配置里新增的、等于默认值的字段不算变更，其余差异会拒绝恢复（换一个新的验收目录）。
- 要在验收环境显示费用，把真实单价写进数据目录 `research.yaml` 的 `pricing`（键为模型名），重启网关即可，已有任务不受影响。

### 5.3 诊断

只读接口：`GET /api/deepresearch/{id}`、`/activity`、`/sources`、`/trace`、`/trace/export`、`/report?format=json|md`、
`/metrics`、`/metrics/export`。多次研究对比（从 `backend/`）：
`uv run --no-sync python -m deepresearch.metrics --data-dir ../.deerflow/deepresearch/live-m232rndj/research --format table`。
`/events` 是 SSE，诊断时加超时，不要无界等待。`/api/subagents` 是 Agent 配置目录，不是后台执行注册表。
研究数据库为 `<数据目录>/research/research.sqlite3`，事件在 `research_event.body`（JSON）中；只读打开即可。

## 6. 测试

后端（`backend/`）：

```bash
uv run --no-sync python -m pytest ../tests/deepresearch \
  tests/test_subagent_executor.py tests/test_jina_client.py \
  tests/test_web_fetch_paging.py tests/test_remote_list_dir.py \
  tests/test_aio_sandbox.py -q
uv run --no-sync ruff check deepresearch ../tests/deepresearch
uv run --no-sync ruff format --check deepresearch ../tests/deepresearch
PYTHONPATH=. uv run --no-sync pytest -m "not live" --ignore=tests/blocking_io tests/ -q   # 等价于 make test
```

前端（`frontend/`）：

```bash
python3 ../scripts/pnpm.py rstest run deepresearch message-list
python3 ../scripts/pnpm.py test
python3 ../scripts/pnpm.py check
```

浏览器端到端（配置默认前端端口 3000；本机 3000 被占用时改用其他端口，并先自行启动演示后端与前端）：

```bash
# 仓库根：演示后端（合成数据，端口必须是 8022）
cd backend && DEEPRESEARCH_DEMO_DATA_DIR=.deerflow/deepresearch/e2e DEEPRESEARCH_DEMO_FRONTEND_PORT=3200 \
  DEEPRESEARCH_DEMO_PLAN_COUNTDOWN=5 DEEPRESEARCH_DEMO_STEP_DELAY=0.2 \
  uv run --no-sync python -m uvicorn deepresearch.demo:app --host 127.0.0.1 --port 8022
# frontend/：测试用前端（同一目录不能同时运行两个 next dev）
SKIP_ENV_VALIDATION=1 DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_ENV=development \
  NEXT_PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:8022 \
  python3 ../scripts/pnpm.py exec next dev --hostname 127.0.0.1 --port 3200
# frontend/：运行测试
DEEPRESEARCH_E2E_FRONTEND_PORT=3200 DEEPRESEARCH_E2E_REUSE_BACKEND=1 DEEPRESEARCH_E2E_REUSE_FRONTEND=1 \
  python3 ../scripts/pnpm.py exec playwright test -c playwright.deepresearch.config.ts
```

观测与离线交接提交前的结果：

| 检查 | 结果 |
| --- | --- |
| `tests/deepresearch` | 153 项通过（含指标 8 项、doctor 3 项、离线模板 3 项、启动器配置 5 项） |
| DeepResearch 与原生模块集合，加 `tests/test_agent_guidance_check.py` | 420 项通过、1 项失败（`subagents/AGENTS.md` 软上限，宿主已有问题，见下表；该文件与 HEAD 相同） |
| ruff check / format（DeepResearch） | 通过 |
| 前端 DeepResearch 单测（`rstest run deepresearch`） | 38 项通过 |
| `pnpm check`（全前端 ESLint + tsc）与 DeepResearch 文件 Prettier | 通过 |
| 浏览器端到端 `playwright.deepresearch.config.ts` | 5 项通过；第一项现在还检查“指标”页签的工具调用数与 JSONL 导出 |
| 离线模板冒烟 | 用离线模板合并出的配置正常启动网关（非验收启动器），`/capabilities` 为 `deerflow`、`ready`；模型地址不可达时 doctor 探测失败、研究在规划阶段报 `NATIVE_AGENT_FAILED`，Trace 中模型节点为 `APIConnectionError` |
| 模型探测实测 | 对 `deepseek-v4-flash` 运行 `--probe-model`：`ok: true`，普通回复 0.4 秒、工具调用 0.5 秒，返回用量 |

本轮没有重跑后端默认离线全量与前端全量单测；改动只涉及 `backend/deepresearch`、`tests/deepresearch`、前端 DeepResearch 文件与文档。

上次推送前的结果：

| 检查 | 结果 |
| --- | --- |
| DeepResearch 与原生模块集合 | 394 项通过 |
| ruff check / format（DeepResearch） | 通过 |
| 后端默认离线全量（`make test` 等价） | 14560 项通过、7 项失败，逐项说明见下 |
| 前端 DeepResearch 相关单测 | 39 项通过 |
| 前端全量单测 `pnpm test` | 1325 项通过 |
| `pnpm check`（全前端 ESLint + tsc） | 通过 |
| 浏览器端到端 `playwright.deepresearch.config.ts` | 5 项通过；重复两轮 10 项通过；另用只安装 `requirements.txt` 的干净环境启动演示后端再次 5 项通过 |

后端全量的 7 项失败：

| 测试 | 原因 | 处理 |
| --- | --- | --- |
| `test_ci_uv_version_pin.py` | `deepresearch.yml` 的 `setup-uv` 未固定版本（09-14 提交引入） | 已修复，重跑通过 |
| `test_agent_guidance_check.py::…scoped_guidance_shape` | `backend/deepresearch/AGENTS.md` 不在允许清单 | 已登记，重跑通过 |
| `test_agent_guidance_check.py::…soft_budgets…` | `harness/deerflow/subagents/AGENTS.md` 41,088 字节超过 40,960 软上限；该文件与 HEAD 相同 | 未修改，属于宿主已有问题；`scripts/check_agent_guidance.py` 同样报 middlewares 与 subagents 的指导链超限 |
| `test_aio_sandbox_local_backend.py::test_start_container_hardens_docker_run_by_default` | `cap_add` 多出重复的 `FOWNER`，来自用户未提交的 `local_backend.py` 修改 | 未修改，也不提交该文件 |
| `test_sandbox_orphan_reconciliation_e2e.py` 三项 | 全量运行时与验收网关的 AIO 容器同时存在，Docker 端到端测试受干扰 | 单独重跑全部通过 |

未运行：`make test-blocking-io`、`make test-live`、前端生产构建。远端 CI 结果见 GitHub Actions 的 “DeepResearch full-stack checks”。单元测试使用伪造提供方，只证明适配与生命周期行为，
不能代替真实研究与浏览器验收。

### 2026-09-22 回归

从 `backend/`（改动只涉及 `backend/deepresearch`、`tests/deepresearch`、MCP 桩与文档；前端没有改动，未重跑前端）：

```sh
uv run --no-sync python -m pytest ../tests/deepresearch -q   # 334 passed（最近新增 test_word_export.py 25 项：Word 的引用链接、参考条目、Mermaid 出图与降级、图片字节校验与尺寸；此前：日期进证据与参考文献、九种真实写法的解析、读页日期、证据目录仍是 JSON）
uv run --no-sync python -m pytest tests/test_agent_guidance_check.py -q   # 11 passed、1 failed：仍是 subagents/AGENTS.md 41,088 > 40,960 的宿主已有问题
uv run --no-sync ruff check deepresearch ../tests/deepresearch && uv run --no-sync ruff format --check deepresearch ../tests/deepresearch
python ../scripts/check_agent_guidance.py   # deepresearch 指导链回到软上限内（新增日期规则后压缩了若干条目）
```

真实验收见第 3.11、3.12 节：两次仅 MCP 检索 + 读取的完整研究——12 条引用全部带日期；19 条来源行 13 条带站点图标。

### 2026-09-21 回归

从 `backend/`（改动只涉及 `backend/deepresearch`、`tests/deepresearch`、技能脚本、MCP 桩与文档；前端没有改动，未重跑前端）：

```sh
uv run --no-sync python -m pytest ../tests/deepresearch -q        # 281 passed（新增 test_mcp_passthrough.py 12 项、test_workflow.py 3 项、test_runner.py 1 项、test_audit_skill.py 3 项；全量连跑 8 次无偶发失败）
uv run --no-sync python -m pytest ../tests/deepresearch tests/test_subagent_executor.py tests/test_summarization_middleware.py \
  tests/test_context_compaction.py tests/test_web_fetch_paging.py -q   # 494 passed
uv run --no-sync python -m pytest tests/test_agent_guidance_check.py -q   # 11 passed、1 failed：仍是 subagents/AGENTS.md 41,088 > 40,960 的宿主已有问题
uv run --no-sync ruff check deepresearch ../tests/deepresearch ../.agents/skills/deepresearch-engineering/scripts
uv run --no-sync ruff format --check deepresearch ../tests/deepresearch ../.agents/skills/deepresearch-engineering/scripts ../examples/deepresearch/mcp-stub
```

真实运行（MCP 桩 + `deepseek-v4-flash`，不限预算）：`956c1ae2`（旧代码对照）、`1d8426f7`、`489c56b1`、`062ec4f8`、`de4e545c`，结论见第 3.7、3.8 节。
旧代码对照是在临时 `git worktree`（HEAD）里起的网关，用完已删除。审计页面在浏览器里看过浅色、深色与 390px 宽度；这几份研究报告本身只核对了接口返回，没有在浏览器里看。

### 2026-09-19 回归

从 `backend/`：

```sh
uv run --no-sync --with python-docx python -m pytest ../tests/deepresearch -q                  # 262 passed（技能与自测修复后；提示词缓存两轮后 256，此前 250）（新增 test_tuning.py、test_review_fixes.py，offline 模板新增 1 项）
uv run --no-sync --with python-docx python -m pytest ../tests/deepresearch tests/test_subagent_executor.py \
  tests/test_summarization_middleware.py tests/test_context_compaction.py tests/test_web_fetch_paging.py -q   # 全部通过
uv run --no-sync python -m pytest tests/test_jina_client.py -q                                 # 44 passed（与 -p no:logging 同跑时 7 个用例因缺少 caplog 报 error，与本轮无关）
uv run --no-sync ruff check deepresearch ../tests/deepresearch && uv run --no-sync ruff format --check deepresearch ../tests/deepresearch
```

从 `frontend/`：

```sh
python3 ../scripts/pnpm.py rstest run deepresearch   # 168 passed（提示词缓存一轮后；此前 167，再之前 73）
python3 ../scripts/pnpm.py test                      # 1456 passed
python3 ../scripts/pnpm.py check                     # eslint + tsc，通过
```

浏览器端到端 `playwright.deepresearch.config.ts`：5 passed，连跑两轮，加 `last_event_seq` 后再跑一轮仍 5 passed。3100 正被使用，所以是在一份前端副本上跑的
（演示后端 8022 + `next dev --port 3200`，按第 6 节“浏览器端到端”的 REUSE 方式）。两点经验：副本不在 git 仓库里，Playwright 往项目目录写 `test-results/` 会让 Tailwind
不停重新扫描、`app/layout` 热更新循环，工作区页刷新后一直加载不出来，加 `--output <项目外目录> --reporter=list` 后消失（真实仓库的根 `.gitignore` 已忽略这些目录）；
用例里“追问后 `usage.tool_calls` 仍为 4”的断言随“预算按任务计”改为 `usage.tool_calls == 0` 且 `usage_history.at(-1).tool_calls == 4`。

真实核对“停止追问后继续对话”（验收网关 8012 + 前端副本 3112，任务 521ed48d）：对已完成报告发追问后立即停止 → `CANCELLED`、报告仍在；
再次 `POST /messages` 返回 202 并在约 3 秒内得到回答、状态回到 `COMPLETED`、用量按新任务重置；浏览器里该会话的输入框可用、
停止提示为“研究已停止。已生成的报告仍可阅读和导出，也可以继续就这份报告提问或要求修改。”，通过界面发送后回答出现、提示消失，控制台无错误。
快照 `last_event_seq` 实测为 1515（该任务的事件总数），刷新后事件流从 `after=12/13` 接续而不是 `after=0`（演示后端日志）。

没有运行：后端默认离线全量、`make test-blocking-io`、`make test-live`、前端生产构建。本轮的人工浏览器检查同样在前端副本上做：`next dev --port 3112`，`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` 指向
运行新后端代码的验收网关 8012；检查了设置页（节点调参、保存往返）、提交后的等待态、计划卡与倒计时、研究中的进度卡、
完成态报告卡、全屏阅读器、点击引用后的来源面板。宿主仓库原有的 AGENTS 指导链超限（`subagents/AGENTS.md`、`middlewares` 链）仍在，与本轮无关。

已知遗留：计划卡/等待占位下方仍会出现共享消息列表的“复制”按钮；等待占位依赖共享消息列表“有内容才渲染”的规则
（占位消息必须带非空文本，`research-conversation.tsx` 有注释），这一点只有浏览器检查、没有列表级单测；
旧报告的引用摘录仍以页面导航开头（新报告已改为从正文第一句开始）。

### 2026-09-18 回归

从 `backend/`：

```sh
uv run --no-sync --with python-docx python -m pytest ../tests/deepresearch tests/test_subagent_executor.py tests/test_summarization_middleware.py tests/test_context_compaction.py -q   # 387 passed
uv run --no-sync python -m pytest tests -q -k "summar or compact or subagent or middleware"                                                                                        # 2661 passed, 10 skipped
uv run ruff check deepresearch packages/harness/deerflow/agents/middlewares/summarization_middleware.py && uv run ruff format --check deepresearch
DEEPRESEARCH_E2E_FRONTEND_PORT=3101 uv run --no-sync --with python-docx python ../scripts/pnpm.py exec playwright test -c playwright.deepresearch.config.ts                          # 5 passed
```

从 `frontend/`：

```sh
python3 ../scripts/pnpm.py check   # eslint + tsc，通过
python3 ../scripts/pnpm.py test    # 1363 passed（新增 32 条：llm-calls / settings helpers、设置页与审计面板 DOM）
```

注意：`tests/test_tool_error_handling_middleware.py::test_build_subagent_runtime_middlewares_threads_app_config_to_llm_middleware`
单独运行时会因中间件顺序断言失败，用 HEAD 的引擎文件复现同样失败，属于既有问题，与本轮改动无关；整批运行时通过。
浏览器端到端需要独占前端开发服务器（Next 16 不允许同目录第二个 dev server），跑之前先停掉本地的 3100。

## 7. 残余风险与未验证项

- **产品指标看不到引擎准入排队。** `time.queue_seconds` 只量研究侧的信号量；角色在引擎里排队的时间计入了步骤耗时与 `agents` 的秒数，
  `agents.max_parallel` 也是名义值。审计脚本能算出来（`engine-queue`），指标页还不能：可以在 `metrics.summarize` 里按“子 Agent 开始 → 第一次模型调用”补一个字段。
- **本机的搜索供应商状态差**（不是代码问题，但会让任何耗时结论失真）：Tavily 配额用尽、Serper 鉴权/网络失败，搜索全靠 DuckDuckGo，
  失败率约 26%、每次失败 35 秒，研究员运行时间的 84% 在等工具。做耗时对比前先修好凭据，或在供应商上设 `timeout_seconds`。
- 自测里两份独立报告都指出、但这次没有动的两点：失败的搜索也计入每步的 `max_searches_per_unit`；被预算拒绝的“读取”可能被算进 `reads` / `pages_read`
  （读代码得出，未验证；今天拒绝读取很少见，加了按步骤的页面上限之后才会放大）。
- 技能自测只在一个很强的模型上做过，断言两组全过，区分不出质量差异；技能对较弱模型（目标环境的自建模型）的帮助没有验证。
- 只在本机用 DeepSeek 验收；目标环境的 MCP、其他模型、SSO 与多用户权限未验收。设置页的“测试模型 / 测试供应商 / 列出 MCP 工具”
  只在 DeepSeek、Tavily、Serper、Jina、DuckDuckGo、direct 上真实跑过，其余预设（brave、exa、bocha、searxng、firecrawl、
  tavily_extract、ragflow、lightrag、http 模板、mcp 供应商）只有单元测试与假服务覆盖。
- 研究员进度说明的语言在提示词加强后于一次验收中做到 132/132 全中文；其他语言、其他模型未验证。
- 上下文压缩的新默认值（阈值 0.6、保留 0.4）只做了一次对照跑（128K 上下文下根本没有触发）；
  小上下文模型（例如 32K 的本地模型）真正频繁压缩时的摘要质量与研究结论完整性没有验证。
- 报告事实没有逐条人工核验；引用校验只保证标记指向本次读取的页面，不保证语义支持。
- 部分引用来自二手站点（律所文章、第三方编纂站点），报告局限中已披露；“一手来源优先”依赖模型遵循。
- 研究耗时约 14–16 分钟，约为 ChatGPT 的 2 倍；并发度、补研轮次和单元数量还可调优。
- 网站图标由 Gateway 访问被引用网站，遵循与原生抓取相同的公网地址校验，DNS 重绑定限制同样存在；无外网部署应设 `favicons: false`。
- 前端直连 Next 开发服务之外的反向代理也需尊重 `no-transform`；仓库 Nginx 本就不压缩 SSE。
- 单 worker SQLite；外部工具至多 at-least-once；两个数据库的备份、保留与日志轮转需要生产方案。
- 新阅读器、来源与活动面板未在真实手机或 390 px 视口验证；深色主题需要再检查一次。
- 宿主仓库已有的 AGENTS 指导链超限问题会让 `lint-check` 的指导检查失败，与本功能无关但会出现在 CI 中。
- 费用只是按 `pricing` 的估算，不是账单；验收配置没有单价，费用未验证过真实金额，只有单元测试覆盖计算。
- 工具错误类型对“返回错误结果”的情况是按内容粗分（状态码、异常名、空内容），不同工具的错误文本格式不同时可能归入 `ToolReturnedError`。
- 重复调用只识别参数完全相同的请求；同一页面换 `max_length` 或查询词不算重复，真实的重复浪费可能更高。
- 提供方不上报用量时，调用只记预留估算且不计入 Token 与费用；DeepSeek 两次验收中未上报调用为 0，其他模型未验证。
- 离线环境加本地模型、内网知识库的完整研究没有验收过；离线模板只验证了配置合法、网关能启动、模型不可达时的报错。
  较弱的本地模型在计划、数据整理、引用标记上的失败率未知，调参建议来自代码与联网验收经验，不是离线实测结论。

- 2026-09-19 新增的能力里，只有这些经过真实运行：节点调参（温度、`max_tokens`）、仅 MCP / 无原文研究、`allowed_tools`、
  请求头插值与保存的 Cookie、`kind: mcp` 与 `type: mcp` 两种接法、时间收尾（工具以 `scope: time` 停止）、
  Chat Completions 客户端（`max_tokens`、流式用量）、经设置接口保存节点开关与 `report_length_scale` 后新建的研究按快照执行
  （关闭 `summary` 后没有摘要调用；0.45 触发了两次章节缩短修复）、设置接口拒绝 stdio MCP / 明文凭据 / 角色文件路径（422）。
  `json_mode` 只在一次运行里开过（DeepSeek 兼容端点，出现过一次 `LengthFinishReasonError` 后回退到普通请求）；
  `nodes.*.model` 指向不同模型、关闭 `rewrite`、`max_seconds_per_unit`、`request_secret_headers` 经真实 HTTP 请求头传入、
  `RESEARCH_TIME_SPENT` 在真实运行里降级单元，只有单元测试。
- 真实网关没有验证过：流式用量与 `max_tokens` 参数名的自动判断基于 LangChain 的行为和 DeepSeek 的 OpenAI 兼容端点；
  网关拒绝 `stream_options` 时要手动写 `stream_usage: false`。真正的慢模型（每秒十几个 token）下的超时与时间预留取值没有实测依据。
- 时间预算的整体硬超时是“上限 + 一份报告预留”，单元硬超时可以借用一半报告预留：总时长可能略超 `max_elapsed_seconds`，这是有意的取舍。
- 预算按任务计之后，`usage` 只反映当前任务；需要整个会话的累计用量时看 `usage_history` 或指标接口。
- 设置页写入口的边界（`profile.guard`）会拒绝已经保存的、含 stdio MCP 或明文凭据的旧覆盖层：启动时回退到配置文件并在设置页显示错误，需要管理员重新保存。
- 读者语言只有中文与英文两套报告标签：日文、韩文提问会告诉研究员用对应语言写，但报告的固定标题（执行摘要、研究范围与局限、参考资料）是英文。
- `kind: mcp` 直挂数据源的引用仍是“整次调用一条证据”，标题为“数据源名: 查询词”；要逐条引用需用 `type: mcp` 供应商。
- 前端的 ChatGPT 对标改造依据的是截图量取值（ChatGPT 的卡片渲染在跨域 iframe 里，读不到计算样式），圆环缓动、spinner 转速等动效参数是近似值。

- **直接暴露的 MCP 工具（第 3.7 节）**：弱模型上的 `NO_EVIDENCE` 是由单元测试复现的，真实运行复现的是同一根因在强模型上的表现（按调用顺序猜、归属错误）；
  没有在用户的内网 MCP 上验证。识别靠形状：一次打开多个 URL 而返回是纯文本时识别不出逐页内容，整段返回仍可引用但没有链接；读取工具的地址参数若既不是 URL 也不在
  常见 ID 参数名里，取第一个字符串参数当标识。`role` 填错（把能打开原文的工具填成 `search`）时，读到的内容只是“发现”，这一点程序不替用户判断。
  “记录覆盖答案 ≥80% 则只引用记录”加在最后一次真实运行之后，只有单元测试。
- **规划提示词默认值改了一句**（第 7 条）。旧运行的审计里 `prompts_differing_from_current_defaults` 会出现 `plan`，不代表用户改过提示词。
- **依赖中的步骤在页面上没有“等待某步骤”的状态**，只显示为未开始；接续调度后等待变短，但被依赖的步骤很慢时用户仍看不出它在等谁。

## 8. 建议下一步

0a. 在内网用真实 MCP 复验第 3.7 节：数据源的 `role` 如实填写后跑一次研究，用技能的 `audit_run.py` 看有没有 `findings-pruned`，
   打开 conversion 调用确认 `observed_calls` 里读取的页面带 `url` 与 `title`；遇到识别不了的返回形状，把脱敏后的样例加进 `test_mcp_passthrough.py` 再改 `extract.py`。
0. 用真实的模型网关与内部 MCP 跑一次完整研究：先 `python -m deepresearch.doctor --probe-model <模型> --probe-mcp`
   （看 `json_contract`、`usage`、输出速度与 MCP 工具清单），再按“指标 → 按节点”里的被截断、格式重试与 P95 延迟调 `nodes`；
   很慢时依次尝试：`supplement_gap_codes: [coverage, unsupported]`、`plan_max_units` 3–4、`report_length_scale` 0.5、关闭 `nodes.summary` / `nodes.rewrite`。
1. 人工评审 82490499、68ff862b 与 52e358b9 的报告事实与结构，形成报告质量验收标准（一手来源比例、状态与日期准确性、事实/推断区分）。
2. 用真实的企业 MCP 服务验证 `mcp` 供应商与 `kind: mcp` 数据源：工具发现缓存、按请求凭据（已实现并有单元测试，未接真实服务验收）、异构返回的解析结果。
2. 跑远端 CI、干净克隆部署与前端生产构建。
3. 用目标环境的实际 MCP 与普通 Chat Completions 模型验证异构返回、身份、工具授权、跨用户读取与导出。
4. 在真实手机与深色主题下复验阅读器、来源抽屉、引用悬浮卡。
4a. 在目标环境确认推理服务开了 prefix caching（vLLM `--enable-prefix-caching`、SGLang RadixAttention 默认开），网关多副本时给模型配
   `session_param` 或 `session_header`；判断依据是指标里"可复用前缀"高而"缓存命中"低。网关不上报缓存 Token 时只能看单次调用延迟是否下降。
5. 调优耗时：研究并发、补研停止条件、单元数量；考虑发布报告时预热引用域名的网站图标。
6. 生产运维：多 worker 或外部存储方案、备份与保留策略、故障注入（网络中断、429、5xx、慢工具）。
7. 按指标调优：给 Jina 配 key 或加退避，降低 `HTTP 429` / `ConnectError`；评估提高研究并发（排队累计过长）；
   考虑跨 Agent 共享抓取缓存；在研究配置中填写真实模型单价，再用命令行对比多次研究的每条引用成本。
8. 离线验收：按 [OFFLINE_AGENT_GUIDE.md](OFFLINE_AGENT_GUIDE.md) 接上真实本地模型和内网知识库，完整跑一次研究，
   记录失败点并据此调整离线模板与模型配置建议。

## 9. 容易踩坑的地方

- 不要把本地 `feat/deepresearch` 推到同名远端分支；目标是 `feat/deepresearch-v1-fullstack-20260914`。
- 不要提交 `.deerflow`、`.env`、真实模型或 MCP 配置、凭据、运行数据库，也不要提交 `local_backend.py` 的用户修改。
- 不要为了显示成功而跳过终检、删除难处理的错误或伪造 citation ID；不要把发现链接、读取原文、报告引用都显示成“已核实”。
- 不要在浏览器里启动研究或重新开始倒计时；不要让旧 GET 或旧会话响应覆盖新状态。
- 普通重试不要发送 `allow_limited_report: false`，那会记录为 owner 拒绝。
- 不要把取消 await 当作停止原生线程或远端工具，必须保留 drain 语义。
- 不要把模型单次输出上限与研究累计预算混淆，也不要覆盖运营方禁用的原生预算策略。
- 修改写作提示词时，保持“引用标记必须保留”的明确措辞；“不要提 markers”这类歧义写法会诱导模型不写引用。
- 网站图标路由不要返回 SVG 或按 Content-Type 判断类型；也不要把第三方 favicon 服务 URL 直接放进前端。
- SSE 响应头必须保留 `no-transform`，否则压缩代理会缓冲进度事件。
- 新增会让研究“直接开始”的路径时，确认运行快照（`units`、状态）在不经过中断处理时也被写入。
- 全量后端测试时如果验收网关与 AIO 容器仍在运行，Docker 端到端测试可能被干扰；判断失败前单独重跑。
- 演示后端在 CI 中只安装 `backend/deepresearch/requirements.txt`；报告或演示路径新增第三方依赖时同步更新该清单，本地 uv 环境不会暴露缺失。
- `/deepresearch-demo` 这类不经过工作区布局的页面必须提供限高父容器，否则研究页面的可调整面板会塌缩。
- 目标环境已有 DeerFlow 时，适配原生宿主接口与完整 feature 差异，不要覆盖对方的业务代码、配置、Skills 或认证体系。
- 不要在运行中的角色对话里"往前面插消息"或改写已有消息（包括打开 `tool_receipt_ledger`、在系统提示词里放日期/预算/计数）：
  供应商只复用从第一个 token 起相同的前缀，开头变一个字后面全部重算。改了请求构造后看指标里的"可复用前缀"，研究节点应在 80% 以上。
- 任务载荷里新增字段时按"所有调用相同 → 本次运行相同 → 只有本次调用才有 → 每次都变的计数"的顺序放，不要随手加在最前面。
- 不要把研究步骤改回“按波次”调度（对一批就绪步骤 `gather`）：带依赖的步骤会等整波里最慢的那个，空着的并发位白白闲置。现在是完成一个调度一个，
  失败判断按整次运行做；提前退出时必须取消并等待在跑的步骤（drain），不要只 `cancel()` 不 `await`。
- 直接暴露的工具（`kind: mcp`、宿主工具）**模型看到的 schema 与返回一律不改**；证据在执行后从调用参数和返回里识别（`sources.opened_pages`、`extract.records`）。
  页面地址只取自调用自己的参数，不取自模型的笔记，也不猜。新增返回形状时改 `extract.py` 并加测试，不要在 `observations.py` 里写某个服务的字段名。
- 写并发相关的测试不要断言“谁先结束”：步骤在到达 `runner.research` 之前有几次存储 await，顺序不确定。用事件让慢步骤等到“该发生的事发生了”再结束，断言它看到的状态。
- 指标中的 `null` 表示没有测量，不要当成 0 汇总；新增指标时，历史数据缺字段也要返回 `null`。
- `request_key`、`error_type` 只用于统计分组，不要据此改变研究流程；`request_key` 是脱敏后参数的哈希，不要改成保存参数原文。
- 指标写入必须吞掉自身异常，只记日志；不要让观测代码的故障让研究失败。
- 不要把真实单价写进 `deepresearch.example.yaml`（那里只保留注释示例）；单价属于部署方私有配置。验收启动器恢复时只忽略 `pricing` 的差异，
  其余字段不一致仍会拒绝恢复。

## 10. 文档索引

- 接手、调试、测试与**运行审计**的技能：[`.agents/skills/deepresearch-engineering/`](../../.agents/skills/deepresearch-engineering/SKILL.md)。
  `scripts/audit_run.py --run <id|URL|latest> [--baseline <run>]` 从研究数据库生成审计底稿和一张离线 HTML 页面（时间线、各阶段的时间 / Token / 工具与搜索、缓存、补研轮的成本与产出、规则发现与对应设置），
  `scripts/show_call.py` 打开或重放一次模型调用；审计报告模板与读数陷阱在 `references/audit-report.md`。

| 文档 | 状态与用途 |
| --- | --- |
| [OFFLINE_AGENT_GUIDE.md](OFFLINE_AGENT_GUIDE.md) | 不联网的本地 Agent 开发入口（规则、命令、排错） |
| [MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md) / [DEERFLOW_CONFIGURATION.md](DEERFLOW_CONFIGURATION.md) / [RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md) | 模型、宿主、研究配置说明 |
| [LOCAL_AGENT_HANDOFF.md](LOCAL_AGENT_HANDOFF.md) | 本地 / 内网 Agent 的入口页，指向上面的文档 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 当前架构与工作流（权威） |
| [HANDOFF.md](HANDOFF.md) | 当前交接状态（本文） |
| [API.md](API.md) / [openapi.json](openapi.json) | 当前接口契约 |
| [CHATGPT_BENCHMARK_2026-09-16.md](CHATGPT_BENCHMARK_2026-09-16.md) | ChatGPT 实测观察（流程）、差距与改造前后对照 |
| [CHATGPT_BENCHMARK_2026-09-19.md](CHATGPT_BENCHMARK_2026-09-19.md) | ChatGPT 界面与动效的实测数值、逐项差距清单、同题耗时/引用/报告对照 |
| `README.deepresearch.md`（仓库根） | 安装、配置、使用 |
| [NATIVE_RUNTIME.md](NATIVE_RUNTIME.md) / [REUSE_AUDIT.md](REUSE_AUDIT.md) | 原生复用专题，仍然有效 |
| [EXTENDING.md](EXTENDING.md) | 新增研究角度与来源 |
| [MCP_RESPONSE_FORMAT.md](MCP_RESPONSE_FORMAT.md) | 自建 MCP 该返回什么形状：最小必要格式、会静默丢内容的四条规则、配套的数据源声明 |
| [STABILITY_AUDIT_2026-09-16.md](STABILITY_AUDIT_2026-09-16.md) | 2026-09-16 稳定性修复与恢复验收 |
| [DESIGN_BASELINE.md](DESIGN_BASELINE.md) / [RUNTIME.md](RUNTIME.md) / [VERIFICATION.md](VERIFICATION.md) / [COMPUTER_USE_2026-09-15.md](COMPUTER_USE_2026-09-15.md) / [COMPUTER_USE_2026-09-16.md](COMPUTER_USE_2026-09-16.md) | 历史记录；早期设计与“未完成”结论不能覆盖后来的实现与验收 |
