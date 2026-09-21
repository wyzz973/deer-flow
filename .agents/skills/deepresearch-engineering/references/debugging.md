# 调试：研究失败或行为异常时

## 先看什么（从便宜到贵）

1. **运行快照**：状态、`error.code`、`unit_statuses`、`gaps`、`usage`。
   `audit_run.py --run <id>` 的底稿开头就有；网关在线时 `GET /api/deepresearch/<id>`。
2. **底稿的“发现”**：大多数常见故障（预算太小、供应商全挂、输出被截断、步骤被提前收尾）规则已经能认出来。
3. **事件流**：发生了什么、先后顺序。底稿 JSON 的 `process.event_counts`，或 `GET /<id>/events?after=0`（SSE，调试时加超时）。
4. **具体的模型调用**：`show_call.py --run <id> --node <节点>` 列出，再 `--call <id>` 打开。模型“为什么这么做”只能从这里看。
5. **Trace**：`GET /<id>/trace`、`/trace/export`（span 树、工具调用的输入输出摘要）。
6. **日志**：`<数据目录>/research/research.log`（JSON 行，结构化审计日志）；网关标准输出里有引擎中间件的告警与 traceback。
7. **配置体检**：`uv run --directory backend --no-sync python -m deepresearch.doctor --config <research.yaml> [--probe-model <名字>] [--probe-sources] [--probe-mcp]`。不带 probe 时不联网。

## 错误码

`error.recoverable=true` 的可以从检查点重试（页面“重试”或 `POST /<id>/retry`），已完成的步骤和章节不会重跑。
**预算类失败例外**：重试不改预算、不清已用量，已“完成”的空步骤也不会重跑，所以 `BUDGET_EXHAUSTED`、`RESEARCH_BUDGET_SPENT`、由 `token_capped` 引出的 `NO_EVIDENCE` 重试无效，要用新的预算**新建**研究。

| 错误码 | 含义 | 通常的根因 / 去哪看 |
| --- | --- | --- |
| `NO_EVIDENCE` | 没有任何带可引用证据的发现 | **先看工具调用数**：为 0 且每步只有 1 轮、`finish_reason=tool_calls` → 工具调用被 Token 预算摘掉了（底稿 `budget-too-small`），和检索工具无关（这种情况现在的错误文案会直接指出预算且 `recoverable=false`；旧版本记录的运行仍写着“请检查检索/读取工具”，不要被它带偏）。工具调用不为 0 → 检索供应商全失败；结果只有摘录而 `cite_search_results=false`；conversion 丢弃了无证据结论（底稿 `findings-pruned`，事件 `research.output.pruned`）。**研究员明明读了页面却没有可引用证据**时，打开该步的 conversion 调用看 `observed_calls`：条目只有 `raw_id`/`tool_name`、没有 `url`/`title`，说明工具的返回没被识别成页面或记录——2026-09-21 之前直接暴露的 MCP 读取工具（`kind: mcp` + `role: read`）就是这样；现在页面地址取自调用参数，见 `sources.opened_pages`。也检查数据源的 `role` 填得对不对：把能打开原文的工具填成 `search`，它读到的内容就只是“发现”|
| `RESEARCH_GAPS` | 有缺口且不允许带局限出报告 | `allow_limited_report=false`；看 `gaps` 的 code |
| `BUDGET_EXHAUSTED` / `RESEARCH_BUDGET_SPENT` / `TIME_BUDGET` / `RESEARCH_TIME_SPENT` | Token、工具调用或时间预算用完 | 每步份额 =（`max_model_tokens` − 报告预留 − 已用）÷ 并行步骤数 − 8192，太小时第一轮就被收尾；接口创建任务时省略 `budget` 会用很小的默认值（12 万） |
| `NATIVE_AGENT_TIMEOUT` | 一次角色执行超时 | 慢模型 + 长上下文；调 `nodes.<节点>.timeout_seconds`、`max_seconds_per_unit`，或减小上下文（`max_searches_per_unit`、压缩） |
| `NATIVE_AGENT_FAILED` / `MODEL_UNAVAILABLE` | 模型调用失败 | Trace 里模型 span 的错误类型（连接、鉴权、4xx）；`doctor --probe-model` |
| `MODEL_NOT_CONFIGURED` / `MODEL_AUTH_REQUIRED` | 研究模型不存在或凭据引用解析不到 | `models`、`default_model`、`api_key: $ENV` 或 `secret:NAME` |
| `OUTPUT_SCHEMA` | 多次重试后仍解析不出契约 | 打开 conversion/plan 的最后一次调用；常见是输出被截断（`finish_reason=length`）或模型不守 JSON |
| `REPORT_EMPTY` | 所有章节都是空的 | 写作模型的 `max_tokens` 太小或超时 |
| `REPORT_REVISION_TRUNCATED` | 改写报告丢了多个章节 | 调大 `nodes.revision.max_tokens` 或缩小修改范围 |
| `FINAL_VALIDATION` | 报告没过引用/结构终检 | 写作模型编造证据 ID；看 `final_errors` |
| `PLAN_INVALID` | 计划引用了未注册的角色/数据源或超预算 | 弱模型的规划输出；`require_dual_source` 与实际数据源不匹配 |
| `TOOL_DENIED` / `SOURCE_UNKNOWN` | 工具策略或数据源名不匹配 | 角色的 `tools` 白名单、`sources` 配置、MCP `allowed_tools` |
| `PROCESS_INTERRUPTED` | 网关重启或停止时运行在进行中 | 正常现象，重试即可 |
| `CONFIG_CHANGED` / `PROFILE_MISSING` | 旧运行对不上当前配置 | 早于配置快照的运行需要原来的运维配置 |
| `RUN_BUSY` / `RUN_STOPPED` / `PLAN_VERSION` / `IDEMPOTENCY_CONFLICT` | 控制操作与当前状态冲突 | 前端或调用方时序问题；看 `conversation.py` 的状态约束 |
| `CAPACITY` | 同时运行数达到 `max_active_runs` | 等待或调大 |

完整列表：`grep -rhoE 'ResearchError\("[A-Z_]+"' backend/deepresearch | sort -u`。

## 常见故障的定位路径

**研究完成了但很慢。** 底稿第 2 节：工具秒数远大于模型秒数 → 第 5 节按供应商的表，找“尝试多、有结果为 0”的供应商（鉴权、配额、超时）。排在前面的坏供应商会让每次调用先失败一轮再切换。模型秒数占大头 → 看节点表的输出速度和 P95，再看 [tuning.md](tuning.md)。

**步骤“完成”却没有发现。** 先看该步的 `stop_reason`（`token_capped`：预算份额太小；`turn_capped`；`loop_capped`：在重复同类调用）。再打开它最后一轮研究调用和对应的 conversion 调用：笔记里有没有结论、conversion 是否因为找不到可引用证据而丢弃。

**想知道这次运行是页面建的还是接口建的。** 记录里没有客户端标识，但预算会说话：页面创建时把 `GET /capabilities` 的 `budget_ceiling` 原样作为预算发出；预算与契约默认值逐项相同（`max_iterations 2 / max_units 12 / max_tool_calls 60 / max_elapsed_seconds 900 / max_model_tokens 120000`，见 `contracts.ResearchBudget`）说明调用方省略了 `budget`。计划确认只等了零点几秒也更像脚本。

**补研停不下来或白补。** 底稿第 6 节的缺口 code。`missing-external` / `missing-internal` 反复出现 → `require_dual_source` 开着但只有一种来源，永远补不上。`open-questions` → 每步最多补一轮，嫌慢就从 `supplement_gap_codes` 去掉。

**章节反复修复。** `show_call.py --node section` 找同一 `unit`（`report-section-N`）的第二次调用，打开后看用户消息**末尾**的 `validation_errors_to_fix` 和 `previous_draft`：是引用了不在 `evidence` 里的 ID、超长，还是写了 URL/数字引用。

**缓存命中突然变低。** 底稿第 4 节的前缀断点表会给出第一条不同的消息。不是压缩造成的断点，说明有东西在往对话前面插入或改写消息（检查 `tool_receipt_ledger`、新加的中间件、载荷字段顺序）。

**网关起不来：`Acceptance configuration changed; cannot resume`。** 验收启动器发现生成的配置和数据目录里保存的不一致。新增的设置只要等于默认值不算变更；其余差异是真的配置变化——换一个新的验收目录（去掉 `--resume-dir`），不要改保存的文件去迁就。

**页面报 hydration 警告、属性里带 `data-cmux-…` 或类似前缀。** 浏览器或扩展在 React 接管前改了自动聚焦的输入框，只在 `next dev` 出现，不是产品缺陷；研究页输入框已加 `suppressHydrationWarning`。

**改了代码不生效。** 验收网关不是热重载的，要重启；前端 `next dev` 是热更新的。旧运行沿用它开始时的设置与提示词快照，验证改动要新建研究。

## 复现一次模型调用

```bash
backend/.venv/bin/python .agents/skills/deepresearch-engineering/scripts/show_call.py --run <id> --call <call_id> --request > /tmp/request.json
curl -sS "$BASE_URL/chat/completions" -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d @/tmp/request.json
```

请求体里没有凭据；`$KEY` 从你的环境取，不要写进文件或日志。用它验证“换个 temperature / max_tokens / 模型，这一步会不会好”，比重跑整次研究快得多。

## 动手之前

- 端口 8001、3000、3100 和 Docker 容器常常是用户或别的会话的；只读确认归属（`lsof -nP -iTCP:<端口> -sTCP:LISTEN`、`pgrep -fl deepresearch.live`），不要直接杀。
- 需要自己的环境时另起一套端口和验收目录，见 [testing.md](testing.md)。
- 先写能复现问题的测试，再改代码（仓库规定 TDD）。
