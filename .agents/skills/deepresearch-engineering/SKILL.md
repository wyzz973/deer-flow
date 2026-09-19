---
name: deepresearch-engineering
description: 接手、开发、调试、测试和审计本仓库的 DeepResearch（深度研究）模块，并根据一次研究的 session（run id、研究页面 URL、thread id）反查生成审计报告：时间花在哪、Token 花在哪、提示词缓存命中、工具与检索表现、补研与报告写作过程、失败根因和对应的调优设置。只要任务涉及 backend/deepresearch、tests/deepresearch、前端 deepresearch 页面、deepresearch.yaml 配置、研究为什么慢/贵/失败/质量差、对比两次研究、验证一次调参是否有效，就使用这个技能，即使用户没有说“审计”二字。Use for any work on the DeepResearch module in this repo - onboarding, development, debugging, testing, and auditing a research run's time, token, cache and tool behaviour from its session id to produce an audit report for verification and tuning.
---

# DeepResearch 工程与审计

DeepResearch 是这个仓库里对标 ChatGPT 深度研究的产品：一条 LangGraph 工作流，把研究角色交给 DeerFlow 引擎的原生子 Agent 执行。它要在**慢的自建模型**和**只有内部 MCP 检索**的环境里可用，所以每个节点都可调，每次模型调用、工具调用都留了记录。

这个技能做两件事：让你几分钟内知道东西在哪、规矩是什么；给你两个脚本，把“一次研究到底发生了什么”从数据库里还原出来。

## 先建立方位

| 你要找的 | 位置 |
| --- | --- |
| 后端模块（工作流、角色执行、指标、审计） | `backend/deepresearch/`，规矩写在其中的 `AGENTS.md`（必读） |
| 后端测试 | 仓库根的 `tests/deepresearch/`（不在 `backend/tests/`） |
| 前端 | `frontend/src/components/deepresearch/`、`frontend/src/core/deepresearch/`，规矩在 `frontend/AGENTS.md` |
| 配置 | `deepresearch.example.yaml`（示例）；字段说明 `docs/deepresearch/RESEARCH_CONFIGURATION.md` |
| 现状、历史决策、踩过的坑 | `docs/deepresearch/HANDOFF.md`（接手时第一份要读的文档） |
| 运行数据 | `<数据目录>/research/research.sqlite3`；本机验收目录在 `.deerflow/deepresearch/live-*/` |
| 接口 | `docs/deepresearch/API.md`，前缀 `/api/deepresearch` |

工作流节点：`rewrite → plan →（用户确认）→ research（并行）→ conversion → 证据合并/缺口检查 →（补研）→ outline → section（并行）→ summary → 引用绑定/终检 → 渲染`，报告完成后有 `follow_up` 与 `revision`。细节见 [references/architecture.md](references/architecture.md)。

## 按任务选路线

- **审计一次研究 / 它为什么慢、贵、失败、质量差 / 调参有没有用** → 下面的“审计工作流”，模板在 [references/audit-report.md](references/audit-report.md)。
- **研究失败或行为异常，要找根因** → [references/debugging.md](references/debugging.md)（错误码表、去哪看、常见故障的定位路径）。
- **要改代码** → [references/development.md](references/development.md)（设计原则、改哪里、哪些文档要同步）。
- **要跑测试或做真实验收** → [references/testing.md](references/testing.md)。
- **要让它更快、更省、更稳** → [references/tuning.md](references/tuning.md)（症状 → 设置）。

读任务需要的那几份：一个“改代码”的任务通常同时需要开发和测试两份。用户只要方案不要代码时（“先别写代码”），交付一份实施计划：要确认的设计决定、要改的文件与执行点、要同步的文档、先写哪些失败的测试、怎么做真实验证、不能违反的规矩。

## 审计工作流

审计的目的不是罗列指标，而是回答：**这次研究的时间和 Token 花得值不值，哪一个设置改了会最有效，改完怎么证明。** 脚本负责事实，你负责判断。

### 1. 定位 session

用户给的可能是 run id、它的前 8 位、研究页面 URL（`…/workspace/deepresearch/<run_id>`）、thread id，或者只说“刚才那次”。脚本都认：

```bash
# 从仓库任意位置，用后端的 Python（脚本要 import deepresearch）
backend/.venv/bin/python .agents/skills/deepresearch-engineering/scripts/audit_run.py --list
```

没有 `backend/.venv` 时用 `uv run --directory backend --no-sync python <脚本绝对路径>`。数据不在本机默认目录时加 `--data-dir <含 research.sqlite3 的目录>`。脚本只读研究数据库，不会影响正在运行的网关。

### 2. 生成事实底稿

```bash
backend/.venv/bin/python .agents/skills/deepresearch-engineering/scripts/audit_run.py \
  --run <id|前缀|URL|thread|latest> [--baseline <调参前的 run>] [--config <含 pricing 的 research.yaml>] [--out <目录>]
```

输出 `audit-<run8>.md`（底稿）和 `audit-<run8>.json`（同样的事实，给程序和对比用），默认写到 `.deerflow/deepresearch/audits/`（git 已忽略；审计会引用私有的研究内容，不要放进会被提交的目录）。底稿的数字来自产品自己的指标代码（`deepresearch.metrics.collect`），和页面“指标”页签一致；脚本另外补了产品没有的逐会话分析：Agent 循环的上下文增长、供应商逐轮缓存核对、前缀断点、最慢调用、各节点输出速度、补研与修复的触发原因。

### 3. 读底稿，先看“发现”，再看它指向的那张表

规则命中的“发现”只是线索。按这个顺序建立判断：

1. **时间**：总时长里模型、工具、排队、等待各占多少。工具秒数大于模型秒数时，换更快的模型没有用，问题在检索供应商。有 `engine-queue` 发现时先处理它：步骤在引擎里排队的时间藏在步骤耗时里，常常是总时长的最大一块。
2. **哪个节点**：节点表里谁的耗时和 Token 最大，通常是 `research`；看它的 P95、截断、重试。
3. **Agent 循环表**：上下文从几千长到几万，每轮整段重发，所以缓存命中率决定成本；“轮间秒”是这一步等工具的时间。
4. **提示词缓存**：先看“可复用前缀”（我们的请求是否只追加），再看“缓存命中”（服务端有没有用上）。前者低是我们的问题，前者高后者低是模型服务的问题。
5. **过程**：补研因为什么触发、哪些步骤被提前收尾、章节修复了几次、结束时还剩什么缺口。

读数时容易错的地方写在 [references/audit-report.md](references/audit-report.md) 的“读数陷阱”，第一次审计前看一遍。

### 4. 对最重要的两三个发现取证

不要只转述规则的结论。打开具体的调用，看模型到底收到了什么、回了什么：

```bash
S=.agents/skills/deepresearch-engineering/scripts
backend/.venv/bin/python $S/show_call.py --run <run> --node section            # 列出某节点的调用
backend/.venv/bin/python $S/show_call.py --run <run> --call <call_id>          # 打开一次调用（默认只显示相对上一轮新增的消息）
backend/.venv/bin/python $S/show_call.py --run <run> --call <call_id> --request  # 导出成 Chat Completions 请求体，可用 curl 重放
backend/.venv/bin/python $S/show_call.py --run <run> --tools [--unit <步骤>]      # 工具调用：状态、耗时、错误类型、每个供应商的尝试
```

典型取证：章节修复多 → 打开修复那次调用，消息末尾的 `validation_errors_to_fix` 写着哪条校验失败；某步没有发现 → 打开它最后一轮和对应的 conversion 调用；输出被截断 → 看 `params.max_tokens` 和 `finish_reason`。

### 5. 写审计报告

按 [references/audit-report.md](references/audit-report.md) 的模板写，默认中文。每个结论都带数字和它的出处（底稿的哪张表、哪个 call_id）；每条建议都落到具体设置和预期变化；没有验证过的推断明确标为推断。报告和底稿放在同一个输出目录；用户指定了文件名或位置就照用户的。你所在的环境不允许写文件时（有些子 Agent 环境如此），把报告全文作为回复返回，并说明底稿的位置。

### 6. 反向验证

调优建议只有跑过才算数。改一个设置（一次只改一类），用同一个问题再跑一次，然后：

```bash
backend/.venv/bin/python $S/audit_run.py --run <新 run> --baseline <旧 run>
```

底稿末尾会多出三张表：**两次运行的设置差异**（先确认到底改了什么——包括提示词默认值的变化）、指标对比、**同一问题的其他已完成运行**（什么都没改时两次运行会差多少；变化小于这个波动就不能算效果，基线也可能恰好是最轻或最重的一次）。两次运行的计划通常不同（步骤数、搜索次数都会变），所以**先比比例**（命中率、每条引用 Token、每次调用耗时、错误率、重做调用占比），再比总量；追问的调用也计入总量。除了“目标指标变好了没有”，还要看**副作用**：重做的调用、截断、章节长度、引用数有没有变差，并打开对应的调用确认原因。把结论写回报告的“验证”一节。怎么发起一次真实研究见 [references/testing.md](references/testing.md)。

## 不要做的事（以及为什么）

- **不要停、杀、重启不是你启动的进程**（8001、3000、3100 端口，Docker 容器）。这台机器上常有用户自己的服务和别的会话的网关；先 `lsof`/`pgrep` 只读确认归属，要重启先问。重启网关会把进行中的研究标为 `PROCESS_INTERRUPTED`。
- **不要提交** `.deerflow/`、`.env`、真实模型或 MCP 配置、运行数据库、凭据，以及 `backend/packages/harness/deerflow/community/aio_sandbox/local_backend.py` 的本地改动（那是用户的）。只在用户要求时 commit 或 push；推送目标分支以 `docs/deepresearch/HANDOFF.md` 第 9 节为准。
- **审计报告里不要出现凭据或整段私有内容**。调用审计在记录时已经去掉已知凭据，但请求头、Cookie、内部文档摘录仍可能敏感；引用时只摘必要的几十个字。
- **不要改 DeerFlow 引擎（`backend/packages/harness/`）来满足研究的需求**。研究通过私有的引擎配置副本使用引擎能力；引擎是上游代码，改了就和上游分叉。
- **不要在代码、目录名或配置里区分“某一种部署”**（不要出现 company、intranet 专用变体）。内网、MCP、网关兼容都是统一能力，用设置表达差异。
- **不要破坏提示词前缀**：不要往运行中的对话前面插消息，不要把每次都变的内容放进系统提示词或载荷开头。原因和规则见 [references/development.md](references/development.md)。
- **指标里的 `null` 是“没有测量”，不是 0**；不要把它算进平均或当成没有问题。
