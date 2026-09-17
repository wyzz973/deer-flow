# 研究配置说明（`deepresearch.local.yaml`）

读者：要配置或修改 DeepResearch 研究行为的人或 Agent。
字段定义在 `backend/deepresearch/config.py`（`Settings`、`SkillSpec`、`SourceSpec`、`ModelPricing`），预算字段在
`backend/deepresearch/contracts.py`（`ResearchBudget`）。以代码为准；本文与代码不一致时，先改代码或本文，再继续工作。

## 1. 文件与加载

- 文件位置：`config.yaml` 的 `plugins` 里 `config_path` 指向的文件，通常是仓库根目录的 `deepresearch.local.yaml`。
- 模板：
  - 离线：`examples/deepresearch/offline/research.yaml`
  - 联网示例：`deepresearch.example.yaml`
  - 公网网页工具片段：`examples/deepresearch/native-web.sources.yaml`
- 相对路径（`data_dir`、`skills.*.path`）按**仓库根目录**解析。
- 改完要重启网关。
- 检查命令（不调用模型）：

  ```bash
  cd backend
  uv run --no-sync python -m deepresearch.doctor --config ../deepresearch.local.yaml
  ```

## 2. 配置指纹：改了配置，旧任务还能不能继续

创建任务时会记下配置指纹。指纹由两部分算出：配置内容（`pricing` 除外）和所有 Skill 文件的内容。

- 改了任何字段（`pricing` 除外）或任何 Skill 文件后，旧任务再继续、重试或确认计划，会报 `CONFIG_CHANGED`。新建的任务不受影响。
- 只改 `pricing` 不影响旧任务。

## 3. 顶层字段

| 字段 | 默认值 | 范围 | 说明 |
| --- | --- | --- | --- |
| `runner` | `demo` | `demo` / `deerflow` | `demo` 用合成数据，不调用模型；`deerflow` 是真实研究 |
| `data_dir` | `.deerflow/deepresearch` | 路径 | 研究数据库 `research.sqlite3`、检查点 `checkpoints.sqlite3`、日志 `research.log` 的目录 |
| `skills` | 必填 | 见第 4 节 | 研究角色注册表。必须包含 `deepresearch`（规划）和 `report-synthesis`（写作） |
| `sources` | `[]` | 见第 5 节 | 研究来源。`runner: deerflow` 时至少一个 |
| `source_fallback` | `[]` | 来源名列表 | 计划没有指定来源时按这个顺序使用；名字必须在 `sources` 里 |
| `source_priority_file` | `null` | 路径 | 可选的来源优先级文件（YAML 列表）；`source_fallback` 已经足够时不用 |
| `require_dual_source` | `true` | 布尔 | `true` 时必须同时配置 `internal` 和 `external` 两类来源，每个研究单元两类都要查。只有一类来源时设为 `false` |
| `max_concurrency` | 3 | 1–8 | 同时执行的研究单元数，也是同时写作的章节数 |
| `max_active_runs` | 8 | 1–100 | 同时运行的研究任务数，超出时报 `CAPACITY` |
| `plan_countdown_seconds` | 45 | 0.05–600 | 计划卡倒计时秒数，到时自动开始 |
| `max_output_tokens` | 4096 | 128–393216 | 研究角色单次输出上限；实际取它和模型 `max_tokens` 中较小的值 |
| `output_retries` | 2 | 0–4 | 把研究员回答整理成数据失败时的重试次数 |
| `extraction_model` | `null` | 模型名 | 整理数据用的模型；`null` 时用角色自己的模型 |
| `native_tools` | `null` | 工具名列表 | `null` 表示研究角色可以用宿主的全部工具；写成列表可以进一步收紧 |
| `trace_capture_content` | `true` | 布尔 | 是否在本地 Trace 里记录输入输出内容（已脱敏） |
| `trace_max_chars` | 16000 | 1000–100000 | Trace 里单条内容的最大字符数 |
| `allow_limited_report` | `true` | 布尔 | 补研后仍有缺口但已有可引用证据时，照样写报告并说明局限。`false` 时需要用户手动同意 |
| `cite_search_results` | `false` | 布尔 | 搜索结果摘要能否作为引用。只有搜索工具返回完整文档时才设为 `true` |
| `max_synthesis_repairs` | 1 | 0–3 | 报告章节引用有问题时的修复次数 |
| `max_report_sections` | 8 | 2–12 | 报告最多章节数 |
| `favicons` | `true` | 布尔 | 网关是否代取被引用网站的图标。**离线必须设为 `false`** |
| `pricing` | `{}` | 见第 7 节 | 模型单价，只用于估算费用 |
| `budget_ceiling` | 见第 6 节 | 见第 6 节 | 部署允许的最大研究预算 |
| `local_secret_env` | `{}` | 映射 | 本地开发用：凭据名 → 环境变量**名**（不是凭据值） |
| `request_secret_headers` | `{}` | 映射 | 生产用：请求头名 → 凭据名，把专用请求头传给 MCP |
| `access_policy` | `null` | `模块:函数` | 可选的企业权限检查函数 |
| `runner_factory` | `null` | `模块:函数` | 可选的自定义 Runner，一般不用 |
| `tool_timeout_seconds`、`tool_retries` | 45、1 | — | 旧版字段，当前不生效。工具超时在工具自己的配置里设置（例如 `knowledge_search` 的 `timeout`） |

## 4. `skills`：研究角色注册表

每一项的键是 Skill 名。计划里的研究单元用这个名字指定角色。

```yaml
skills:
  technical-route:
    agent: technical-researcher        # config.yaml 里 subagents.custom_agents 的名字
    path: examples/deepresearch/skills/technical-route/SKILL.md
    description: 技术路线、工程约束与取舍
    model: local-model                 # 可选
    timeout_seconds: 1800              # 可选
```

| 字段 | 默认值 | 范围 | 说明 |
| --- | --- | --- | --- |
| `agent` | 必填 | 字母、数字、`-`、`_` | 执行这个角色的 Custom Agent 名 |
| `path` | 必填 | 路径 | 方法论文件，内容会进入角色提示词和配置指纹；不能超过 10 万字符 |
| `description` | 必填 | 文本 | 给规划模型看的角色说明，决定规划时选不选这个角色 |
| `model` | `null` | 模型名 | 覆盖 Custom Agent 的模型 |
| `system_prompt` | `""` | 文本 | 额外追加的角色提示词 |
| `max_turns` | `null` | 1–1000 | 收紧 Custom Agent 的 `max_turns`；`null` 时继承 |
| `timeout_seconds` | 600 | 5–1800 | 单次执行超时；实际取它和 Custom Agent 的 `timeout_seconds` 中较小的值 |

- `deepresearch` 和 `report-synthesis` 是固定角色，规划时不会被当成研究员。
- 其余角色都是研究员。
- 新增研究角度的方法见 [EXTENDING.md](EXTENDING.md)。

## 5. `sources`：研究来源

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `name` | 必填 | 来源名，唯一 |
| `origin` | 必填 | `internal`（内部资料）或 `external`（外部资料） |
| `kind` | `mcp` | `mcp`：MCP 服务的工具；`native`：`config.yaml` 里的原生工具 |
| `server` | — | `kind: mcp` 时必填，是 MCP 服务名；`kind: native` 时不能填 |
| `tool` | 必填 | 宿主里的**精确**工具名。MCP 工具是 `服务名_工具原名` |
| `role` | `data` | `search`：搜索结果只用来发现来源，不能引用；`read`：打开文档的工具，读到的内容可以引用；`data`：返回可引用记录的工具（例如知识库） |
| `level` | `L4` | `L1`–`L4`，来源等级，`L1` 最权威 |
| `priority` | 100 | 来源排序依次看：计划里指定的来源顺序、`level`（`L1` 在前）、`priority`（数字小的在前） |
| `publisher` | `unclassified` | 发布方分类，显示在引用信息里 |

旧版字段 `query_arg`、`fixed_args`、`results_path`、`response_mode`、`fields` 不再生效，可以删掉。

引用能不能生效，还要看工具本身：

- 只有 Jina 版本的 `web_fetch` 会附带网页的地址和标题，引用才显示为网页链接。
- 其他 `read` 或 `data` 工具返回的内容也能被引用，但引用显示为工具记录。

离线部署的推荐写法：

```yaml
require_dual_source: false
sources:
  - name: internal-knowledge
    kind: native
    tool: knowledge_search
    role: data
    origin: internal
    level: L1
    publisher: your-organization
    priority: 10
source_fallback: [internal-knowledge]
```

只有一类来源时，规划模型（尤其是较弱的模型）可能仍然要求“内部加外部”。当 `require_dual_source` 为 `false` 时，
系统在规划后会自动去掉没有工具可用的来源要求（`Settings.fit_origins`），避免研究单元报 `TOOL_DENIED`。

## 6. `budget_ceiling`：研究预算上限

| 字段 | 默认值 | 范围 | 说明 |
| --- | --- | --- | --- |
| `max_iterations` | 2 | 0–8 | 补研轮数 |
| `max_units` | 12 | 1–64 | 一轮最多研究单元数 |
| `max_tool_calls` | 60 | 2–1000，或 `null` | 一个任务的工具调用总数 |
| `max_elapsed_seconds` | 900 | 10–14400，或 `null` | 一个任务的执行总时长（不含等待确认的时间） |
| `max_model_tokens` | 120000 | 1000–2000000，或 `null` | 一个任务的模型 Token 总数 |

- `null` 表示不限。
- 页面新建任务时，请求的预算就等于这个上限。
- 用接口新建任务时，`budget` 的每一项都不能超过上限，否则报 `BUDGET_LIMIT`。上限不是 `null` 时，请求也不能写 `null`。
- 模型调用前会先预留“估算输入 + 单次输出上限”的额度，结束后按实际用量结算。
  所以 `max_model_tokens` 太小会直接 `BUDGET_EXHAUSTED`。至少要留出 `max_output_tokens` 乘以 `max_concurrency` 的若干倍。
- 本地模型不花钱但很慢，可以给大一些的 Token 上限，同时用 `max_units`、`max_iterations` 控制时长。

## 7. `pricing`：费用估算

```yaml
pricing:
  local-model:                       # 模型名
    input_per_million: 0.0           # 每百万输入 Token 的价格
    cached_input_per_million: 0.0    # 可选：缓存命中输入的价格，不填时按普通输入计价
    output_per_million: 0.0          # 每百万输出 Token 的价格
    currency: CNY                    # 币种，3 到 8 个字符
```

- 只影响“指标”页签、`/metrics` 接口和 `python -m deepresearch.metrics` 里的费用估算，不是账单。
- 没有配置单价的模型，费用显示为“—”。
- 本地模型可以不配，或者按电费、机器折旧自行折算。

## 8. 离线与弱模型的推荐值

完整文件见 `examples/deepresearch/offline/research.yaml`，这里只列和默认值不同的地方：

| 字段 | 推荐值 | 原因 |
| --- | --- | --- |
| `runner` | `deerflow` | 真实研究 |
| `require_dual_source` | `false` | 离线通常只有内部来源 |
| `favicons` | `false` | 离线不能访问公网网站 |
| `extraction_model` | 最稳的本地模型 | 整理失败会让研究单元失败 |
| `output_retries` | 3 | 弱模型整理数据容易出错 |
| `max_concurrency` | 1–2 | 本地模型服务吞吐有限 |
| `plan_countdown_seconds` | 120 | 给人留出修改计划的时间 |
| `max_output_tokens` | 等于模型 `max_tokens`（例如 8192） | 章节写作需要较长输出 |
| `max_report_sections` | 4–5 | 减少写作调用次数 |
| `skills.*.model` | 本地模型名 | 所有角色先用同一个模型 |
| `skills.*.timeout_seconds` | 1800 | 本地模型慢 |
| `budget_ceiling.max_units` | 4 | 控制总时长 |
| `budget_ceiling.max_iterations` | 1 | 控制总时长 |
| `budget_ceiling.max_elapsed_seconds` | 7200 | 本地模型慢 |
| `budget_ceiling.max_model_tokens` | 2000000 | 本地模型不按 Token 计费 |
