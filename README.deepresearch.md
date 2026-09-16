# DeepResearch：DeerFlow 原生研究模块

在 DeerFlow 工作区侧栏打开 **DeepResearch**，进入 `/workspace/deepresearch`。
用原生对话输入框描述研究需求，计划以消息卡片呈现。默认 45 秒后自动启动；点击编辑立即暂停，通过同一输入框修改同一研究的计划。研究由原生子 Agent 执行，最终输出带可追溯引用的报告。

## 设计原则

- LangGraph 管计划审批、状态、依赖调度与补研。
- DeerFlow `SubagentExecutor` 管 Agent、工具授权、Skill 激活、沙箱、上下文压缩、运行保护和取消。
- MCP 的参数和结果由原生工具链处理，**不要求任何统一搜索返回格式**。模型直接阅读原始 ToolMessage。
- 研究完成后，从原生 ToolMessage 和 receipts 建立报告引用；调用记录不等于语义支持证明。
- 普通 Chat Completions 回复通过独立、有限重试的输出整理进入研究契约，不要求 JSON mode。
- 本地 Trace 与轮转日志支持调查，不依赖 LangSmith 或其他遥测服务。

详细说明：[设计与原生能力复用](docs/deepresearch/NATIVE_RUNTIME.md) · [逐步评审记录](docs/deepresearch/REUSE_AUDIT.md) · [API](docs/deepresearch/API.md) · [公司 Agent 交接](docs/deepresearch/HANDOFF.md)

## 接入已有 DeerFlow

1. 合并 `examples/deepresearch/host-config.fragment.yaml` 到本地 `config.yaml`，保留原模型、其他 Agent 与插件配置。
2. 复制 `deepresearch.example.yaml` 为 `deepresearch.local.yaml`，设置 `runner: deerflow`。
3. 将 Skill registry 的 `agent` 对应到现有 Custom Agent，`path` 指向实际方法论文件。
4. 在 `sources` 选择已配置的原生工具或 MCP 工具。默认 `kind: mcp` 需要服务名与**精确工具名**；`kind: native` 只填宿主精确工具名，不填 `server`。两者都不配置返回字段映射。
5. MCP 连接、stdio/HTTP/SSE、认证及凭据策略沿用宿主；研究模块不建立另一套客户端或推断工具名前缀。
6. 执行 `uv sync` 安装宿主依赖（本分支已声明 DOCX 依赖）。检查后重启 Gateway。
7. 打开工作区侧栏 DeepResearch，新建研究。旧运行配置指纹不兼容时应新建任务。

原生 Agent 和激活 Skill 的工具白名单仍生效。`native_tools: null` 表示使用宿主候选工具；可以额外配置列表收紧权限。研究模块不会绕过原生授权，也不会自动开放嵌套 Agent 或非交互任务中的澄清工具。

Skill registry 默认继承原生 Agent 的 `max_turns`。该宿主字段实际限制图递归步骤，包含中间件节点，不等于模型对话轮数；不要将旧简化工作流的 8/12 轮限制直接移植过来。显式 Skill 限制仍可收紧原生上限。

不要在宣告使用原生 Skill 后删掉 `read_file`：模型需要通过原生机制读取技能文件，而不是用网页抓取工具访问沙箱路径。示例 Planner/Synthesizer 仅允许读取方法论，不允许搜索或写文件；Researcher 默认继承宿主工具，并继续受原生授权与 Skill 策略约束。

模型预算先用离线估算预留输入及输出容量，再按供应商返回的 usage 结算。缺失 usage 或失败请求保留估算费用；实际超额会记录并阻止后续工作。`max_output_tokens` 同时约束研究角色的私有模型配置与输出整理，不改宿主模型配置，也不把 UTF-8 字节数冒充已用 token。

Researcher 使用原生预算预警提前收尾，为其他单元和综合报告留出空间。输出整理只接收紧凑的回执/来源目录，综合报告只读取结论实际引用的证据；完整原始记录仍保留用于审计。最新真实验收状态见 `docs/deepresearch/COMPUTER_USE_2026-09-16.md`，尚未将完整真实报告与最终 CI 标为通过。

默认要求 internal/external 两类来源；可以设置 `require_dual_source: false` 并在计划中选择实际来源类别。模型负责调用顺序和并行机会，没有强制的“两次预搜索”。

已有 `web_search` / `web_fetch` 的部署可参考 `examples/deepresearch/native-web.sources.yaml`，不必为了研究而另建搜索 MCP。该片段不会自动启用服务、配置凭据或扩大 Custom Agent 的工具权限。

## 对话与研究过程

页面复用原生 `ChatSurface`、`MessageList`、`PromptInput` 和 `ChatBox` 面板，不另设研究约束表单或页面内历史列表。约束通过意图理解进入计划；必要澄清也在同一对话中完成。

倒计时由服务端保存，刷新、多标签页不会生成新的启动期限或重复批准。重启进程时暂停尚未批准的计划，不保存和重用旧请求凭据。点击编辑后，计划停留在修改状态，直到提交修改或放弃修改。

缓存计划重新显示时沿用响应接收时的计时基准；断线重连会刷新服务端状态。网络失败后的消息重试保留幂等键，迟到响应不能覆盖新会话。服务不可用时，按钮和键盘提交都会被拦下。移动端从来源返回引用时自动关闭抽屉，避免正文仍被遮挡。

主对话显示计划/进度卡和报告预览；右侧“来源”“活动”展示引用分组、发现的链接、实际 Agent 与工具调用。发现链接、读取原文和报告引用不是同一事实，也不共用同一个计数。正文引用定位到来源片段，历史报告按自身版本导出。

报告完成后的解释直接使用现有内容；改写不重复搜索；需要新证据的追问生成新一轮计划。是否在研究运行中接受更新仍单独设计，当前执行中输入框不会承诺已经应用新要求。

配置检查（不访问模型/MCP）：

```sh
PYTHONPATH=backend python -m deepresearch.doctor --config deepresearch.local.yaml
```

已配置模型的开发机可用隔离的原生 Gateway 验收启动器，不覆盖现有配置或数据库：

```sh
cd backend
uv run --no-sync python -m deepresearch.live --allow-live --model YOUR_CONFIGURED_MODEL --port 8001 --frontend-port 3100
```

它使用真实供应商，会产生调用用量；只监听回环地址，使用宿主开发免登录模式，并将数据放在 `.deerflow/deepresearch/live-*`。`--jina-no-key` 仅让验收进程使用 Jina 原生公共模式，不修改 `.env` 中的密钥。该启动器不代表生产登录/企业 SSO 已验收。

## 凭据

优先使用已经可用的 DeerFlow MCP 认证。需要请求级凭据时，沿用宿主的 `headers_from_context`，通过专用请求头或本地环境变量传递：

```yaml
local_secret_env:
  research_cookie: DEEPRESEARCH_MCP_COOKIE
request_secret_headers:
  X-Research-Cookie: research_cookie
```

这里存的是环境变量名/映射，不是真实凭据。凭据只进入原生 executor 的 runtime context；不要把宿主登录 Cookie 自动转发到另一个业务系统。

## 本地 Trace 与日志

从“活动”进入 Trace，查看时间轴、可搜索的分组记录以及 workflow/node/agent/model/tool/conversion 的父子关联、输入输出、耗时与错误。详细载荷按需读取，普通进度 SSE 不传输完整 trace 内容；支持分页及完整 JSONL 导出。

研究数据目录默认 `.deerflow/deepresearch`：

- `research.sqlite3`：研究数据、事件和本地 Trace。
- `checkpoints.sqlite3`：LangGraph 检查点。
- `research.log`：不含原始 prompt/header 的运行元数据，5 MiB 轮转、3 个备份。

`trace_capture_content: false` 可关闭明细采集；`trace_max_chars` 限制单条载荷长度。已知凭据与敏感字段被脱敏，不采集隐藏推理块。数据仍可能包含业务内容，必须按私有应用数据保护；当前 Trace 无自动 TTL。

## 演示与验收

演示使用合成数据，不调用业务模型/MCP：

```sh
python -m pip install -r backend/deepresearch/requirements.txt
python -m uvicorn deepresearch.demo:app --app-dir backend --host 127.0.0.1 --port 8022
python scripts/pnpm.py dev
```

打开 `/deepresearch-demo`。它与真实工作区共用研究组件，但后端仅接受回环地址。

从 `backend/` 执行回归：

```sh
uv run --no-sync --with python-docx python -m pytest ../tests/deepresearch tests/test_subagent_executor.py -q
DEEPRESEARCH_E2E_FRONTEND_PORT=3100 uv run --no-sync --with python-docx python ../scripts/pnpm.py exec playwright test -c playwright.deepresearch.config.ts
```

浏览器测试使用独立演示数据库和宿主已有的开发免登录模式，覆盖演示页与真实工作区页面、侧栏、移动端、计划编辑/确认、报告/引用、Trace 和下载。权限隔离由后端测试覆盖，不能把开发免登录测试当作真实企业 SSO 验收。

## 当前边界

单进程 SQLite；不支持多实例负载均衡。成功的完整 Agent 执行会缓存，格式修复不重复研究；中途失败的子 Agent 会重启，不自动缓存/重放任意 MCP 调用。工具可能有副作用，不能宣称 exactly-once。

原生子 Agent 的能力不等于全部 Lead 能力。Lead 记忆写入、聊天历史、上传管理没有被暗中移植。原始文档日期、发布方独立性和语义支持由研究内容评审，代码仅确认执行来源和结构覆盖。真实业务验收清单见交接文档。

### Explicit unbounded live acceptance

For an operator-authorized real-provider acceptance, the isolated launcher accepts
`--unlimited-budget --max-output-tokens 393216`. This is not the default.
`--unlimited-budget` disables cumulative model-token, tool-call, and elapsed-time
ceilings in the private test configuration and token budgets on the acceptance
subagents. Usage accounting, local traces, cancellation, unit/iteration bounds,
and native execution timeouts remain active. It does not edit operator files.
The output limit is a per-response maximum, not a target response length or a
context-window setting. Verify the provider's supported limit before using it.
DeepSeek's Chat Completions documentation lists a maximum of 393,216 output tokens:
https://api-docs.deepseek.com/api/create-chat-completion/ .

A code-only acceptance restart can use `--resume-dir /absolute/path/to/live-DIR`
with the original launch options. The launcher reconstitutes process-only
credentials, verifies that the private configuration is unchanged, and retains
completed native executions and checkpoints instead of starting a new study.
For a failed run, an owner can explicitly accept a report with disclosed
limitations through the retry API's `allow_limited_report` field. Reference
validation remains mandatory; this never turns missing evidence into facts.

### Report quality and source reading

Research plans infer report style and citation scope from natural language;
there is no additional source-constraint form. Explicit official-domain/original
reading requirements are applied again before report publication. Native web
reading supports longer excerpts, section lookup and continuation positions
instead of silently discarding everything after 4096 characters. Optional native
page artifacts preserve the actual title, URL and page hash without imposing a
result shape on MCP tools.

Reports lead with a recommendation, support compact comparison tables, separate
facts from inferences and retain disclosed limitations. Multiple excerpts of one
page snapshot share a visible citation number while their individual provenance
remains available. Reading a tool result and validating an ID still do not prove
semantic correctness; important conclusions require content review.

Brief reports also use paragraph- and table-cell-level editing bounds rather than
relying on a whole-report word-count instruction. Historical reports retain their
original storage/export contract. Conversational edits receive the existing
report itself, so a targeted change need not regenerate unrelated sections from
research notes. Provider token settings and cumulative budgets are separate.

### Workflow recovery and auditability

Long model-generated research-unit names no longer become native thread paths;
short stable identities isolate task, round and role, while Trace retains the
complete names. Accepted plan/message commands survive the gap before background
execution begins. Recovery reuses completed research, reconciles its status, and
publishes each report version atomically. Cancellation drains native/SQLite work,
and shutdown rejects new execution. Idempotent requests still obey current source
access policy. Common provider authentication, billing, throttling and timeout
failures are surfaced without echoing raw credential-bearing error bodies.

These safeguards do not make remote tools exactly-once or guarantee factual
correctness. The deployment remains single-worker SQLite, and production identity,
third-party MCP behavior, resource limits and load need deployment-specific tests.

### Recovery and stability acceptance

Failed native runs can resume from their checkpoint without rerunning committed
research units. If bounded supplementary research still leaves evidence gaps,
the existing plan card offers an explicit **generate report with limitations**
action when evidence exists. It does not bypass citation validation.

The [2026-09-16 stability audit](docs/deepresearch/STABILITY_AUDIT_2026-09-16.md)
records the fixes, 361 backend and 18 frontend passing tests, real DeepSeek run
recovery, local trace closure, browser checks, and remaining boundaries.
