# 离线开发交接：给本地 Agent

更新时间：2026-09-18。读者：在不联网的机器上继续开发 DeepResearch 的本地 Agent，以及使用它的人。

本文只讲怎么做。为什么这样设计见 [ARCHITECTURE.md](ARCHITECTURE.md)，历史进度和验收记录见 [HANDOFF.md](HANDOFF.md)。
配置分三份：[模型配置](MODEL_CONFIGURATION.md)、[DeerFlow 宿主配置](DEERFLOW_CONFIGURATION.md)、[研究配置](RESEARCH_CONFIGURATION.md)。

## 0. 每次开始工作前

按顺序做，不要跳过：

1. 读本文第 1、2 节。
2. 运行第 3.3 节的环境检查，全部通过再继续。
3. 看任务属于第 5 节表格里的哪一行，找到要改的文件和对应测试。
4. 按第 6 节的流程做。

一次只做一件小事。每做完一件：跑测试 → 改文档 → 提交。

## 1. 项目是什么

- **DeerFlow**：AI Agent 系统。后端是 Python（`backend/`），前端是 Next.js（`frontend/`）。
- **DeepResearch**：DeerFlow 里的深度研究功能，交互模仿 ChatGPT 深度研究：
  用户提问 → 生成研究计划卡 → 倒计时后自动开始 → 多个研究员 Agent 并行查资料 → 写成带引用的长报告。
- **代码位置**：后端 `backend/deepresearch/`；前端 `frontend/src/components/deepresearch/` 和 `frontend/src/core/deepresearch/`；
  测试 `tests/deepresearch/` 和 `frontend/tests/**/deepresearch/`；研究方法论 `examples/deepresearch/skills/`。
- **当前状态**：
  - 功能完整。
  - 联网环境用 DeepSeek 真实验收过，记录见 HANDOFF.md 第 4 节。
  - 离线环境加本地模型的真实研究还没有人验收。只验证过：用离线模板启动网关时研究模块就绪；模型连不上时，doctor 和研究任务都会报错。

## 2. 铁律

违反任何一条之前，先停下来问人：

1. 不要提交 `backend/packages/harness/deerflow/community/aio_sandbox/local_backend.py` 的改动，那是使用者自己的本地修改。
2. 不要提交这些东西：`.env`、`config.yaml`、`extensions_config.json`、`deepresearch.local.yaml`、`skills/custom/`、`.deerflow/`、任何 `*.sqlite3`、任何密钥。
3. 不要修改 `CLAUDE.md`。
4. 不要为了通过测试而放宽引用校验，也不要伪造证据 ID 或网址。引用校验的代码在 `report.py` 和 `report_policy.py`。
5. 不要删除测试，也不要加 `skip`。测试失败时修代码；修不好就如实记录。
6. 不要另写一套 Agent 循环、工具注册或登录系统。DeepResearch 复用 DeerFlow 的执行引擎（见 ARCHITECTURE.md 第 1 节）。
   但**配置是独立的**：研究的模型、数据源与供应商、MCP 服务、角色、提示词都写在研究配置或设置页里，不要改成读宿主
   `config.yaml` 的 `models` / `tools` / `subagents` 或 `extensions_config.json`。
7. 不要要求模型开启 JSON mode。模型输出普通文本，由 `structured.py` 解析。
8. 交互保持 ChatGPT 风格：计划卡、倒计时、编辑、更新、报告阅读器。不要改成表单填约束。
9. 离线时不要运行需要联网的命令：`uv sync`、`pip install`、`pnpm install`、`git push`、`playwright install`。
10. 不要结束不是自己启动的进程，也不要清理 Docker。结束之前先用 `lsof -nP -iTCP:端口 -sTCP:LISTEN` 看清是谁的进程。
11. 行为变了，就在同一次提交里更新文档：用户能看到的写进 `README.deepresearch.md`，开发约定写进
    `backend/deepresearch/AGENTS.md` 或 `frontend/AGENTS.md`。
12. 指标里的 `null` 表示“没测到”，不要当成 0。

## 3. 离线环境

### 3.1 必须在联网时准备好的东西

到了离线环境就补不了。

| 项目 | 联网时执行 | 离线时怎么检查 |
| --- | --- | --- |
| 后端依赖 | `cd backend && uv sync --locked` | `backend/.venv/bin/python` 存在 |
| 前端依赖 | `cd frontend && python3 ../scripts/pnpm.py install` | `frontend/node_modules/.bin/next` 存在 |
| pnpm | 安装 pnpm 10.26.2 到 PATH | `pnpm --version` 输出 10.26.2 |
| 浏览器测试用的 Chromium（可选） | `cd frontend && python3 ../scripts/pnpm.py exec playwright install chromium` | macOS：`ls ~/Library/Caches/ms-playwright`；Linux：`ls ~/.cache/ms-playwright` |
| 本地模型服务 | 在内网部署 vLLM、Xinference 等，见 [MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md) | 第 4.2 节的模型探测 |
| 内网知识库（只有真实研究需要） | RAGFlow、LightRAG 或内网 MCP 服务 | 在本机能访问它的地址 |

### 3.2 离线时设置的环境变量

写进 shell 配置文件（例如 `~/.zshrc` 或 `~/.bashrc`）：

```bash
export UV_OFFLINE=1               # uv 不访问网络；缺包时直接报错，不会去下载
export NEXT_TELEMETRY_DISABLED=1  # Next.js 不上报遥测
```

### 3.3 环境检查（每次开工先跑）

从仓库根目录开始执行：

```bash
git status --short
# local_backend.py 显示为修改是正常的，不要动它

cd backend
uv run --no-sync python -c "import deepresearch, deerflow, langgraph; print('backend ok')"
# 期望输出：backend ok

uv run --no-sync python -m pytest ../tests/deepresearch -q -x
# 期望：最后一行是 “N passed”，没有 failed

cd ../frontend
python3 ../scripts/pnpm.py exec tsc --noEmit
# 期望：没有任何输出
```

任何一步失败，先解决它（查第 8 节），不要开始写代码。

## 4. 三种运行方式

### 4.1 演示模式：不需要模型，开发界面时首选

演示模式用合成数据走完整个界面流程，不调用模型和工具。需要开两个终端。

终端 1，在仓库根目录执行，启动演示后端：

```bash
cd backend
DEEPRESEARCH_DEMO_DATA_DIR=.deerflow/deepresearch/demo DEEPRESEARCH_DEMO_FRONTEND_PORT=3200 \
  DEEPRESEARCH_DEMO_PLAN_COUNTDOWN=10 DEEPRESEARCH_DEMO_STEP_DELAY=0.5 \
  uv run --no-sync python -m uvicorn deepresearch.demo:app --host 127.0.0.1 --port 8022
```

终端 2，在仓库根目录执行，启动前端：

```bash
cd frontend
SKIP_ENV_VALIDATION=1 DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_ENV=development \
  NEXT_PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:8022 \
  python3 ../scripts/pnpm.py exec next dev --hostname 127.0.0.1 --port 3200
```

浏览器打开 `http://127.0.0.1:3200/deepresearch-demo` 或 `http://127.0.0.1:3200/workspace/deepresearch`。

注意：同一个 `frontend/` 目录同时只能运行一个 `next dev`。

### 4.2 真实模式：本地模型加内网知识库

完整步骤见 [DEERFLOW_CONFIGURATION.md](DEERFLOW_CONFIGURATION.md) 第 2 节。简要顺序：

1. 生成 `config.yaml`（示例配置 + `examples/deepresearch/offline/host-config.fragment.yaml`）。
2. 在 `.env` 里写上 `config.yaml` 用到的每个 `$变量`。
3. 复制研究配置：`cp examples/deepresearch/offline/research.yaml deepresearch.local.yaml`。
4. 安装研究 Skill 到 `skills/custom/`。
5. 检查配置并探测模型：

   ```bash
   cd backend
   uv run --no-sync python -m deepresearch.doctor --config ../deepresearch.local.yaml --probe-model local-model
   # 期望：agents 全部为 true；model_probe.ok 为 true；退出码为 0
   ```

6. 启动网关（在仓库根目录执行）：

   ```bash
   cd backend
   DEER_FLOW_HOME=.deer-flow DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_ENV=development PYTHONPATH=. \
     uv run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001
   ```

7. 启动前端（在仓库根目录执行）：

   ```bash
   cd frontend
   DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_ENV=development \
     python3 ../scripts/pnpm.py exec next dev --hostname 127.0.0.1 --port 3000
   ```

8. 检查：`curl http://127.0.0.1:8001/api/deepresearch/capabilities` 返回 `"ready": true` 和 `"mode": "deerflow"`。
9. 浏览器打开 `http://127.0.0.1:3000/workspace/deepresearch`。

离线环境**不要用** `python -m deepresearch.live`，它固定使用公网的 `web_search` 和 `web_fetch`。

### 4.3 只跑测试

见第 7 节。测试不需要模型，也不需要联网。

## 5. 改什么，去哪里

| 任务 | 后端文件（`backend/deepresearch/`） | 前端文件（`frontend/src/`） | 测试 |
| --- | --- | --- | --- |
| 任何提示词 | `prompts.py`（全部默认值，20 条） | 设置页“提示词”（`settings/prompts-section.tsx`） | `test_runner.py`、`test_research_settings.py` |
| 请求改写（第一步） | `workflow.py`（`rewrite` 节点）、`structured.py`（`rewrite_request`）、`prompts.py`（`rewrite`） | `components/deepresearch/request-card.tsx` | `test_workflow.py`、`test_runner.py` |
| 计划内容、计划提示词 | `runner.py`（`plan`）、`workflow.py`（`planner`）、`prompts.py`（`plan`） | `components/deepresearch/plan-card.tsx` | `test_runner.py`、`test_workflow.py`、`plan-card.dom.test.tsx` |
| 倒计时、编辑、更新、追问 | `conversation.py`、`service.py` | `components/deepresearch/research-conversation.tsx`、`core/deepresearch/hooks.ts` | `test_conversation.py`、`hooks.dom.test.tsx` |
| 研究员怎么查资料 | `runner.py`（`RESEARCH_INSTRUCTIONS`）、`native.py`、`examples/deepresearch/skills/*/SKILL.md` | — | `test_native_bridge.py`、`test_runner.py` |
| 回答整理成数据 | `structured.py`、`output.py` | — | `test_structured_role.py` |
| 报告大纲、章节、摘要 | `runner.py`（`write_report` 及写作指令）、`report.py` | `components/deepresearch/report-view.tsx`、`report-reader.tsx` | `test_report_quality.py` |
| 引用资格、来源 | `report_policy.py`、`observations.py`、`sources.py`、`evidence.py` | `components/deepresearch/sources-panel.tsx`、`citation-preview.tsx` | `test_sources.py`、`test_native_sources.py`、`citation-preview.dom.test.tsx` |
| 数据源、供应商与故障切换 | `providers.py`、`channels.py`、`extract.py`、`mcp.py` | 设置页“数据源与搜索”（`settings/sources-section.tsx`） | `test_source_providers.py` |
| 研究模型、上下文压缩 | `models.py`、`native.py`（`model_budget_config`） | 设置页“模型 / 运行参数” | `test_research_models.py` |
| 按节点的模型与参数（`nodes:`） | `config.py`（`NodeSpec`）、`models.py`（`model_for`、`with_node`）、`native.py`（`node_of`）、`structured.py` | 设置页“节点调参”（`settings/nodes-section.tsx`）；指标“按节点”表 | `test_tuning.py` |
| 只用 MCP、没有原文时的引用规则 | `report_policy.py`（`results_citable`）、`runner.py`（`records_only`）、`observations.py`、`prompts.py`（`research_records`） | 设置页“数据源与搜索”顶部提示 | `test_tuning.py`、`test_review_fixes.py` |
| MCP 白名单、鉴权、失败诊断 | `mcp.py`、`secrets.py`、`config.py`（`McpServerSpec.allowed_tools`） | 设置页“MCP 服务” | `test_tuning.py` |
| 时间预算收尾、预算按任务计 | `store.py`（`research_seconds_left`）、`channels.py`（`SearchBudget.deadline`）、`runner.py`、`service.py`、`conversation.py` | 指标“预算”区 | `test_tuning.py`、`test_conversation.py` |
| 设置页能保存什么（安全边界） | `profile.py`（`guard`、`masked`） | — | `test_review_fixes.py` |
| 只支持 Chat Completions 的模型网关 | `chat_completions.py`、`models.py`（`legacy_token_param`、流式用量） | 设置页“模型”的兼容项 | `test_review_fixes.py` |
| 设置、快照、密钥 | `profile.py`、`secrets.py`、`catalog.py`、`service.py` | `components/deepresearch/research-settings.tsx`、`core/deepresearch/settings.ts` | `test_research_settings.py`、`settings.test.ts`、`research-settings.dom.test.tsx` |
| 模型调用审计 | `audit.py`、`trace.py`、`store.py` | `components/deepresearch/llm-calls-panel.tsx`、`llm-call-dialog.tsx`、`core/deepresearch/llm-calls.ts` | `test_llm_audit.py`、`llm-calls.test.ts`、`llm-calls.dom.test.tsx` |
| 活动时间线 | `activity.py` | `sources-panel.tsx`（活动页签） | `test_activity.py`、`activity-panel.dom.test.tsx` |
| 成本与效率指标 | `metrics.py`、`trace.py` | `components/deepresearch/metrics-panel.tsx` | `test_metrics.py`、`metrics-panel.dom.test.tsx` |
| Trace | `trace.py`、`store.py` | `components/deepresearch/trace-panel.tsx`、`core/deepresearch/trace-model.ts` | `test_trace_finalization.py`、`trace-panel.dom.test.tsx` |
| 接口 | `api.py` | `core/deepresearch/api.ts`、`types.ts` | `test_api.py` |
| 配置项 | `config.py`、`contracts.py` | `core/deepresearch/types.ts` | `test_core.py`、`test_offline_config.py` |
| 配置检查、模型探测 | `doctor.py` | — | `test_doctor.py` |

后端测试在 `tests/deepresearch/`，前端测试在 `frontend/tests/unit/**/deepresearch/`。

## 6. 标准开发流程

1. **读**：打开要改的文件，找到相关函数。改提示词或规则之前，先读 ARCHITECTURE.md 里对应的章节。
2. **先写测试**：在对应测试文件里加一个测试，描述期望行为。运行它，确认它失败，而且失败原因就是要修的问题。
3. **改代码**：只改需要改的地方。沿用周围代码的写法，不做无关重构。
4. **跑这个测试**：确认它通过。
5. **跑整组测试**：后端或前端的整组测试（第 7 节），确认没有破坏别的功能。
6. **格式化**：后端运行 `ruff format`，前端运行 `prettier --write`（第 7 节）。
7. **更新文档**：按铁律第 11 条。
8. **提交**：按第 9 节。

改提示词时要特别注意：

- 引用标记 `[[E012]]` 必须保留。不要写“不要提 markers”这类有歧义的话，弱模型会因此不写引用。
- 研究员的进度说明要使用用户的语言，也不要提工具名和 Skill 文件。

## 7. 测试命令

后端（在 `backend/` 目录执行）：

```bash
uv run --no-sync python -m pytest ../tests/deepresearch -q                            # 全部 DeepResearch 测试
uv run --no-sync python -m pytest ../tests/deepresearch/test_metrics.py -q            # 单个文件
uv run --no-sync python -m pytest "../tests/deepresearch/test_metrics.py::test_usage_is_normalized_across_provider_shapes" -q  # 单个测试
uv run --no-sync ruff format deepresearch ../tests/deepresearch                       # 自动格式化
uv run --no-sync ruff check deepresearch ../tests/deepresearch                        # 期望：All checks passed!
```

前端（在 `frontend/` 目录执行）：

```bash
python3 ../scripts/pnpm.py rstest run deepresearch            # DeepResearch 单元测试
python3 ../scripts/pnpm.py check                              # ESLint 加类型检查，期望没有错误
python3 ../scripts/pnpm.py exec prettier --write 改过的文件路径
```

浏览器端到端测试（可选，需要第 3.1 节的 Chromium）：

1. 按第 4.1 节启动演示后端（端口 8022），但把 `DEEPRESEARCH_DEMO_PLAN_COUNTDOWN` 改成 5、`DEEPRESEARCH_DEMO_STEP_DELAY` 改成 0.2，
   `DEEPRESEARCH_DEMO_DATA_DIR` 改成 `.deerflow/deepresearch/e2e`。
2. 按第 4.1 节启动前端（端口 3200）。
3. 在 `frontend/` 目录执行：

```bash
DEEPRESEARCH_E2E_FRONTEND_PORT=3200 DEEPRESEARCH_E2E_REUSE_BACKEND=1 DEEPRESEARCH_E2E_REUSE_FRONTEND=1 \
  python3 ../scripts/pnpm.py exec playwright test -c playwright.deepresearch.config.ts
# 期望：5 passed
```

交接时（2026-09-17）的结果：`tests/deepresearch` 全部通过；前端 DeepResearch 单元测试全部通过；`pnpm check` 通过；浏览器测试 5 项通过。
宿主仓库还有一个已知失败，它不在 `tests/deepresearch` 里：`backend/tests/test_agent_guidance_check.py` 报
`subagents/AGENTS.md` 超出软上限。这与 DeepResearch 无关，不要为了修它去删 AGENTS.md 的内容。

## 8. 常见错误与处理

| 看到的错误 | 原因 | 处理 |
| --- | --- | --- |
| `Environment variable XXX not found for config value $XXX` | `config.yaml` 用了 `$XXX`，但 `.env` 里没有 | 在 `.env` 里加 `XXX=值`；服务不校验时写任意非空值 |
| doctor 输出 `One or more Custom Agents are not configured` | `config.yaml` 缺少研究角色 | 合并 `host-config.fragment.yaml` 的 `subagents.custom_agents` |
| doctor 的 `model_probe.error` 是 `APIConnectionError` | 模型服务没启动，或者地址写错 | 检查 `base_url`（通常以 `/v1` 结尾）和端口；确认模型服务在运行 |
| doctor 警告 `No tool call` | 模型或服务端没有开启工具调用 | vLLM 启动时加 `--enable-auto-tool-choice --tool-call-parser 解析器名`；或换支持工具调用的模型 |
| `/api/deepresearch/capabilities` 返回 404 | 研究插件没有加载 | 检查 `config.yaml` 的 `plugins` 段和 `config_path`；查看网关日志 |
| 研究很快失败，错误码 `NATIVE_AGENT_FAILED` | 通常是模型连不上 | 运行 doctor 探测模型；在 Trace 里搜 `model`，看 `error_type` |
| `TOOL_DENIED` | 研究单元需要的来源没有可用工具 | 检查 `sources[].tool` 的名字；检查研究员角色的 `tools` 白名单 |
| `Host research tool is unavailable: xxx` | 工具不在 `config.yaml` 的 `tools` 里，或 MCP 服务没连上 | 按工具名配置好工具，然后重启网关 |
| `DeerFlow mode needs explicit internal and external source bindings` | 只配置了一类来源 | 研究配置里设 `require_dual_source: false` |
| `BUDGET_LIMIT` “请求超过部署预算上限” | 用接口创建任务时，`budget` 超过了 `budget_ceiling` | 直接使用 `/capabilities` 返回的 `budget_ceiling` 作为 `budget` |
| `BUDGET_EXHAUSTED` | 研究用完了预算 | 调大研究配置的 `budget_ceiling` 后新建任务 |
| `CONFIG_CHANGED` | 改了研究配置或 Skill 文件后，继续旧任务 | 新建任务。只改 `pricing` 不影响旧任务 |
| `RESULT_CONTRACT`、整理失败、报告反复修复 | 模型太弱 | 调高 `output_retries`，`extraction_model` 换成最强的模型，减少 `max_report_sections` |
| `NATIVE_AGENT_TIMEOUT`，或研究特别慢 | 本地模型慢 | 研究配置 `skills.*.timeout_seconds` 和 `config.yaml` 里 `custom_agents.*.timeout_seconds` 都调大（取两者中较小的值，上限 1800）；降低 `max_concurrency` |
| 模型服务报上下文超长 | 输入超过了模型上下文 | 降低 `summarization.trigger` 的值、`sandbox.read_file_output_max_chars` 和 `tool_output.externalize_min_chars` |
| 打开页面跳到 `/setup` | 没有关闭登录 | 网关和前端两个进程都要设置 `DEER_FLOW_AUTH_DISABLED=1` |
| `Another next dev server is already running` | 同一个 `frontend/` 目录里已经在运行 `next dev` | 先停掉另一个，停之前确认是自己启动的 |
| 页面一直显示“执行中”，不刷新 | 反向代理压缩或缓冲了事件流 | 让代理遵守 `Cache-Control: no-transform`；本地直连时不会出现 |
| `uv` 报网络错误 | 命令没加 `--no-sync`，或者缺依赖包 | 加上 `--no-sync`；缺包只能联网补装 |

查问题的通用方法：

1. 在页面里打开研究，进入“活动”，再点 Trace，按时间看报错的节点。
2. 调用接口 `GET /api/deepresearch/{id}/trace/export`，下载完整记录。
3. 查看研究日志 `<data_dir>/research.log`。
4. 在“指标”页签看模型和工具的失败原因。

## 9. Git 提交

离线时只提交，不推送。联网后由人工推送到 `origin/feat/deepresearch-v1-fullstack-20260914`，不要推到同名远端分支。

```bash
git branch --show-current     # 期望：feat/deepresearch
git status --short
git add 具体文件路径            # 逐个写文件，不要用 git add -A 或 git add .
git diff --cached --stat      # 确认里面没有 local_backend.py、.env、config.yaml、*.sqlite3
git commit -m "fix(deepresearch): 一句话说明改了什么"
```

提交信息的开头按改动类型选择：

- `feat(deepresearch):` 新功能
- `fix(deepresearch):` 修复问题
- `docs(deepresearch):` 只改文档
- `test(deepresearch):` 只改测试

## 10. 文档索引

| 文档 | 什么时候读 |
| --- | --- |
| [OFFLINE_AGENT_GUIDE.md](OFFLINE_AGENT_GUIDE.md) | 每次开工（本文） |
| [MODEL_CONFIGURATION.md](MODEL_CONFIGURATION.md) | 接入或更换本地模型 |
| [DEERFLOW_CONFIGURATION.md](DEERFLOW_CONFIGURATION.md) | 配置 `config.yaml`、`.env`、MCP、前端，以及从零搭建 |
| [RESEARCH_CONFIGURATION.md](RESEARCH_CONFIGURATION.md) | 配置 `deepresearch.local.yaml` 的每个字段 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 理解工作流、数据、引用规则、指标 |
| [API.md](API.md) | 修改或调用接口 |
| [HANDOFF.md](HANDOFF.md) | 历史进度、验收记录、已知风险 |
