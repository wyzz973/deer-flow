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

设置页不能改的部分（运维专属）：`runner`、`runner_factory`、`data_dir`、`max_active_runs`、`favicons`、`favicon_private_network`、
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
    top_p: null
    timeout_seconds: 600
    max_retries: 2
    stream_usage: null              # 流式返回里带 Token 用量；null=自动（OpenAI 兼容供应商开启）
    max_tokens_param: null          # 输出上限的参数名；null=自动
    session_param: null             # 携带会话 ID 的请求字段（如 prompt_cache_key、user）；null=不发送
    session_header: null            # 携带会话 ID 的请求头（如 x-session-affinity）；null=不发送
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
- 设置页每个模型都有“测试连接”：发一次普通请求、一次工具调用和一次“只返回 JSON”的请求，报告耗时、工具是否被调用、
  JSON 是否可解析、输出速度（token/秒）、是否上报用量。
- **只支持 Chat Completions 协议的模型网关**用 `provider: openai` 加 `base_url` 即可，研究不依赖 JSON mode、
  Responses API 或结构化输出。两个兼容性开关默认按“自动”处理，一般不用写：
  - `stream_usage`：研究的每次调用都是流式的，而 LangChain 的 OpenAI 客户端在设置了 `base_url` 后默认**不再**请求流式用量
    （`stream_options.include_usage`），结果是所有调用都“未上报用量”：Token 与费用指标为空，预算按满额输出上限保守扣减。
    自动=对 `openai` / `vllm` / `deepseek` 开启；网关不认识 `stream_options` 而报错时写 `false`。
  - `max_tokens_param`：OpenAI 把 `max_tokens` 改名为 `max_completion_tokens`，LangChain 的客户端总是发送新名字；
    仍实现早期协议的网关会忽略输出上限（真实运行里 4096 的上限写出了 7414 个 token）或直接拒绝请求。
    自动=对非 OpenAI 官方的 `base_url` 发送 `max_tokens`；网关只认新名字时写 `max_completion_tokens`。
- **会话粘性（`session_param` / `session_header`）**：提示词前缀缓存存在单个推理副本上。网关把请求轮询到多个副本时，
  同一个研究员循环的各轮落在不同副本，缓存永远不命中（`prefix_reuse_ratio` 高而 `cache_read_ratio` 低，见第 9 节）。
  填写后，每个请求都会带上所属会话的摘要 ID（`dr-` 加 48 位十六进制，来自线程标识的哈希，不含用户、任务内容或凭据）：
  研究员/写作角色的一次执行是一个会话，一次直接调用（改写、转换）连同它的重试是一个会话。
  `session_param` 写进请求体（OpenAI 用 `prompt_cache_key`，一些网关按 `user` 哈希），`session_header` 写进请求头
  （如 `x-session-affinity`、`x-session-id`），两者可以同时用。默认都不发送，因为严格的网关会拒绝未知字段；
  只有 OpenAI 官方地址会自动带 `prompt_cache_key`。字段名不能是 `messages`、`model` 等研究自己要发的字段，
  请求头不能是 `Authorization`、`Cookie` 等凭据或传输头。`anthropic` 客户端没有 `extra_body`，只发送请求头。

### 3.1 `nodes`：按节点指定模型与参数

研究链路是 `rewrite → plan → research（并行）→ conversion → outline → section（并行）→ summary`，报告完成后还有
`revision`（改写报告）和 `follow_up`（追问分流）。每个节点都可以单独指定模型和采样参数，没写的字段继承：

```yaml
nodes:
  rewrite:    {enabled: true, temperature: 0.2, max_tokens: 1500}
  plan:       {temperature: 0, max_tokens: 3000}
  research:   {temperature: 0.3, timeout_seconds: 2400}
  conversion: {model: small-strict, temperature: 0, output_retries: 3}
  outline:    {temperature: 0.1}
  section:    {model: strong-writer, temperature: 0.5, max_tokens: 6000}
  summary:    {enabled: true}
```

| 字段 | 说明 |
| --- | --- |
| `enabled` | 只有 `rewrite` 和 `summary` 可以关。关掉 `rewrite`：直接用用户原话规划，修改计划时把改动追加到原请求后面（省一次模型调用）。关掉 `summary`：用大纲的关键结论作为执行摘要（省最后一次长生成） |
| `model` | 研究模型名。模型选择顺序：研究节点是“研究员自己的 `skills.<角色>.model` → `nodes.research.model` → `default_model`”；其余节点是“`nodes.<节点>.model` → 角色模型 → `default_model`”。`rewrite` 和 `conversion` 还会依次回退到旧字段 `rewrite_model`、`extraction_model` |
| `temperature` / `top_p` | 采样参数。输出 JSON 的节点（`rewrite`、`plan`、`conversion`、`outline`、`follow_up`）建议 0–0.3 |
| `max_tokens` | 这个节点单次调用的输出上限；不写用全局 `max_output_tokens` |
| `timeout_seconds` | 这个节点一次执行的超时：研究节点是整个 Agent 循环，`rewrite` / `conversion` 是一次直接调用；不写用角色的 `timeout_seconds` |
| `output_retries` | 输出没通过契约校验时的修复次数；不写用全局 `output_retries` |
| `json_mode` | 向供应商请求 JSON 对象（`response_format`）。只对直接调用的 `rewrite`、`conversion` 生效，且网关要支持；契约始终同时写在提示词里，所以不支持时关掉即可 |
| `extra_body` | 合并进请求体的额外字段（例如 `repetition_penalty`、网关自己的开关），不会覆盖模型自身的 `extra_body` |

节点参数写在该次执行私有的模型副本上，由引擎自己的模型工厂生效，宿主配置和其他节点都不受影响。
“指标”页签的“按节点”表和 `python -m deepresearch.doctor` 的 `nodes` 段会显示每个节点实际生效的模型与参数、
调用数、Token、P50/P95 延迟、被截断次数（输出碰到上限，调大该节点 `max_tokens`）和格式重试次数（降低温度或换模型）。

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
| `timeout_seconds` | 600 | 单次执行超时（5–14400 秒；慢模型要调大）。`nodes.<节点>.timeout_seconds` 优先 |
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
    enabled: true             # false：保留配置但不提供给规划与研究员（设置页上是开关）
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
- **没有任何启用的 `role: read` 数据源时**（例如只有内部检索或知识库 MCP 工具，拿不到原文），检索结果本身就是证据：
  研究员收到的是“研究方法（无原文）”提示词（`prompts.research_records`），不会被要求“先打开页面”；
  每条检索结果单独成为一条可引用证据（带自己的标题和链接）；报告参考资料里这类引用标注“检索摘录，未读取原文”，
  写作模型也被要求对摘录做归因式表述。不需要打开 `cite_search_results`。用户要求“只引用原文”而部署里没有读取工具时，
  研究照常进行，并在局限里写明。
- 只想用一部分数据源时，把其余的写成 `enabled: false`：它们不会出现在规划与研究员的工具里，历史证据仍可引用。
  至少要有一个启用的数据源；`require_dual_source: true` 时启用的数据源要同时覆盖 internal 与 external。
- 供应商类型：
  - 搜索：`tavily`、`serper`、`brave`、`exa`、`bocha`、`searxng`、`jina_search`、`duckduckgo`
  - 阅读：`jina_reader`、`tavily_extract`、`firecrawl`、`direct`（网关直接抓取，含可读性提取与 PDF 解析）
  - 数据：`ragflow`、`lightrag`
  - 通用：`http`（自定义接口，模板变量 `{query}`、`{max_results}`、`{url}`、`{time_range}`）、
    `mcp`（调用 `mcp_servers` 里某个服务的工具，参数按工具 schema 自动对应）
- 失败处理：限流、额度用尽、鉴权失败、超时、网络错误、服务异常、配置缺失 → 该供应商进入冷却，由下一个接替；
  单个页面打不开、无结果、不支持的内容 → 只换下一个供应商重试，不影响该供应商后续调用。
- 设置页每个供应商都能单独“测试这个供应商”，并显示健康状态（成功次数、最近失败原因、冷却剩余时间）。
- 自己能决定 MCP 返回什么形状时，先看 [MCP_RESPONSE_FORMAT.md](MCP_RESPONSE_FORMAT.md)：最小必要格式、会静默丢内容的几条规则、以及要配套怎么声明数据源。
- `kind: mcp` 的数据源直接把某个 MCP 工具原样暴露给研究员（保留它自己的参数 schema，模型看到的返回也不改）。
  证据在执行之后从调用的参数和返回里识别，返回格式不需要统一：
  - `role: read`：参数里的 URL（`url`/`uri`/`link`/`urls` 或任何取值为绝对 URL 的参数）就是被打开的页面，登记为“已读原文”，
    引用带这个地址；标题和正文从返回里找（Markdown、任意字段名的 JSON 信封、只返回部分正文都可以）。按文档 ID 打开的内部文档
    没有地址，引用以 `mcp://<数据源>/<ID>` 标识，同一份文档读几次都是一条引用。少于 40 个字符的返回（`403 Forbidden`）不算页面。
  - `role: search` / `role: data`：结构化返回（JSON）里的每条结果成为一条带标题、链接的记录；没识别全或正文被截断时，整段返回
    仍可作为一条证据引用（标题是“数据源名: 查询词”）。纯文本返回里的链接按“发现的来源”处理。搜索结果是否可引用仍由
    `cite_search_results` 决定；没有任何 `role: read` 的数据源时自动可引用。
  - 两种写法（`kind: mcp` 与 `providers: [{type: mcp, ...}]`）的调用都计入 `max_searches_per_unit` 和 `max_tool_calls`。
    想让搜索用统一的 `query`/`max_results` 参数、并享受供应商故障切换，用后一种。
- 返回格式不需要统一：系统按通用结构识别记录（带 URL 或标题的对象、`Title:/URL:` 文本块、Markdown 链接），
  只有正文没有标题和链接的片段（知识库常见的 `{content, score, doc_id}`）也会逐条成为记录，标题取正文首句；
  完全没有结构的文本整体交给模型。
- `require_dual_source: true` 时必须同时配置 `internal` 与 `external` 两类来源，且每个研究单元两类都要查。
  只有一类来源时设为 `false`：规划后系统会自动去掉没有工具可用的来源要求（`Settings.fit_origins`）。

## 6. `mcp_servers`：研究自己的 MCP 服务

与 DeerFlow 的 `extensions_config.json` 无关。返回格式不固定也没关系：系统会从 JSON、Markdown 链接、
`Title/URL` 文本块或 HTML 里识别标题、链接和正文。

```yaml
mcp_servers:
  internal-kb:
    transport: http            # http | streamable_http | sse | stdio
    url: http://127.0.0.1:9000/mcp
    headers:                     # 值可以整体是引用，也可以在字符串里插值
      Authorization: Bearer ${KB_TOKEN}              # ${环境变量}
      Cookie: sid=${secret:kb-cookie}; lang=zh       # ${secret:名字}
    allowed_tools: [search_docs, search_wiki]        # 工具白名单；null=不限制
    timeout_seconds: 60          # 连接、列工具和 kind: mcp 数据源单次调用的超时
    enabled: true
    description: 内部知识库
```

- `allowed_tools` 是服务器级白名单：数据源（`kind: mcp`）和供应商（`type: mcp`）只能绑定名单里的工具，
  加载配置时检查一次，解析工具时再检查一次；服务器以后新增的工具在写进名单之前研究调不到。
- 鉴权：请求头和环境变量的值可以是 `$ENV_NAME`、`secret:NAME`，或在字符串里插值（`Bearer ${ENV}`、`sid=${secret:NAME}`）。
  每个用户用自己的身份访问时配合 `request_secret_headers`（见“凭据”一节）。插值得到的值同样会从 Trace 和审计里屏蔽。
- 连接失败会说明原因而不是只给异常类名：HTTP 401/403 归为鉴权失败（“服务器拒绝了凭据，请检查请求头、Cookie 或 Token”），
  其余分为超时、无法连接、服务异常；错误文本不会回显请求头。
- `transport: stdio` 时改用 `command` 与 `args`，凭据写在 `env` 里（同样只写引用）。stdio 会在网关主机上启动进程，
  **只能写在配置文件里**，设置页不能新增或修改 stdio 服务，也不能对未在文件里定义的 stdio 服务“列出工具/测试”。
- 设置页可以“连接并列出工具”，勾选进白名单；命令行用 `python -m deepresearch.doctor --probe-mcp`
  连接每个服务、列出工具并检查研究用到的工具是否存在、是否在白名单里。

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
| `research_records` / `conversion_records` | 研究 | 研究步骤没有可以打开原文的读取工具时使用的两条：检索结果与记录就是证据，不要求“先打开页面” |
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
矛盾点与下一步。默认提示词禁止编造原文中没有的网址、引文、数字与日期。

### 提示词缓存（prompt cache）

模型服务（DeepSeek、OpenAI、vLLM/SGLang 的 automatic prefix caching 等）只能复用**从第一个 token 起完全相同的前缀**。
命中的部分更便宜，在自建的慢模型上更重要的是省掉这部分 prefill 时间。研究的请求因此遵守三条规则：

1. **对话只追加，不改写开头。** 引擎默认会在系统提示词后面插入一份"工具回执清单"，并且每一轮重写它，
   于是研究员循环里系统提示词之后的全部内容每一轮都要重新计算（实测输入 Token 命中 4–5%）。研究角色默认不显示这份清单：

   ```yaml
   tool_receipt_ledger: false   # 默认；true 会让研究员循环失去可复用前缀
   ```

   证据一直来自工具消息本身，回执（`r1..rN`、调用状态）在执行结束后从同一批消息推导，所以关闭它不影响证据与引用。
2. **任务载荷从"所有调用都相同"排到"只有这次调用才有"。** 研究步骤的任务依次是研究方法说明、本次研究的请求与约束、
   数据源，然后才是这一步的 `unit`，预算计数排在最后；章节写作把各章共用的内容放在前面，本章的 `section` 放在证据之后。
   追问先发计划与整份报告、再发对话、最后才是这次的问题，所以同一份报告的第二个问题能复用第一次的提示词（实测 4% → 94%）；
   改写报告先发发现与证据；计划把说明、输出契约、角色、数据源这些对一个部署恒定的内容放在最前。
3. **不把每次都变的内容放进系统提示词。** 日期、预算、用户中途的补充都在任务载荷里，不在系统提示词里。

`GET /{id}/metrics` 的 `tokens.prefix_reuse_ratio`（以及 `breakdown.by_node[]` 里的同名字段）是"同一会话里与上一次请求
从头相同的提示词占比"，即前缀缓存能命中的**上限**，与模型服务是否上报缓存无关。把它和 `cache_read_ratio` 放在一起看：

| 可复用前缀 | 缓存命中 | 含义 |
| --- | --- | --- |
| 低 | 低 | 请求的开头在变：有东西改写了对话头部，先查请求构造 |
| 高 | 低 | 请求没问题：模型服务没开前缀缓存，或网关把同一会话的各轮分到了不同副本（需要会话粘性），或两轮间隔超过了缓存有效期 |
| 高 | 高 | 正常 |

并行启动的步骤/章节彼此不能命中对方刚写入的缓存（缓存要等第一个请求算完才可用），所以只有一轮的节点命中率天然偏低，
这不是配置问题。

## 10. 运行参数

| 字段 | 默认值 | 范围 | 说明 |
| --- | --- | --- | --- |
| `runner` | `demo` | `demo` / `deerflow` | `demo` 用合成数据，不调用模型；`deerflow` 是真实研究 |
| `data_dir` | `.deerflow/deepresearch` | 路径 | `research.sqlite3`、`checkpoints.sqlite3`、`research.log` 的目录 |
| `max_concurrency` | 3 | 1–8 | 同时执行的研究单元数 |
| `writer_concurrency` | `null` | 1–16，或 `null` | 同时写作的章节数；`null` 与 `max_concurrency` 相同。网关对长生成的并发更敏感时调小 |
| `plan_min_units` / `plan_max_units` | 3 / 6 | 1–12 / 1–24 | 要求规划模型给出的研究步骤数范围（还受预算 `max_units` 约束）。规划结果不合规（未知角色、未知或已停用的数据源、步骤过多）会带原因重试，最后一次自动修正而不是让研究失败 |
| `max_active_runs` | 8 | 1–100 | 同时运行的研究任务数，超出报 `CAPACITY` |
| `plan_countdown_seconds` | 45 | 0.05–600 | 计划卡倒计时秒数 |
| `max_output_tokens` | 8192 | 128–393216 | 单次输出上限；实际取它和模型 `max_tokens` 的较小值。`nodes.<节点>.max_tokens` 优先 |
| `max_searches_per_unit` | 30 | 1–500，或 `null` | 一个研究单元最多检索多少次（`search`/`data` 类数据源）。打开已找到的网页**不计入**，因为只有读过的原文才能被引用。这个数字会写进研究员的任务里；用完后检索工具返回“不能再检索，读完已找到的页面后写笔记”，**不会**让研究失败。读网页只计入 `budget_ceiling.max_tool_calls`；`null` 表示检索也只受它约束 |
| `max_seconds_per_unit` | `null` | 30–14400，或 `null` | 一个研究单元的软时限：到点后它的检索/读取工具返回“时间用完，请写笔记”，研究员用已有内容收尾；角色超时仍是硬上限 |
| `max_findings_per_unit` | 12 | 1–100 | 一个研究单元交给写作的发现条数。整理时要求模型只保留决定结论的发现，多出的截掉：每条发现都会进入大纲和章节的输入，整理输出过长也是慢模型上最耗时的一步 |
| `supplement_gap_codes` | 全部 | `coverage`、`unsupported`、`missing-internal`、`missing-external`、`date`、`open-questions` 的子集 | 哪些缺口会触发补研；其余缺口直接写进报告局限，空列表=从不补研。`open-questions`（研究员认为还能再查）每个步骤最多触发一轮。慢模型建议只留 `coverage`、`unsupported` |
| `report_time_reserve_seconds` | `null` | 30–7200，或 `null` | 从 `max_elapsed_seconds` 里留给写报告的时间；`null`=上限的 1/4（120–900 秒，且不超过上限的一半） |
| `output_retries` | 2 | 0–4 | 笔记整理失败的重试次数。被输出上限截断的回复会收到“请缩短”的反馈，而不是原样重试 |
| `allow_limited_report` | `true` | 布尔 | 仍有缺口但已有可引用证据时照样写报告并说明局限 |
| `cite_search_results` | `false` | 布尔 | 搜索结果摘要能否作为引用（只有搜索接口返回完整正文时才开） |
| `max_synthesis_repairs` | 1 | 0–3 | 章节引用有问题时的修复次数 |
| `max_report_sections` | 8 | 2–12 | 报告最多章节数 |
| `report_length_scale` | 1.0 | 0.2–3.0 | 报告长度系数：乘到计划的报告风格（brief / standard / detailed）对应的每章与摘要长度目标上，并把目标同时描述成“形态”（几段、至多几张表）。小于 1 时，明显超出上限（1.3 倍）的章节会得到一次“请缩短”的修复。模型并不严格按字数写：同一问题实测 1.0 约 2.9 万字、0.45 约 1.8 万字（ChatGPT 深度研究约 5 千字加表格）。长度只是目标，不会因此失败 |
| `trace_capture_content` | `true` | 布尔 | 总开关：关掉之后一律不记内容（Trace payload、模型审计、工具审计、线级审计都停），只剩计量 |
| `llm_audit` | `true` | 布尔 | 是否保存每次模型调用的完整提示词与返回（“LLM 调用”审计） |
| `tool_audit` | `true` | 布尔 | 是否保存每次工具调用的完整参数、返回与 artifact。关掉后工具调用只剩计量与参数哈希 |
| `wire_audit` | `true` | 布尔 | 是否保存工具背后每一次 MCP 调用与 HTTP 供应商请求：服务器、真实远端工具名、实际发出的参数、状态码、返回体、重试链。凭据一律不记 |
| `audit_max_chars` | 200000 | 1000–2000000 | 单条记录正文的上限。超出时记录显式标注 `truncated`，不静默截断。实测工具返回通常几千字符，整页最多几十万 |
| `audit_retention_days` | `null` | 1–3650 | 保留多少天的运行记录。`null` 为不自动删除；设置后网关启动时清理一次。手动清理见下 |
| `favicons` | `true` | 布尔 | 网关是否代取被引用网站图标。数据源可以在结果里声明自己站点的图标（`logo_url` 与 `url` 同域），由网关取、浏览器不直连。**离线必须 `false`** |
| `favicon_private_network` | `false` | 布尔 | 允许图标代取访问内网地址（内网站点常没有 `/favicon.ico`）。只对**数据源为自己域名声明的**图标生效；打开意味着数据源可以指定一个网关会去取的地址 |
| `source_fallback` | `[]` | 来源名列表 | 计划没指定来源时的默认顺序 |
| `native_tools` | `null` | 工具名列表 | 可选的引擎工具上限（运维字段） |
| `tool_timeout_seconds`、`tool_retries` | 45、1 | — | 旧版字段，当前不生效；超时在供应商的 `timeout_seconds` 里设置 |

### 记录了什么，怎么整包取出来

一次研究的记录分布在 `research.sqlite3` 的十几张表里：`research_run`（运行快照）、`research_profile_snapshot`（设置快照）、
`research_event`（事件与 span）、`research_model_call` + `research_llm_exchange` + `research_llm_blob`（模型调用的计量与完整请求/返回）、
`research_tool_call` + `research_tool_exchange`（工具调用的计量与完整参数/返回/artifact）、`research_wire_call`（每次 MCP 与 HTTP 往返）、
`research_audit_blob`（前两者的正文，按内容去重压缩）、`research_agent_run`、`research_unit`、`research_evidence`、`research_report`。

要读就整包导出成一个 JSONL，正文已还原、文件自包含：

```sh
backend/.venv/bin/python -m deepresearch.logbook export --data-dir <含 research.sqlite3 的目录> --run <id|前缀|页面 URL|thread|latest> --out run.jsonl
# 或走接口：GET /api/deepresearch/{id}/log/export
```

每行一条，`record` 标明类型：`run`、`settings`、`event`、`model_call`、`tool_call`、`wire_call`、`agent_run`、`unit`、`evidence`、`report`。
`tool_call` 把计量行和正文合在一起，`wire_call` 用 `call_id` 指回它所属的工具调用。一次六步研究约 2–20MB，加 `--compact` 去掉与调用记录重复的 span payload。

**凭据永远不在里面**：API key、token、Cookie、`Authorization`、MCP 的 header 与 env 取值都被替换成 `[redacted]`；MCP 服务发现只记 header 的**名字**。

### 清理

代码里没有任何自动删除。磁盘会一直涨（实测一次研究的 LangGraph 检查点约 100–155MB），所以要么配 `audit_retention_days`，要么定期手动清：

```sh
backend/.venv/bin/python -m deepresearch.logbook prune --data-dir <目录> --older-than 30d --dry-run   # 先看会删什么
backend/.venv/bin/python -m deepresearch.logbook prune --data-dir <目录> --older-than 30d             # 删掉并 VACUUM 回收空间
```

## 11. `budget_ceiling`：研究预算上限

| 字段 | 默认值 | 范围 | 说明 |
| --- | --- | --- | --- |
| `max_iterations` | 2 | 0–8 | 补研轮数 |
| `max_units` | 12 | 1–64 | 一轮最多研究单元数 |
| `max_tool_calls` | 60 | 2–1000，或 `null` | 一个任务的工具调用总数 |
| `max_elapsed_seconds` | 900 | 10–14400，或 `null` | 执行总时长（不含等待确认） |
| `max_model_tokens` | 120000 | 1000–2000000，或 `null` | 模型 Token 总数 |

- `null` 表示不限；页面新建任务时请求的预算就等于上限。
- **预算按任务计**：报告完成后的每次追问（回答、改写报告、开启新一轮研究）都是同一会话里的新任务，用量从零开始计，
  上一任务的用量保存在 `usage_history`（指标里的 `budget.earlier_tasks`）。否则研究用掉大半预算后，任何追问都会因为
  `TIME_BUDGET` / `BUDGET_EXHAUSTED` 失败。
- **时间也是“收尾”而不是“到点失败”**：研究在只剩 `report_time_reserve_seconds` 时收尾——每个研究单元开始时拿到
  “工具停止时间”（剩余研究时间减去写笔记的余量，至少留 30 秒）和硬超时（剩余研究时间加一半报告预留），
  见事件 `research.unit.deadline`；到点后检索工具返回停止提示（`research.search.limited`，`scope: time`），
  研究员写出笔记并在局限里说明；剩余时间不足 20 秒时不再启动新单元（`RESEARCH_TIME_SPENT`，该单元降级）、不再补研，
  报告照常生成。整体硬超时是“上限 + 一份报告预留”，只兜住连报告也写不完的情况。
  数据源单次调用的超时也受剩余时间约束，慢工具不会把研究单元拖到硬超时。
- 模型调用前会预留“估算输入 + 单次输出上限”，结束后按实际结算，所以 `max_model_tokens` 太小会直接
  `BUDGET_EXHAUSTED`：至少留出 `max_output_tokens × max_concurrency` 的若干倍。
- `max_tool_calls` 现在很难再直接让研究失败：数据源工具在触达上限前会返回“停止检索”的提示，研究员据此收尾。
  想控制检索量，优先调 `max_searches_per_unit`（每步），`max_tool_calls` 作为整体兜底。
- `max_model_tokens` 同理：系统会预留一部分额度给报告写作（上限的 1/5，至少 60000，最多一半）。研究阶段用尽额度时，
  当前研究单元降级为“未能完成”并写明局限、不再补研，报告仍然会写出来；只有完全没有可引用证据时才失败（`NO_EVIDENCE`）。
- 研究阶段的每次模型调用按“输入 + 4096”预留额度，调用结束后按模型实际上报的用量结算；写报告的调用仍按
  `max_output_tokens` 预留。所以把 `max_output_tokens` 调大（例如 32768）不会让研究阶段过早显得“额度用尽”。
  模型不上报用量时，按完整输出上限记账（宁多勿少）。
- 研究阶段的 token 按研究单元分配：每个单元拿到“研究剩余额度 ÷ 同时进行的单元数”（再留出整理笔记的余量）。
  用到一半时模型会收到“收尾”提醒，用满时引擎停止它的工具调用，让它用已读到的内容写笔记，单元正常完成。
  整理笔记这一步可以动用报告预留。
- 预算要够写报告：一次 3 个研究单元的完整研究，写报告阶段实测约 145k token（8 次调用）。
  `max_model_tokens` 明显低于这个量级时，研究会被压缩得很短，报告写作本身也可能超出预算。

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
  请求头和环境变量的值里还可以插值：`Bearer ${ENV_NAME}`、`sid=${secret:名字}; lang=zh`。
- 设置页（API）能保存什么有硬性边界，和谁登录无关：不能给角色指定 Skill 文件路径（路径会被读取并回显，只能写正文）、
  不能新增 stdio MCP 服务、不能引入自定义模型类、不能在请求头/环境变量里保存明文凭据（键名像凭据而值不是引用会被拒绝）。
  这些只能写在运维的配置文件里。不能编辑设置的用户看到的设置视图里，明文凭据值显示为 `[hidden]`。
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
| `nodes` | JSON 节点温度 0–0.2、`section` 0.5、按需设 `max_tokens` / `timeout_seconds` | 稳定 JSON 输出；很慢的模型可以关掉 `rewrite`、`summary` |
| `supplement_gap_codes` | `[coverage, unsupported]` | 弱模型几乎总会提“还可以再查”，不为它多跑一轮 |
| `plan_min_units` / `plan_max_units` | 2 / 4 | 步骤越少越快 |
| `max_seconds_per_unit`、`report_time_reserve_seconds` | 1800、1200 | 单元到点收尾；给报告留足时间 |
| `max_findings_per_unit` | 10 | 缩短整理输出和章节输入 |
| `max_concurrency` | 1–2 | 本地服务吞吐有限 |
| `plan_countdown_seconds` | 120 | 给人留出改计划的时间 |
| `max_output_tokens` | 等于模型 `max_tokens` | 章节写作需要长输出 |
| `max_report_sections` | 4–5 | 减少写作调用次数 |
| `compaction.trigger_fraction` | 0.5 | 本地模型上下文小，早一点压缩 |
| `skills.*.timeout_seconds` | 1800 | 本地模型慢 |
| `budget_ceiling` | `max_units: 4`、`max_iterations: 1`、`max_elapsed_seconds: 7200`、`max_model_tokens: 2000000` | 控制总时长，本地不按 Token 计费 |
