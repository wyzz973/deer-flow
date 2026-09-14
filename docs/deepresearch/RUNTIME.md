# 运行时、接口与生产边界

## 工作流与职责

`planner → plan_review(interrupt) → dispatch → evidence_merge → validator → supplement/dispatch → synthesis → citation_binder → final_validator → renderer`

Planner 与 Synthesizer 无检索工具。研究角色读取现有 Custom Agent 的角色配置；source_tools 在 discovery 阶段已裁剪为指定服务器，调用阶段进一步按 Agent/Skill 的 tool 白名单收紧。必需 internal/external 在模型分析之前并行检索，不能以来源不可用为由静默换源。

通过设计中的四类契约贯通链路；Skill ID 可扩展。业务快照、LangGraph checkpoint 的职责不同：前者支持 UI/审计/成功结果缓存，后者控制可恢复节点与 interrupt。新工作流用 `dr-<run_id>` 命名独立 thread，不复用或重写普通聊天线程。

## API

| 接口 | 说明 |
|---|---|
| `POST /api/deepresearch` | CreateResearch；支持 Idempotency-Key；返回 202 和 run/thread |
| `GET /api/deepresearch` | 当前用户最近任务 |
| `GET /api/deepresearch/capabilities` | 动态 Skill/来源/预算目录；不包含凭据 |
| `GET /api/deepresearch/{id}` | 当前用户任务快照 |
| `POST .../plan/approve` | `{"plan_version":1}`，旧版本返回 409 |
| `POST .../plan/edit` | `{"plan_version":1,"plan":{...}}`；规范化后再次等待确认 |
| `POST .../plan/reject` | 拒绝计划并结束 |
| `POST .../cancel` | 取消活动任务，取消态不允许 retry |
| `POST .../retry` | 仅恢复 recoverable 的 FAILED；不重置预算 |
| `GET .../events` | SSE；持久事件序列；支持 after、Last-Event-ID；心跳与禁止代理缓冲 |
| `GET .../evidences` | owner/ACL 校验后的 Evidence Pool 与 raw→evidence lineage |
| `GET .../report?format=json\|md\|html\|docx` | 完成态报告；导出复用同一 AST/引用映射 |

同一个创建幂等键不能绑定不同请求。内部 sources/headers 由部署配置给出，HTTP 客户端不能注册新的 MCP 地址、Skill 路径或 Python 代码。

反向代理需要把 `/api/deepresearch` 转发到 Gateway（不是 LangGraph Server），关闭 SSE 缓冲，并为长连接配置适当 read timeout。同源生产前端复用宿主 CSRF Cookie `csrf_token` 和头 `X-CSRF-Token`。

## 缓存、恢复与取消

SQLite 表：research_run、research_unit、research_evidence、research_gap、research_report、research_event，另有 research_cache。保存成功单元时先落业务库再进入下一节点，恢复 dispatch 时跳过同 input_hash 的成功单元。研究工具按 run/unit/source/args 保存**成功**响应缓存；失败只重试当前调用，已成功的另一来源不重打。

恢复并非对远程调用 exactly-once：如果下游已成功、进程却在本地记录前被杀，重启可能再次调用。因此 MCP 检索应只读；有副作用的工具需要下游幂等键，不能直接套用本实现。

服务重启后将执行中任务标记为可恢复失败，不自动使用过期/持久化凭据调用内部系统；等待用户确认的计划不变。取消会向活动 asyncio 任务传播，已完成报告不会被取消覆盖。

## 预算

max_iterations/max_units/max_tool_calls 为代码约束；工具调用在并发前事务预留。时间预算累计实际执行片段，不计算等待用户审批的时间，恢复不重置。

`usage.model_tokens` 是**保守准入预算单位**：请求文本 UTF-8 字节数、工具 schema 大小及 max_output_tokens 的预留，不是账单 token。`reported_model_tokens` 是提供方有返回时的统计。部分提供方会忽略输出上限（宿主 Codex 适配器也会调整该参数），因此不宣称所有提供方都能做到完全精确、绝不超量的 token 硬限。要求计费硬限时应同时在模型网关实施配额。默认 120000 对较大的中文材料可能过严，应按输入长度配置，而不是删除预算检查。

补研有轮次、单元、重复缺口且无新增证据等停止条件。默认仍有缺口就失败、不生成伪完整报告；显式 allow_limited_report=true 才生成带 limitations 的报告。

## 校验含义

已实现：契约、DAG、引用存在性、原始目标 ID 的结构覆盖、有效内外源覆盖、日期筛选、由配置定义的独立发布方、高风险结论、无手写编号/URL/HTML 的正文、确定性渲染。

高风险结论补证时应复用原结论文本，便于规则合并其证据引用；不能仅凭两个不同表述“语义上可能相同”就判定通过。

未把结构规则包装成语义证明：目标 ID 覆盖不等于真正回答目标；Evidence 存在不等于支持该句；合法 URL 不等于在线可达；来源独立性也依赖可信分类。当前不内置外部 URL 探活，以免绕过自定义 MCP 内容获取边界。RACE/FACT 类语义质量指标需要真实基线、人工/独立评审，不能从代码测试推断 0.90 等质量分数。

## 权限和生产扩展

宿主入口使用 `resolve_principal(request)`；run、报告、证据、SSE 按 owner 隔离，跨用户返回 404。`access_policy: company.acl:can_read_research` 可在读取时检查源文档权限撤销，签名可同步或异步 `(request, run_dict) -> bool`。没有此 hook 时仅保证研究任务 owner 隔离，不宣称自动获得企业源文档的实时 ACL 撤销能力。

凭据只在本次 Runtime.context，后台任务结束清空；不放入 config/configurable/state。模块禁用自动模型 tracing，使用无 prompt/secret 的事件审计。生产自定义 interceptor、提供方 SDK 日志也须自行验证脱敏，不应打开 HTTP header 明文调试日志。

单进程本地锁拒绝第二个共享数据目录的 worker。禁止多个 Pod 各挂独立本地 SQLite 再用轮询负载均衡，否则请求找不到同一研究任务。多实例演进需要共享数据库/checkpointer、持久队列、lease/心跳/抢占、统一配额、用户授权重新获取；这些属于 Phase 3，不在本次基线中冒充实现。

raw_content_ref 保留存储接口，当前只持久化摘要和定位元数据，没有自建原文对象存储。前端引用抽屉展示受当前任务权限保护的摘录，内部 locator 不生成公开下载链接。
