# DeepResearch 结构速览

权威说明在 `backend/deepresearch/AGENTS.md` 和 `docs/deepresearch/ARCHITECTURE.md`。这一页只给你建立心智模型用，细节以那两份为准。

## 一次研究怎么走

```
用户消息
  └ rewrite        直接调用，把对话改写成完整的研究请求（可关）
  └ plan           规划角色，产出 3–6 个研究步骤（unit），每步指定角色与数据源
  └ plan_review    等用户确认；服务端倒计时自动开始
  └ dispatch       并行执行研究步骤（max_concurrency）
       research    每步一个原生子 Agent 循环：搜索 → 阅读 → 笔记；上下文逐轮增长
       conversion  直接调用，把笔记整理成带证据引用的结构化发现
  └ evidence_merge 程序合并证据池、去重
  └ validator      程序检查 6 类缺口；需要且允许时 → supplement（补研）→ 回到 dispatch
  └ synthesis      outline（一次）→ section（并行，writer_concurrency）→ summary（一次，读全部章节）→ 程序拼接
  └ citation_binder / final_validator   [[E012]] 标记绑定成 [1][2]，核对每个引用；不通过触发有限次重写
  └ renderer       发布报告
报告之后：follow_up（追问分流与回答）、revision（按要求改写报告）
```

可调的“节点”就是上面带模型调用的九个：`rewrite plan research conversion outline section summary revision follow_up`（`config.NODE_NAMES`）。指标、审计记录里的 `config_node` 就是它。

两种模型调用方式，审计时要分清：

- **Agent 循环**（`purpose=agent`）：`native.execute_role` 把角色交给引擎的 `SubagentExecutor`，有工具、多轮、`execution_id` 相同。plan、research、outline、section、summary、revision、follow_up 都走这里（后几个通常只有一轮）。
- **直接调用**（`purpose=rewrite|conversion`）：`structured._contract_call`，单次请求加有限次带反馈的重试，没有工具。

输出契约不依赖供应商的 JSON mode：schema 写在提示词里，`output.parse_contract` 从普通回复里解析，失败才走 conversion 重试。

## 模块地图（`backend/deepresearch/`）

| 关注点 | 模块 |
| --- | --- |
| 控制流与状态机 | `workflow.py`（LangGraph 图）、`service.py`（生命周期、准入、恢复）、`conversation.py`（消息、计划确认、追问、中途补充） |
| 执行 | `runner.py`（各节点的载荷与调用）、`native.py`（对接引擎子 Agent、私有引擎配置）、`structured.py`（契约与直接调用）、`models.py`（研究模型 → 引擎模型、节点参数、会话粘性） |
| 配置 | `config.py`（全部设置与校验）、`profile.py`（设置页可改的部分、快照、指纹）、`prompts.py`（所有提示词默认值）、`catalog.py`、`secrets.py` |
| 数据源 | `channels.py`（内置 web_search/web_fetch 与供应商故障切换）、`providers.py`、`mcp.py`（研究自己的 MCP，白名单、鉴权）、`extract.py`、`sources.py` |
| 证据与报告 | `observations.py`（工具消息 → 证据）、`evidence.py`、`validators.py`（缺口）、`report.py`（Markdown 报告、引用标记、拼接、校验）、`report_policy.py` |
| 观测 | `trace.py`（span 与模型/工具回调，所有计量从这里来）、`metrics.py`（汇总与 CLI）、`audit.py`（完整请求/响应审计）、`activity.py`（用户可见时间线） |
| 存储与接口 | `store.py`（SQLite）、`api.py`、`contracts.py` |
| 运维工具 | `doctor.py`（配置与连通性体检）、`live.py`（本机真实验收启动器）、`demo.py`（合成数据演示后端） |

## 数据都在哪（`research.sqlite3`）

每张表基本是 `run_id + id + body(JSON)`。审计最常用的：

| 表 | 内容 |
| --- | --- |
| `research_run` | 运行快照：query、status、error、plan、units、unit_statuses、budget、usage、usage_history、gaps、conversation、report、profile（设置快照的 hash） |
| `research_event` | 持久事件流（SSE 的来源）：`plan.created`、`research.unit.started/completed`、`validator.gap_found`、`report.section.*`、`report.draft.repair`、`research.output.retry`、`research.search.limited` … |
| `research_model_call` | 每次模型调用的计量：节点、步骤、execution_id、输入/输出/缓存 Token、耗时、finish_reason、prompt_chars、prefix_chars |
| `research_tool_call` | 每次工具调用：工具、角色、耗时、状态、error_type、request_key（脱敏参数的哈希）、供应商尝试与切换 |
| `research_agent_run` | 每次子 Agent 执行：状态、stop_reason、调用数、Token |
| `research_llm_exchange` + `research_llm_blob` | 完整的请求与响应；消息按哈希去重压缩存储，`message_hashes` 记录每次请求的消息序列 |
| `research_unit` | 每个步骤的结构化结果（findings、raw_evidences、open_questions、limitations） |
| `research_profile_snapshot` | 运行开始时的设置快照（运行不受之后的设置修改影响） |

用产品代码读（`Store`、`metrics.collect`、`store.llm_exchange`），不要自己拼 SQL 解释业务含义；技能里的两个脚本就是这样做的。

## 和引擎的边界

- 研究通过**私有的引擎配置副本**使用引擎（`models.private_config`、`native.model_budget_config`）：研究模型、输出上限、节点采样参数、压缩策略、Token 预算份额、关闭工具回执清单，都写在这份副本上；宿主配置不被修改。
- 研究不继承宿主的工具列表：只有本次运行的数据源工具，加上 `engine_tools` 允许的引擎工具。
- 有一项研究改不了、必须在**引擎配置**里设：`subagent_runtime.max_running`（同时运行的原生角色数，启动时读取）。它小于研究的 `max_concurrency` / `writer_concurrency` 时，多出来的角色在引擎里排队，研究侧的指标看不到。常规部署在宿主 `config.yaml`；验收目录里是 `research.yaml` 旁边的 `host.yaml`（由启动器生成）。
- 引擎代码（`backend/packages/harness/`）是上游，不为研究而改。
