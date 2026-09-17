# DeepResearch 交接文档

更新时间：2026-09-17。本文面向继续开发本项目的工程师或 Agent，记录当前状态、验收证据、运行方式、
风险和下一步。架构与工作流细节见 [ARCHITECTURE.md](ARCHITECTURE.md)，接口契约见 [API.md](API.md)。
本文不是产品宣传，也不是全量安全认证。

## 1. 先看这里

| 项目 | 当前状态 |
| --- | --- |
| 仓库 | `https://github.com/wyzz973/deer-flow` |
| 本机目录 | `/Users/sd3/Desktop/project/deer-flow` |
| 本地分支 | `feat/deepresearch` |
| 远端分支 | `origin/feat/deepresearch-v1-fullstack-20260914`（不是同名远端分支） |
| 已推送提交 | `eb3ce656` 对标 ChatGPT 深度研究的完整改造与本文、[ARCHITECTURE.md](ARCHITECTURE.md)；其后的提交修复浏览器端到端测试与 CI 依赖。以 `git log` 为准 |
| 更早的基线 | `5fa0299c` `feat(deepresearch): unify native research chat and harden workflow recovery` |
| 功能状态 | 交互、工作流、报告与前端改造完成；三次真实 DeepSeek 研究端到端完成 |
| 本次回归 | 见第 6 节 |
| 未验证 | 干净克隆部署、生产构建、公司 MCP 与 SSO、真实手机视口、报告事实逐条核验；远端 CI 以 GitHub Actions 结果为准 |

**务必保留用户原有的本地修改：**
`backend/packages/harness/deerflow/community/aio_sandbox/local_backend.py`。
它在接手前就存在，没有纳入任何 feature 提交。不要提交、覆盖、重置或清理；跨环境迁移时单独评估它对 sandbox 启动的影响。

阅读顺序：仓库根 `AGENTS.md` → `backend/AGENTS.md` → `backend/deepresearch/AGENTS.md` → `frontend/AGENTS.md`
→ [ARCHITECTURE.md](ARCHITECTURE.md) → 本文。不要修改 `CLAUDE.md`，它只是导入 `AGENTS.md` 的薄入口。

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

## 3. 本轮完成的工作

按层概括，细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

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

**仓库卫生与 CI**
- `.github/workflows/deepresearch.yml` 的 `setup-uv` 固定为生产使用的 uv `0.11.1`（`test_ci_uv_version_pin.py`）。
- `backend/deepresearch/AGENTS.md` 登记到 `test_agent_guidance_check.py` 的允许清单。
- 浏览器端到端测试（`frontend/tests/deepresearch/workbench.spec.ts`）按新交互改写：编辑状态、对话修改直接开始、
  全屏阅读、引用悬浮原文片段、来源与活动页签、Trace、移动端抽屉与侧栏历史。
- `/deepresearch-demo` 页面补上与工作区相同的限高框架（`SidebarProvider h-screen` + `SidebarInset`）。此前面板组只有约 104 px 高，
  输入框在视口外，这是 09-16 起 CI 端到端测试失败的原因。
- `backend/deepresearch/requirements.txt` 补充 `markdown-it-py`。CI 的演示后端只安装该清单，缺少它时报告渲染失败（已在同等环境复现）。
- 网站图标的首字母徽标与图片标为装饰性（`aria-hidden`），不改变域名标题的可读名称。

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

浏览器检查（1568×691 视口）：计划卡、编辑引用条、确认消息、进度卡与“更新”、统计行、报告卡、阅读器悬停目录、
来源页签（网站图标、摘要）、活动页签（网站标签、跟随）、引用悬浮卡（原文片段）。窄屏只在 Chrome 最小窗口宽度 606 px
检查过（阅读器与来源抽屉无横向滚动），未在 390 px 或真实手机上验证。

历史验收：2026-09-16 的 `fa984b8d-91bd-4dd6-8609-6e88d4660a86`（数据目录 `live-3j8hr_hv`）暴露了线程 ID 超长、AIO `ls`
阻塞等问题，修复后从检查点恢复完成，过程与证据见 [STABILITY_AUDIT_2026-09-16.md](STABILITY_AUDIT_2026-09-16.md)。
同一目录的 `ed5d5da5-f155-4a60-bf1f-00d69b822764` 是 SQLite/PostgreSQL 选型的旧格式报告样本。

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
- 最近的研究页面：`http://127.0.0.1:3100/workspace/deepresearch/68ff862b-20dd-45a8-b7e9-90a254e3d889`。

### 5.3 诊断

只读接口：`GET /api/deepresearch/{id}`、`/activity`、`/sources`、`/trace`、`/trace/export`、`/report?format=json|md`。
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

本次推送前的结果：

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

## 7. 残余风险与未验证项

- 只在本机用 DeepSeek 与无 key 的 Jina 抓取验收；公司 MCP、其他模型、SSO 与多用户权限未验收。
- 报告事实没有逐条人工核验；引用校验只保证标记指向本次读取的页面，不保证语义支持。
- 部分引用来自二手站点（律所文章、第三方编纂站点），报告局限中已披露；“一手来源优先”依赖模型遵循。
- 研究耗时约 14–16 分钟，约为 ChatGPT 的 2 倍；并发度、补研轮次和单元数量还可调优。
- 网站图标由 Gateway 访问被引用网站，遵循与原生抓取相同的公网地址校验，DNS 重绑定限制同样存在；无外网部署应设 `favicons: false`。
- 前端直连 Next 开发服务之外的反向代理也需尊重 `no-transform`；仓库 Nginx 本就不压缩 SSE。
- 单 worker SQLite；外部工具至多 at-least-once；两个数据库的备份、保留与日志轮转需要生产方案。
- 新阅读器、来源与活动面板未在真实手机或 390 px 视口验证；深色主题需要再检查一次。
- 宿主仓库已有的 AGENTS 指导链超限问题会让 `lint-check` 的指导检查失败，与本功能无关但会出现在 CI 中。

## 8. 建议下一步

1. 人工评审 82490499 与 68ff862b 的报告事实与结构，形成报告质量验收标准（一手来源比例、状态与日期准确性、事实/推断区分）。
2. 跑远端 CI、干净克隆部署与前端生产构建。
3. 用公司实际 MCP 与普通 Chat Completions 模型验证异构返回、身份、工具授权、跨用户读取与导出。
4. 在真实手机与深色主题下复验阅读器、来源抽屉、引用悬浮卡。
5. 调优耗时：研究并发、补研停止条件、单元数量；考虑发布报告时预热引用域名的网站图标。
6. 生产运维：多 worker 或外部存储方案、备份与保留策略、故障注入（网络中断、429、5xx、慢工具）。

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
- 公司已有 DeerFlow 时，适配原生宿主接口与完整 feature 差异，不要覆盖公司的业务代码、配置、Skills 或认证体系。

## 10. 文档索引

| 文档 | 状态与用途 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 当前架构与工作流（权威） |
| [HANDOFF.md](HANDOFF.md) | 当前交接状态（本文） |
| [API.md](API.md) / [openapi.json](openapi.json) | 当前接口契约 |
| [CHATGPT_BENCHMARK_2026-09-16.md](CHATGPT_BENCHMARK_2026-09-16.md) | ChatGPT 实测观察、差距与改造前后对照 |
| `README.deepresearch.md`（仓库根） | 安装、配置、使用 |
| [NATIVE_RUNTIME.md](NATIVE_RUNTIME.md) / [REUSE_AUDIT.md](REUSE_AUDIT.md) | 原生复用专题，仍然有效 |
| [EXTENDING.md](EXTENDING.md) | 新增研究角度与来源 |
| [STABILITY_AUDIT_2026-09-16.md](STABILITY_AUDIT_2026-09-16.md) | 2026-09-16 稳定性修复与恢复验收 |
| [DESIGN_BASELINE.md](DESIGN_BASELINE.md) / [RUNTIME.md](RUNTIME.md) / [VERIFICATION.md](VERIFICATION.md) / [COMPUTER_USE_2026-09-15.md](COMPUTER_USE_2026-09-15.md) / [COMPUTER_USE_2026-09-16.md](COMPUTER_USE_2026-09-16.md) / [LOCAL_AGENT_HANDOFF.md](LOCAL_AGENT_HANDOFF.md) | 历史记录；早期设计与“未完成”结论不能覆盖后来的实现与验收 |
