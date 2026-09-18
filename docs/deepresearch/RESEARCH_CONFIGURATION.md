# 研究配置说明（`deepresearch.local.yaml` 与设置页）

读者：要配置或修改 DeepResearch 研究行为的人或 Agent。

DeepResearch 的配置**独立于 DeerFlow**：研究用哪些模型、哪些搜索/阅读/知识库接口、哪些 MCP 服务、哪些角色、
哪些提示词，全部写在研究自己的配置里。DeerFlow 只提供执行引擎（子 Agent 执行器、模型工厂、沙箱、中间件），
宿主 `config.yaml` 里只需要一行 `plugins` 注册。宿主的 `models`、`tools`、`subagents`、`extensions_config.json`
都不会影响研究。

字段定义在 `backend/deepresearch/config.py`（`Settings`、`ModelSpec`、`SkillSpec`、`SourceSpec`、`ProviderSpec`、
`McpServerSpec`、`CompactionSpec`、`ModelPricing`）、`backend/deepresearch/prompts.py`（`PromptSet`）和
`backend/deepresearch/contracts.py`（`ResearchBudget`）。以代码为准。

## 1. 两个入口：配置文件与设置页

| | 配置文件（运维） | 设置页 `/workspace/deepresearch/settings`（管理员） |
| --- | --- | --- |
| 改什么 | 全部字段 | 模型、角色、提示词、数据源与供应商、MCP 服务、引擎工具、运行参数、上下文压缩、费用单价 |
| 何时生效 | 重启网关 | 立刻生效，之后**新建**的研究使用；进行中的研究不受影响 |
| 存在哪 | `deepresearch.local.yaml` | 研究数据库的 `research_profile` 表（只存与配置文件的差异） |

设置页不能改的部分（运维专属）：`runner`、`runner_factory`、`data_dir`、`max_active_runs`、`favicons`、
`budget_ceiling`、`local_secret_env`、`request_secret_headers`、`access_policy`、`native_tools`、
`source_priority_file`、`trace_max_chars`。改这些要改文件并重启。

- 文件位置：`config.yaml` 的 `plugins` 里 `config_path` 指向的文件，通常是仓库根目录的 `deepresearch.local.yaml`。
- 模板：联网 `deepresearch.example.yaml`；离线 `examples/deepresearch/offline/research.yaml`。
- 相对路径（`data_dir`、`skills.*.path`）按**仓库根目录**解析。
- 检查命令（不调用模型）：

  ```bash
  cd backend
  uv run --no-sync python -m deepresearch.doctor --config ../deepresearch.local.yaml
  uv run --no-sync python -m deepresearch.doctor --config ../deepresearch.local.yaml --probe-model   # 真实调一次模型
  uv run --no-sync python -m deepresearch.doctor --config ../deepresearch.local.yaml --probe-sources # 真实调一次每个供应商
  ```

## 2. 版本与快照：改了配置，旧任务怎么办

- 每次在设置页保存都会产生一个新版本号（`第 N 版`），历史可回滚（设置页“密钥与历史”）。
- 创建研究任务时会保存一份**完整的执行配置快照**（含角色方法论正文），任务全程用这份快照执行。
  所以保存设置不会改变已经在跑或已完成的研究，重启后继续也用原快照。
- 快照不含运维字段（见上表），这些字段总是取当前文件的值。
- 更早期、没有快照的旧任务仍然沿用配置指纹校验：改了文件里的任何字段（`pricing` 除外）或 Skill 文件后，
  这些旧任务继续/重试会报 `CONFIG_CHANGED`。

## 3. `models`：研究模型

研究只用这里声明的模型，与 `config.yaml` 的 `models` 无关；同名时研究模型在私有副本里覆盖宿主模型，宿主配置对象不会被修改。

```yaml
models:
  - name: deepseek-flash            # 其他字段用这个名字引用
    display_name: DeepSeek V4 Flash
    provider: deepseek              # openai | deepseek | vllm | anthropic | custom
    model: deepseek-v4-flash        # 服务端模型名（vLLM 是 --served-model-name）
    base_url: https://api.deepseek.com   # 可选，留空用该类型默认地址
    api_key: $DEEPSEEK_API_KEY      # 只写引用：$环境变量 或 secret:名字
    max_tokens: 8192
    context_window: 128000
    temperature: null
    timeout_seconds: 600
    max_retries: 2
    supports_thinking: true         # 研究总是关闭思考；打开此项才会发送关闭开关
    extra: {}                       # 原样传给模型类，例如 extra_body、default_headers
default_model: deepseek-flash       # 没有单独指定模型的角色都用它
rewrite_model: null                 # 请求改写；null 跟随 default_model
extraction_model: deepseek-flash    # 研究笔记整理；null 跟随角色的模型
```

- `provider: custom` 时必须用 `use` 指定模型类，例如 `use: langchain_openai:ChatOpenAI`。
- `api_key` 只接受引用（`$ENV` 或 `secret:NAME`），不接受明文密钥。解析不到值时创建模型会报
  `MODEL_AUTH_REQUIRED`。`secret:` 的值在设置页“密钥与历史”里保存，写入后接口不再返回。
- `context_window` 会用于上下文压缩阈值和上下文用量显示，建议填写。
- 设置页每个模型都有“测试连接”：发一次普通请求和一次工具调用，报告耗时、工具是否被调用、是否上报用量。

## 4. `skills`：研究角色

键是角色标识，计划里的研究单元用它指定角色。必须包含固定角色 `deepresearch`（规划）和 `report-synthesis`（报告撰写），
其余都是研究员。

```yaml
skills:
  deepresearch:
    path: examples/deepresearch/skills/deepresearch/SKILL.md
    description: 研究问题拆解与计划规范化
  technical-route:
    name: 技术路线研究员
    description: 技术方案、工程约束与取舍      # 规划模型据此分配研究单元
    methodology: |                            # 直接写正文，或用 path 指向 SKILL.md
      ## 方法
      - 先用多个聚焦查询搜索，优先官方与一手资料。
    model: deepseek-flash                     # 可选，默认 default_model
    system_prompt: ""                         # 可选，放在系统提示最前面
    tools: null                               # null=全部数据源+默认引擎工具；列表=白名单
    enabled: true
    max_turns: null
    timeout_seconds: 600
```

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `path` / `methodology` | 二选一 | `path` 读 Skill 文件（YAML 头信息会自动去掉），`methodology` 直接写正文。设置页保存后统一存为正文 |
| `description` | 必填 | 给规划模型看的角色说明 |
| `name` | `""` | 显示名称 |
| `model` | `null` | 角色模型，`null` 用 `default_model` |
| `tools` | `null` | 允许的工具名白名单：数据源的 `tool` 名 + `engine_tools` 里的名字 |
| `enabled` | `true` | 关掉后规划不会再分配给它（至少要留一个启用的研究员） |
| `max_turns` | `null` | 引擎图递归步数上限 |
| `timeout_seconds` | 600 | 单次执行超时 |
| `agent` | `null` | **旧版**：绑定 DeerFlow 子 Agent，继承它的提示词/工具/模型。新配置不要用 |

## 5. `sources`：数据源与供应商故障切换

一个数据源 = 研究员看到的一个工具（`tool`），背后可以挂多个供应商，按顺序尝试，失败自动切换。
模型看到的参数是固定的：`search(query, max_results, time_range)`、`read(url, start_index, max_length, query)`、
`data(query, max_results)`，与供应商返回什么格式无关。

```yaml
sources:
  - name: public-web-search
    tool: web_search          # 模型看到的工具名，部署内唯一
    role: search              # search | read | data
    origin: external          # internal | external
    level: L4                 # L1 最权威
    priority: 100
    publisher: web-search-unclassified
    description: ""           # 给模型的工具说明，留空用该 role 的默认说明
    providers:
      - id: tavily
        type: tavily
        api_key: $TAVILY_API_KEY
      - id: serper
        type: serper
        api_key: $SERPER_API_KEY
      - id: duckduckgo        # 不需要密钥，兜底
        type: duckduckgo
```

- `role` 决定证据能不能被引用：`search` 的结果只用于发现来源（除非 `cite_search_results: true`）；
  `read` 打开的文档正文可以引用；`data` 返回的每条记录都可以单独引用。
- 供应商类型：
  - 搜索：`tavily`、`serper`、`brave`、`exa`、`bocha`、`searxng`、`jina_search`、`duckduckgo`
  - 阅读：`jina_reader`、`tavily_extract`、`firecrawl`、`direct`（网关直接抓取，含可读性提取与 PDF 解析）
  - 数据：`ragflow`、`lightrag`
  - 通用：`http`（自定义接口，模板变量 `{query}`、`{max_results}`、`{url}`、`{time_range}`）、
    `mcp`（调用 `mcp_servers` 里某个服务的工具，参数按工具 schema 自动对应）
- 失败处理：限流、额度用尽、鉴权失败、超时、网络错误、服务异常、配置缺失 → 该供应商进入冷却，由下一个接替；
  单个页面打不开、无结果、不支持的内容 → 只换下一个供应商重试，不影响该供应商后续调用。
- 设置页每个供应商都能单独“测试这个供应商”，并显示健康状态（成功次数、最近失败原因、冷却剩余时间）。
- `kind: mcp` 的数据源直接把某个 MCP 工具原样暴露给研究员（保留它自己的参数 schema），适合企业知识库工具。
- `require_dual_source: true` 时必须同时配置 `internal` 与 `external` 两类来源，且每个研究单元两类都要查。
  只有一类来源时设为 `false`：规划后系统会自动去掉没有工具可用的来源要求（`Settings.fit_origins`）。

## 6. `mcp_servers`：研究自己的 MCP 服务

与 DeerFlow 的 `extensions_config.json` 无关。返回格式不固定也没关系：系统会从 JSON、Markdown 链接、
`Title/URL` 文本块或 HTML 里识别标题、链接和正文。

```yaml
mcp_servers:
  company-kb:
    transport: http            # http | streamable_http | sse | stdio
    url: http://127.0.0.1:9000/mcp
    headers:
      Authorization: secret:company-kb-token
    timeout_seconds: 60
    enabled: true
    description: 公司知识库
```

`transport: stdio` 时改用 `command` 与 `args`，凭据写在 `env` 里（同样只写引用）。
设置页可以“连接并列出工具”，确认工具名与参数。

## 7. `engine_tools`：引擎工具

研究员除数据源外还能使用的 DeerFlow 引擎工具，默认 `["read_file"]`，可选 `ls`、`glob`、`grep`。
宿主的工具列表**不会**被继承（没有 `bash`、浏览器、写文件）。角色可以在自己的 `tools` 白名单里另行指定。

## 8. `prompts`：全部提示词

研究流程发给模型的每一条指令都可以覆盖，默认值在 `backend/deepresearch/prompts.py`：

| 键 | 阶段 | 用途 |
| --- | --- | --- |
| `rewrite` | 改写 | 把对话改写成完整研究请求（对应 ChatGPT 的 `user_query`），修改计划时还要写确认话术 |
| `plan` | 规划 | 生成计划卡：标题、步骤、假设、报告风格、来源限制 |
| `research` / `researcher_output` | 研究 | 研究单元任务说明；进度说明与研究笔记的要求（`{language}` 替换为读者语言） |
| `conversion` / `converter_system` / `converter_retry` | 研究 | 把研究笔记整理成结构化发现、关联证据 ID |
| `outline` / `section` / `summary` / `draft_repair` | 写作 | 大纲、章节正文、执行摘要、引用修复 |
| `revision` / `follow_up` | 对话 | 改写已有报告；判断追问是回答、改写还是新研究 |
| `role_guard` / `schema_output` / `writer_output` / `skill_files` | 通用 | 角色安全约束与输出格式要求 |
| `compaction` | 通用 | 上下文压缩的笔记要求，必须包含 `{messages}` 占位符 |

设置页“提示词”按阶段分组，可搜索、可逐条恢复默认。

## 9. `compaction`：上下文压缩

研究员打开的网页很快会超出模型上下文。超过阈值时，引擎把较早的消息改写成一份笔记（用 `prompts.compaction`），
最近的消息原样保留；**角色的系统提示词和当前任务始终保留**。这是研究自己的策略，与宿主聊天的
`summarization` 阈值无关。

```yaml
compaction:
  enabled: true
  trigger_fraction: 0.6          # 用到模型上下文的 60% 时压缩
  fallback_trigger_tokens: 48000 # 模型没声明 context_window 时用这个绝对值
  keep_fraction: 0.4             # 压缩后原样保留的最近内容，占阈值的比例（按 token）
  max_summary_input_tokens: 24000
  model: null                    # 写摘要的模型，null 跟随角色的模型
```

保留量按 **token** 而不是消息条数计算，而且只占阈值的一部分：一次网页阅读动辄几千 token，如果按条数保留，
压缩后剩下的内容可能仍然超过阈值，于是每一轮都要重新压缩一次（实测每次 45–55 秒），研究会变得极慢。
`keep_fraction` 越小压缩越不频繁，但丢掉的原文越多；默认 0.4 表示压缩后上下文降到阈值的 40%。

压缩笔记要求保留：已确认的发现（含打开过的网址、原文摘录、日期与状态）、已经做过的搜索与阅读、
工具回执 ID、矛盾点与下一步。默认提示词禁止编造原文中没有的网址、引文、数字与日期。

## 10. 运行参数

| 字段 | 默认值 | 范围 | 说明 |
| --- | --- | --- | --- |
| `runner` | `demo` | `demo` / `deerflow` | `demo` 用合成数据，不调用模型；`deerflow` 是真实研究 |
| `data_dir` | `.deerflow/deepresearch` | 路径 | `research.sqlite3`、`checkpoints.sqlite3`、`research.log` 的目录 |
| `max_concurrency` | 3 | 1–8 | 同时执行的研究单元数，也是同时写作的章节数 |
| `max_active_runs` | 8 | 1–100 | 同时运行的研究任务数，超出报 `CAPACITY` |
| `plan_countdown_seconds` | 45 | 0.05–600 | 计划卡倒计时秒数 |
| `max_output_tokens` | 4096 | 128–393216 | 单次输出上限；实际取它和模型 `max_tokens` 的较小值 |
| `output_retries` | 2 | 0–4 | 笔记整理失败的重试次数 |
| `allow_limited_report` | `true` | 布尔 | 仍有缺口但已有可引用证据时照样写报告并说明局限 |
| `cite_search_results` | `false` | 布尔 | 搜索结果摘要能否作为引用（只有搜索接口返回完整正文时才开） |
| `max_synthesis_repairs` | 1 | 0–3 | 章节引用有问题时的修复次数 |
| `max_report_sections` | 8 | 2–12 | 报告最多章节数 |
| `trace_capture_content` | `true` | 布尔 | Trace 是否记录内容（已脱敏） |
| `llm_audit` | `true` | 布尔 | 是否保存每次模型调用的完整提示词与返回（“LLM 调用”审计） |
| `favicons` | `true` | 布尔 | 网关是否代取被引用网站图标。**离线必须 `false`** |
| `source_fallback` | `[]` | 来源名列表 | 计划没指定来源时的默认顺序 |
| `native_tools` | `null` | 工具名列表 | 可选的引擎工具上限（运维字段） |
| `tool_timeout_seconds`、`tool_retries` | 45、1 | — | 旧版字段，当前不生效；超时在供应商的 `timeout_seconds` 里设置 |

## 11. `budget_ceiling`：研究预算上限

| 字段 | 默认值 | 范围 | 说明 |
| --- | --- | --- | --- |
| `max_iterations` | 2 | 0–8 | 补研轮数 |
| `max_units` | 12 | 1–64 | 一轮最多研究单元数 |
| `max_tool_calls` | 60 | 2–1000，或 `null` | 一个任务的工具调用总数 |
| `max_elapsed_seconds` | 900 | 10–14400，或 `null` | 执行总时长（不含等待确认） |
| `max_model_tokens` | 120000 | 1000–2000000，或 `null` | 模型 Token 总数 |

- `null` 表示不限；页面新建任务时请求的预算就等于上限。
- 模型调用前会预留“估算输入 + 单次输出上限”，结束后按实际结算，所以 `max_model_tokens` 太小会直接
  `BUDGET_EXHAUSTED`：至少留出 `max_output_tokens × max_concurrency` 的若干倍。

## 12. `pricing`：费用估算

```yaml
pricing:
  deepseek-flash:
    input_per_million: 0.0
    cached_input_per_million: 0.0   # 可选，不填按普通输入计价
    output_per_million: 0.0
    currency: CNY
```

只影响“指标”页签、`/metrics` 接口和 `python -m deepresearch.metrics` 的费用估算，不是账单；没配单价的模型显示“—”。

## 13. 凭据

- 配置里只写引用：`$环境变量名`（网关进程环境或 `.env`）或 `secret:名字`（设置页保存，只写不读）。
- `local_secret_env`：本地开发用，凭据名 → 环境变量**名**。
- `request_secret_headers`：生产用，请求头名 → 凭据名。每个请求带来的凭据只属于这一次请求：它会覆盖同名的
  `secret:名字`，用于研究自己的 MCP 服务（连接头/环境变量）和 `type: http` 供应商的请求头；
  不同凭据不会共用 MCP 工具发现缓存，值也不会被保存或写进 Trace。
- Trace 与 LLM 审计会自动屏蔽已知的凭据值、`Bearer` 令牌和 URL 里的密钥参数。

## 14. 离线与弱模型的推荐值

完整文件见 `examples/deepresearch/offline/research.yaml`，这里只列和默认值不同的地方：

| 字段 | 推荐值 | 原因 |
| --- | --- | --- |
| `runner` | `deerflow` | 真实研究 |
| `models[0]` | `provider: vllm`，填 `base_url`、`context_window`、`max_tokens` | 本地推理服务 |
| `require_dual_source` | `false` | 离线通常只有内部来源 |
| `favicons` | `false` | 离线不能访问公网 |
| `sources` | 一个 `role: data` 的来源，供应商 `ragflow` 或 `mcp` | 内部知识库 |
| `extraction_model` | 最稳的本地模型 | 整理失败会让研究单元失败 |
| `output_retries` | 3 | 弱模型整理数据容易出错 |
| `max_concurrency` | 1–2 | 本地服务吞吐有限 |
| `plan_countdown_seconds` | 120 | 给人留出改计划的时间 |
| `max_output_tokens` | 等于模型 `max_tokens` | 章节写作需要长输出 |
| `max_report_sections` | 4–5 | 减少写作调用次数 |
| `compaction.trigger_fraction` | 0.5 | 本地模型上下文小，早一点压缩 |
| `skills.*.timeout_seconds` | 1800 | 本地模型慢 |
| `budget_ceiling` | `max_units: 4`、`max_iterations: 1`、`max_elapsed_seconds: 7200`、`max_model_tokens: 2000000` | 控制总时长，本地不按 Token 计费 |
