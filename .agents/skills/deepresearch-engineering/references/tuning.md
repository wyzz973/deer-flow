# 调优：症状 → 设置

所有设置的含义、默认值、范围见 `docs/deepresearch/RESEARCH_CONFIGURATION.md`；大部分可在设置页改（管理员），其余在运维的 research YAML 里。运行会保存开始时的设置快照，所以**改完要新建研究才看得到效果**。一次只改一类，用 `audit_run.py --baseline` 验证。

## 先判断瓶颈在哪

| 底稿里看到 | 瓶颈 | 往哪调 |
| --- | --- | --- |
| 工具秒 > 模型秒，或 Agent 循环的“轮间秒”远大于“模型秒” | 检索与阅读 | “检索”一节 |
| 模型秒占大头，输出速度（tok/s）低 | 模型解码慢 | 少写字：“输出量”一节 |
| 模型秒占大头，输入 Token 巨大、命中率低 | prefill | “上下文与缓存”一节 |
| 底稿有 `engine-queue`：步骤的第一次模型调用比它的开始晚几十秒到几分钟 | 引擎准入排队 | 引擎配置的 `subagent_runtime.max_running`（宿主默认 3）小于研究的并发。调到不小于 `max(max_concurrency, writer_concurrency)` 并重启网关；等过 `queue_timeout_seconds`（默认 300）的角色会直接失败。这段等待不在“排队秒”里。`python -m deepresearch.doctor` 会报告不匹配；验收启动器会自动抬高它 |
| 排队秒高（研究侧） | 并发不够 | `max_concurrency`、`writer_concurrency`（模型服务与检索供应商扛得住才调；并发越高，免费搜索越容易被限流） |
| 步骤多、补研多 | 工作量本身 | “工作量”一节。补研值不值看底稿的 `supplement-yield`：补研轮的时间占比对比它带来的被引用来源占比 |
| `failed-tool-time` 里“各步骤合计多等了 N 秒” | 失败的工具调用（多数是超时）拖住了整轮 | “检索”一节；底稿给出去掉它们后每轮大约多久 |
| `findings-capped`：多数步骤的发现数等于 `max_findings_per_unit` | 交给写作的结论被上限截断 | “逐项对比”类请求调大 `max_findings_per_unit`，或在计划里把步骤拆细 |
| 重试、修复、截断多 | 返工 | “返工”一节 |

## 工作量

| 设置 | 作用 |
| --- | --- |
| `plan_min_units` / `plan_max_units` | 研究步骤数。慢模型 3–4 步 |
| `supplement_gap_codes` | 哪些缺口触发补研。慢模型 `[coverage, unsupported]`；`[]` 为从不补研，缺口写进报告局限 |
| 预算 `max_iterations`（部署上限 `budget_ceiling.max_iterations`） | 补研轮数上限，0 为不补 |
| `max_searches_per_unit` | 每步搜索次数；直接决定上下文长到多大 |
| `max_seconds_per_unit`、`nodes.research.timeout_seconds` | 单步时间上限；到时按已读内容收尾 |
| `max_findings_per_unit` | 每步交给写作的发现数；影响 conversion 输出和每章输入 |
| `require_dual_source` | 只有一种来源（例如只有内部 MCP）时必须关，否则每步都报缺口且永远补不上 |
| `allow_limited_report` | 缺口补不上时带局限出报告（默认开） |

## 输出量（解码慢时最有效）

| 设置 | 作用 |
| --- | --- |
| `report_length_scale` | 报告篇幅系数，0.5 约减半 |
| `max_report_sections` | 章节数上限 |
| `nodes.summary.enabled: false` | 不单独写执行摘要，用大纲的核心结论代替（省一次长输出） |
| `nodes.rewrite.enabled: false` | 不改写请求 |
| `nodes.<节点>.max_tokens` | 单次输出上限。太小会截断（更贵的返工），按底稿里该节点的平均输出留 1.5–2 倍余量 |
| `nodes.<节点>.thinking` | 该节点是否开模型思考。默认全关：检索一轮多半是工具调用，思考的代价主要是等待和 reasoning token。只在底稿显示某个节点质量不够（章节反复修复、大纲结构差）时单独开，且模型要 `supports_thinking: true` |

## 上下文与缓存

- 研究循环每轮重发整个上下文，输入 Token ≈ 轮数 × 平均上下文。降低它的办法是少搜（`max_searches_per_unit`）、早压缩，或让缓存生效。
- `compaction.trigger_fraction` / `keep_fraction`：何时压缩、压缩后保留多少。模型必须声明 `context_window`，否则用 `fallback_trigger_tokens`。压缩本身是一次长输入调用，且让该会话缓存失效一轮，不要太频繁。
- 缓存：底稿里“可复用前缀”高而“缓存命中”低 → 模型服务侧的问题。自建推理确认开启 prefix caching（vLLM `--enable-prefix-caching`；SGLang 默认开）；网关后面有多个副本时，给模型配 `session_param`（如 `prompt_cache_key`、`user`）或 `session_header`（如 `x-session-affinity`），让同一会话落到同一副本。
- “可复用前缀”低 → 我们的请求开头被改写，是代码问题，见 [development.md](development.md) 的提示词缓存规则；`tool_receipt_ledger` 必须保持关闭。

## 检索

- 底稿第 5 节按供应商的表：`attempts` 多而 `answered` 为 0 的供应商要么修好凭据，要么从该数据源的 `providers` 里移除；顺序就是尝试顺序，坏的排在前面等于每次调用先白等一轮。
- 超时在**供应商**上设：`sources[].providers[].timeout_seconds`（留空时按类型取默认：`type: mcp` 跟随所属服务的 `mcp_servers.<名>.call_timeout_seconds`，默认 300；其余供应商 30。DuckDuckGo 实现里还会多等约 5 秒，所以失败的搜索是 35 秒一次）。旧字段 `tool_timeout_seconds`、`tool_retries` 已不生效。一轮里并行的工具要等最慢的那个，一次超时就拖住整轮；底稿的“失败的工具调用耗时”给出总代价。
- 失败的搜索同样计入每步的 `max_searches_per_unit`：供应商不稳时，步骤“用完搜索次数”有一部分是超时造成的，先修供应商再考虑调大次数。
- 失败几次的供应商会进入冷却被跳过，它自身耗时不大；代价是流量全落到兜底供应商上。
- 错误类型 `HTTP 429/432`、`quota`：配 key 或降并发；`failing_domains` 里反复失败的站点可以在来源策略里排除。
- 只用内部 MCP：`sources` 里只保留 `kind: mcp` 的数据源，MCP 的 `allowed_tools` 做白名单；没有能打开原文的工具时研究会自动改用“记录即证据”的提示词。
- MCP 的超时分两层：`mcp_servers.<名>.timeout_seconds`（默认 60）只管连接和列工具，`call_timeout_seconds`（默认 300）管一次工具调用的应答，并且会下发给传输层（适配器自己的默认值是 streamable HTTP 30 秒、SSE 建流 5 秒，不设就是它们在掐断慢应答）。内网服务慢就调 `call_timeout_seconds`，不要动前者。

## 返工

| 现象 | 调整 |
| --- | --- |
| `truncated > 0` | 调大该节点 `max_tokens`，或减小输出量 |
| conversion / plan 格式重试多 | `nodes.<节点>.temperature: 0`；直接调用节点（rewrite、conversion）在网关支持时开 `json_mode`；`nodes.conversion.model` 换更守格式的模型 |
| 章节修复多 | 先取证是哪条校验（引用了不存在的证据 ID / 超长 / 写了 URL）；调低 `nodes.section.temperature`，超长类问题配合 `report_length_scale`；`max_synthesis_repairs` 限制修复次数 |
| 删除陈述多 | 写作模型在编造证据 ID，换模型或降温 |

## 按节点选模型

`nodes.<节点>.model` 指向 `models` 里的名字。常见分法：plan / conversion / outline 用守格式、快的模型，温度 0；section / summary 用文笔好的模型；research 用工具调用稳定、上下文窗口大的模型。研究角色自己的 `model` 优先于 `nodes.research.model`。

## 成本

在部署的私有配置里填 `pricing`（含 `cached_input_per_million`），底稿和指标页才有费用；不要把真实单价写进示例配置。比较成本用“每条引用 Token / 费用”，不要用总量。
