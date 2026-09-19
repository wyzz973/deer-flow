# 测试与真实验收

“完成”的定义：单测通过 + lint/格式通过 + 相关文档已同步；改动影响研究行为时，再加一次真实运行的验证，并把数字记进 `docs/deepresearch/HANDOFF.md`。

## 单元测试

后端测试在仓库根的 `tests/deepresearch/`，从 `backend/` 运行：

```bash
cd backend
uv run --no-sync --with python-docx python -m pytest ../tests/deepresearch -q          # 全部，约 10 秒
uv run --no-sync python -m pytest ../tests/deepresearch/test_runner.py -q -x            # 单个文件
uv run --no-sync ruff check deepresearch ../tests/deepresearch
uv run --no-sync ruff format --check deepresearch ../tests/deepresearch                 # CI 会卡格式
```

动了和引擎交界的地方（`native.py`、`models.py`、压缩、工具）时再跑：
`tests/test_subagent_executor.py tests/test_summarization_middleware.py tests/test_context_compaction.py tests/test_web_fetch_paging.py`。

前端从 `frontend/` 运行，pnpm 一律经 `scripts/pnpm.py`：

```bash
cd frontend
python3 ../scripts/pnpm.py rstest run deepresearch      # DeepResearch 单测
python3 ../scripts/pnpm.py rstest run <文件名片段>        # 单个
python3 ../scripts/pnpm.py check                         # ESLint + tsc，提交前必跑
```

### 哪个测试文件管什么

| 改动的领域 | 后端（`tests/deepresearch/`） | 前端（`frontend/tests/unit/`） |
| --- | --- | --- |
| 设置、快照、指纹、设置接口 | `test_research_settings.py`、`test_core.py`（配置校验）、`test_offline_config.py`（示例与离线模板）、`test_live_config.py`（验收启动器） | `core/deepresearch/settings.test.ts`、`components/deepresearch/research-settings.dom.test.tsx`、夹具 `core/deepresearch/fixtures.ts` |
| 节点参数、模型、网关兼容 | `test_tuning.py`、`test_research_models.py`、`test_compatibility.py` | — |
| 数据源、供应商切换、检索预算收尾 | `test_source_providers.py`、`test_tuning.py`（MCP 直出工具） | `sources-panel.dom.test.tsx` |
| 各节点发给模型的载荷、提示词缓存 | `test_runner.py`、`test_prompt_cache.py`、`test_structured_role.py` | — |
| 对接引擎、预算份额、计量 | `test_native_bridge.py`、`test_metering.py`、`test_stability.py` | — |
| 工作流、补研、错误码、恢复、会话控制 | `test_workflow.py`、`test_review_fixes.py`、`test_conversation.py`、`test_api.py`、`test_store.py` | `core/deepresearch/hooks.dom.test.tsx`、`events.test.ts` |
| 指标、活动时间线、调用审计 | `test_metrics.py`、`test_activity.py`、`test_llm_audit.py`、`test_audit_skill.py`（本技能的脚本） | `metrics-panel.dom.test.tsx`、`llm-calls.dom.test.tsx` |
| 报告、引用、证据 | `test_report_quality.py`、`test_dependency_evidence.py`、`test_sources.py` | `report-view.dom.test.tsx`、`report-reader.dom.test.tsx` |

文件名会变，以 `ls tests/deepresearch` 为准；找不到就 grep 相近设置或函数名在测试里的出现位置。

### 写测试的约定

- **先写失败的测试**，再改代码（后端 TDD 是硬性要求）。
- 测试名是一句行为描述（`test_a_second_follow_up_reuses_the_report_the_first_one_sent`），断言旁用注释说明这条行为为什么重要。
- 不连网、不要真实凭据。引擎的子 Agent 执行用桩替换：`monkeypatch.setitem(sys.modules, "deerflow.subagents.executor", …)`（见 `test_native_bridge.py`）；runner 层替换 `runner._json` / `runner._markdown` / `execute_role` 捕获载荷（见 `test_runner.py`、`test_prompt_cache.py`）。
- 公共夹具在 `tests/deepresearch/conftest.py`：`settings`（由示例配置加载）、`plan`、`result`。
- 指标类测试：历史数据缺字段时应返回 `None` 而不是 0，要有对应断言。
- 改了请求构造，`test_prompt_cache.py` 是守卫：同一节点只在新内容上不同的两次调用，必须共享大部分前缀。

## 浏览器端到端（合成数据）

演示后端端口必须是 8022；本机 3000 常被占用，前端另选端口：

```bash
# backend/
DEEPRESEARCH_DEMO_DATA_DIR=.deerflow/deepresearch/e2e DEEPRESEARCH_DEMO_FRONTEND_PORT=3200 \
DEEPRESEARCH_DEMO_PLAN_COUNTDOWN=5 DEEPRESEARCH_DEMO_STEP_DELAY=0.2 \
  uv run --no-sync python -m uvicorn deepresearch.demo:app --host 127.0.0.1 --port 8022
# frontend/（同一目录不能同时跑两个 next dev；需要时复制一份前端目录）
SKIP_ENV_VALIDATION=1 DEER_FLOW_AUTH_DISABLED=1 DEER_FLOW_ENV=development \
NEXT_PUBLIC_BACKEND_BASE_URL=http://127.0.0.1:8022 \
  python3 ../scripts/pnpm.py exec next dev --hostname 127.0.0.1 --port 3200
# frontend/
DEEPRESEARCH_E2E_FRONTEND_PORT=3200 DEEPRESEARCH_E2E_REUSE_BACKEND=1 DEEPRESEARCH_E2E_REUSE_FRONTEND=1 \
  python3 ../scripts/pnpm.py exec playwright test -c playwright.deepresearch.config.ts
```

在非 git 的前端副本里跑时加 `--output <项目外目录> --reporter=list`：Playwright 往项目目录写结果会触发 Tailwind 扫描，开发服务器进入热更新死循环，表现为页面一直加载不出报告。

## 真实验收（真实模型与检索）

用自己的端口和验收目录，不要碰用户的 8001/3100：

```bash
# 仓库根。去掉 --resume-dir 会新建隔离目录；--resume-dir 必须是绝对路径
uv run --directory backend --no-sync python -m deepresearch.live \
  --allow-live --model <config.yaml 里的模型名> --unlimited-budget --max-output-tokens 16384 \
  --port 8012 --frontend-port 3112 [--jina-no-key] [--research-overlay <覆盖用的 yaml>] [--resume-dir <绝对路径>]
```

- 启动器生成私有配置，不改根配置；凭据从 `.env` 和进程环境读，只存在于进程里。
- `--research-overlay` 用来试一组设置（节点参数、MCP 数据源、补研缺口）而不改示例配置——调优验证就靠它。
- 前端要看页面时另起一个 `next dev`，环境变量 `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8012`、`DEER_FLOW_AUTH_DISABLED=1`。

用接口发起研究（**一定要带 budget**；省略会落到很小的默认预算，首轮就被收尾，结果是 `NO_EVIDENCE`）：

```bash
B=http://127.0.0.1:8012/api/deepresearch
R=$(curl -s -X POST $B -H 'Content-Type: application/json' -H "Idempotency-Key: $(uuidgen)" -d '{
  "query": "<研究问题>",
  "budget": {"max_iterations": 1, "max_units": 8, "max_tool_calls": null, "max_elapsed_seconds": null, "max_model_tokens": null}
}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')
# 等到 AWAITING_PLAN_CONFIRMATION 后确认计划（或等服务端倒计时自动开始）
curl -s -X POST $B/$R/plan/approve -H 'Content-Type: application/json' -d '{"plan_version": 1}'
# 轮询 GET $B/$R 直到 COMPLETED / FAILED；追问用 POST $B/$R/messages {"text": "...", "client_message_id": "<唯一>"}
```

预算各项不能超过 `GET $B/capabilities` 里的上限；`null` 表示不限。一次公网研究通常 5–12 分钟、几百万输入 Token，跑之前确认用户接受这个成本。

### 验证一次调优

1. 基线：当前设置下跑一次，记下 run id。
2. 只改一类设置（用 `--research-overlay` 或设置页），新建研究，用**同一个问题**。
3. `audit_run.py --run <新> --baseline <旧>`，先比比例再比总量（见 [audit-report.md](audit-report.md) 的读数陷阱）。
4. 把两次的 run id、改了什么、关键数字写进 HANDOFF 的“真实验收”。

## 体检

```bash
uv run --directory backend --no-sync python -m deepresearch.doctor --config <research.yaml> \
  [--probe-model <模型名>] [--probe-sources] [--probe-mcp]
```

不带 probe 只做静态校验；`--probe-model` 会真实调用模型（普通回复、工具调用、只返回 JSON 的契约），报告耗时、是否上报用量、JSON 是否可解析、输出速度——接入一个新的模型网关时先跑它。
