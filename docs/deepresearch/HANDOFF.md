# 公司 Agent 交接：将 DeepResearch 作为 DeerFlow feature 迁移

目标仓库：`https://github.com/wyzz973/deer-flow`。
开发分支：`feat/deepresearch-v1-fullstack-20260914`；本地分支别名为 `feat/deepresearch`。
完整 feature 相对主线的共同基线是 `0d4925305a6330a3442dcd336ed25750aea87cbd`；此轮改造前的 feature 提交是 `03df0622`。公司若还没有初版研究模块，不能只 cherry-pick 最后的重构提交：应比较完整 feature 分支与共同基线，再按下列范围移植。不要直接覆盖公司的 config.yaml、MCP 配置、Skill 或业务代码。

## 先读

最新状态以 `COMPUTER_USE_2026-09-16.md` 为准：完整真实报告尚未通过，不能把
197 项离线回归或已有原生聊天成功当成整个研究 feature 的发布验收。移植时尤其
保留原生 Skill 读取、递归上限语义、预算预警与证据上下文压缩，不要恢复 Web-only
工具列表或 8/12 步的默认限制。

1. README.deepresearch.md：启用、配置和边界。
2. NATIVE_RUNTIME.md 与 REUSE_AUDIT.md：每一步复用了什么、保留什么、移除了什么。
3. API.md 与 openapi.json：接口及消息契约。
4. VERIFICATION.md：实际验收记录，区分原生单元回归、合成浏览器与真实业务验收。

## 合并范围

- `backend/deepresearch/`：研究领域模块和扩展入口。
- `backend/packages/harness/deerflow/subagents/executor.py`：仅新增可选 request_secrets、execution_callbacks 桥接，及清理；沿用公司原生执行器其他能力。
- `frontend/src/components/deepresearch/`、`frontend/src/core/deepresearch/`、研究页面。
- 工作区侧栏 `workspace-nav-chat-list.tsx` 与 breadcrumb `workspace-container.tsx` 的研究入口。
- 新版共享会话表面 `chat-surface.tsx`、普通 `chat-page.tsx` 的复用接线、`MessageList` 的领域消息渲染入口，以及 `ChatBox` 的扩展侧面板。移植时保留公司原生组件能力，不能复制第二套聊天布局。
- 动态研究会话路由、原生侧栏研究历史、`conversation.py` 的服务端倒计时/消息状态，以及来源/调用关联表。
- 研究测试、相关原生 executor 回归、CI、示例配置和文档。

不要带入开发机原有的 `community/aio_sandbox/local_backend.py` 本地修改。此文件不属于研究 feature。

## 与公司版本对齐的接口

先确认原生 `SubagentExecutor.execute_async/get_background_task_result/request_cancel_background_task/cleanup_background_task` 和配置注册表。
确认 MCP cache 提供带 `get_mcp_source` 元数据的原始工具；若公司版本较旧，适配的是这个**宿主接口**，不是逐个 MCP 的业务返回。
确认原生捕获 ai_messages（包含 ToolMessage）和 tool receipts；缺失的 receipt 要如实标记，不编造日志。
确认扩展 API `extension/resolve_principal`，以及前端 WorkspaceContainer、认证 fetcher 和 UI primitives 的名称。

## 配置迁移

合并 host-config.fragment，注册原有行业/技术/综合 Agent，指向实际 Skill。MCP sources 使用服务名、精确工具名和 origin；原生 sources 使用 `kind: native`、精确工具名和 origin，不指定 server。不要迁移旧返回字段映射。复杂 MCP 参数沿用原始 schema，由 Agent 构造。

MCP 在普通 DeerFlow 中必须先能调用。研究不建立私有 MCP 客户端，不替代公司的 OAuth、SSO、stdio 会话池或 headers_from_context。凭据仅传 runtime context。

`native_tools: null` 沿用宿主候选工具并受 Agent/Skill 限制；按公司策略可收紧。真实模式 runner=deerflow；演示模式绝不可冒充业务研究。

部署仍为单 worker、本地 SQLite。配置/Skill 指纹变化后新建任务，不能假装旧检查点兼容。合并 backend/pyproject.toml 中的 python-docx 依赖和对应锁记录；必要时让公司的包管理器重新解析锁文件，不复制开发机虚拟环境。

## 必须完成的公司环境验收

- 用公司的普通 Chat Completions 模型完成计划审核、研究、综合；不得依赖 JSON mode。
- 使用真实的不同 MCP 参数及返回：纯文本、嵌套对象、内容块；验证模型先直接读取，后置引用不阻断执行。
- 检查内部认证、工具授权与 Skill 策略没有被绕过，跨用户读取/导出被拒绝。
- 验证中途失败、取消、重启恢复、预算和 cap，确认无任意工具副作用重放承诺。
- 验证工作区侧栏、桌面/手机、计划增删、引用 Sheet、日志与 Trace 导出。
- 验证倒计时不随刷新重置、编辑立即暂停、旧计划版本被拒绝、手动/自动批准不会重复执行、重启不复用请求凭据。
- 验证同一输入框修改计划不会生成“计划报告”；解释/改写不重复搜索，新研究轮次不错误复用旧任务缓存，历史报告按自身版本导出。
- 验证来源、调用、最终引用分别计数，正文引用与侧栏片段联动，原生 Markdown 的安全前缀处理未被禁用。
- 对结论做人工内容评审：真实引用存在不代表语义支持、发布日期或独立性已经验证。

运行仓库提供的回归和 CI，再记录真实模型/MCP 验收结果。不得把合成数据测试、静态类型检查或配置检查报告为真实业务通过。

2026-09-15 的会话改造仍在工作区验收阶段，不能沿用旧提交的 CI 作为新代码的证明。当前记录在 `COMPUTER_USE_2026-09-15.md`：188 项后端回归、6 项展示逻辑测试，以及 computer-use 复测；DeepSeek Flash 仅完成 14-token 连通性请求，完整真实研究、最终推送与对应 CI 尚未完成。
