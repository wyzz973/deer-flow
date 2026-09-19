# 开发：原则、改哪里、要同步什么

开始前读 `docs/deepresearch/HANDOFF.md`（现状与“容易踩坑的地方”）和 `backend/deepresearch/AGENTS.md`（每个模块的约定，很长，按需搜索）。前端读 `frontend/AGENTS.md` 里 DeepResearch 的部分。这一页是这些文档的索引和最不能违反的几条。

## 产品目标（判断取舍时用）

对标 ChatGPT 深度研究，差别只应在模型；同时要在**慢的自建模型 + 只支持 Chat Completions 的网关 + 只有内部 MCP 检索（常常拿不到原文）**的环境里可用。所以：可用性第一；成本、时间、性能必须可观测；链路上每个节点的模型与参数都能单独调。用户列出的需求是下限，做的时候把它补完整，并用真实运行和浏览器验证。

## 设计原则

1. **研究的配置独立于 DeerFlow**。模型、角色、数据源、MCP、提示词、压缩、预算都在研究自己的配置里（一个 YAML + 设置页覆盖）；DeerFlow 只是执行引擎。不要让研究读宿主的 `config.yaml` 业务设置，也不要改宿主配置对象——用私有副本（`models.private_config`）。
2. **不改引擎**（`backend/packages/harness/`）。引擎能力不够时先看能不能通过私有配置开关解决（这次关闭回执清单就是这样做的）。
3. **不区分部署**。代码、目录、配置里不要出现 company/内网专用的命名或模板；差异用设置表达。
4. **不依赖供应商特性**。不要求 JSON mode、Responses API、结构化输出；契约写在提示词里，从普通回复解析。网关不认识的请求字段一律要显式开启才发送。
5. **凭据只作为引用**（`$ENV`、`secret:NAME`）。不落库、不进日志、不进审计记录、不进报告。按请求传入的凭据只存在于该次执行的上下文里。
6. **观测代码不能让研究失败**。指标、审计写入吞掉自己的异常只记日志；没测量到的返回 `null`，不是 0。
7. **预算是收尾，不是失败。** 检索次数、每步时限、Token 份额用完时，工具返回带 `BUDGET_STOP` 标记的停止提示，让研究员用已读内容写笔记；这条回复永远不是证据。读网页刻意不计入每步检索额度：只有打开过的页面才能被引用，历史上把读取算进去，真实运行 `40e5b85a` 一页没读，以 `NO_EVIDENCE` 失败。新增的限制沿用同样的写法（并发调用先占位再等待、失败退还、每种原因只发一次事件）。
8. **确定性的事用程序做**：证据合并、缺口检查、引用绑定、终检、报告拼接都不调模型；模型不被信任去生成来源元数据。
9. **控制操作要幂等、可恢复**：计划确认、消息、重试都经过同一条受保护的路径；副作用不放在 LangGraph 中断之前；重启后能从检查点继续。

## 提示词缓存规则（改请求构造时必须遵守）

供应商只复用**从第一个 token 起完全相同的前缀**；在慢的自建模型上，命中还意味着省掉这部分 prefill 时间。

1. 运行中的对话只追加：不要插入或改写已有消息，不要在系统提示词后面放每轮变化的内容。`tool_receipt_ledger` 默认关就是因为引擎的回执清单每轮重写对话头部（命中率 4–5% → 88%）。
2. 载荷从“所有调用相同”排到“只有本次调用才有”，每次都变的计数放最后。长的写作任务可以把说明留在末尾（贴近生成位置），其余照此排序。
3. 系统提示词里不放日期、预算、计数、用户中途的补充。
4. 滑动窗口不要每轮移动一条（`runner.recent_messages` 每 10 条才移动窗口头）。

守卫：`tests/deepresearch/test_prompt_cache.py`；运行时看指标里的“可复用前缀”，研究节点应在 80% 以上。

## 常见改动落在哪

**最可靠的办法：找一个最相近的现有设置（或事件、指标），全仓 grep 它，每一处命中都是一个同步点。** 下表是起点，不是全集；例如 `max_searches_per_unit` 会带你走到 `config.py`、`profile.py`、`channels.py`、`runner.py`、`prompts.py`、`mcp.py`、前端四个文件、两份示例配置和六七份文档。

| 要做的事 | 位置 | 同步 |
| --- | --- | --- |
| 新增一个设置 | `config.py`（字段 + 校验 + 说明“为什么”的注释）；设置页可改则加进 `profile.EDITABLE`；**每个新字段都要加进 `profile.LATER_FIELDS`**（否则早于快照的旧运行会报 `CONFIG_CHANGED`）；要随运行快照保存就不要放进 `OPERATOR_ONLY`。**行为类设置还要有执行点**：由工具或程序强制（如 `channels.SearchBudget`、`runner.py`），不能只靠提示词。**默认值不能改变现有行为**：旧快照缺这个字段，恢复时取默认值 | 后端：`deepresearch.example.yaml`（值要等于默认值，验收目录才能恢复）、`examples/deepresearch/offline/research.yaml`、`RESEARCH_CONFIGURATION.md`、`ARCHITECTURE.md` 的设置表、`MODEL_CONFIGURATION.md` 的调优表、`README.deepresearch.md` 的设置页清单、`backend/deepresearch/AGENTS.md`。前端：`types.ts`（新字段标成可选 `?:`，旧网关不返回它）、`settings.ts` 的 `SECTION_FIELDS` 与 `FIELD_LABELS`、`settings/` 下对应分区、`tests/unit/core/deepresearch/fixtures.ts`。本技能：`references/tuning.md`、`audit_run.py` 的 `SETTING_KEYS`。设置字段不在 `openapi.json` 里（设置接口收的是自由结构） |
| 新增或调整节点参数 | `config.NodeSpec`、`models.node_overrides` / `merged_overrides` | 同上 + `catalog.py` |
| 改提示词 | `prompts.py`（默认值；用户可覆盖，运行保存快照） | 写作提示词保留“引用标记必须保留”的明确措辞 |
| 改某节点发给模型的内容 | `runner.py`（载荷）、`structured.py`（契约与直接调用）、`native.py`（系统提示词拼装） | 遵守缓存规则；`test_prompt_cache.py`、`test_runner.py` |
| 新数据源 / 供应商 / MCP | `providers.py`、`channels.py`、`mcp.py`、`extract.py`（异构返回的解析） | `EXTENDING.md`、`examples/deepresearch/` |
| 新指标 | 计量在 `trace.py` 的回调里记录，汇总在 `metrics.py` | `API.md` 的指标表、前端 `types.ts` + `metrics-panel.tsx`、历史数据缺字段返回 `null` 的测试；审计脚本需要时一并更新 |
| 新事件 / 状态 | `workflow.py`、`conversation.py`、`contracts.py` | `activity.py`（用户可见时间线）、前端 `events`、`API.md` |
| 接口 | `api.py` | `API.md`、`openapi.json`、前端 `api.ts` |

## 改完要做的

1. 先写失败的测试，再实现（后端硬性要求）。
2. `ruff check` + `ruff format`（后端）、`pnpm check`（前端），见 [testing.md](testing.md)。
3. 同步文档：用户可见变化 → `README.deepresearch.md` / `docs/deepresearch/*`；开发约定 → 对应的 `AGENTS.md`（不要改 `CLAUDE.md`，它只是导入 `AGENTS.md`）。各级 `AGENTS.md` 有字节软上限（`scripts/check_agent_guidance.py`，`backend/tests/test_agent_guidance_check.py`）：原地改写，不要只追加。
4. 影响研究行为的改动，做一次真实运行并用 `audit_run.py` 留数字；把“做了什么、为什么、验证结果、残余风险”记进 `HANDOFF.md`。
5. 只在用户要求时 commit / push。提交信息说明动机；不要提交运行数据、`.env`、真实配置，以及 `aio_sandbox/local_backend.py` 的用户改动。推送的目标分支看 HANDOFF 第 9 节（本地分支名和远端目标分支不同）。

## 代码风格

读起来像周围的代码：注释解释“为什么”（通常是一次真实故障），不复述代码；函数短、少抽象；后端行宽 240、双引号；前端遵守 `frontend/AGENTS.md`。测试名是一句行为描述。
