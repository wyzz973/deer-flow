# 给本地开发 Agent 的交接指令

基于随附源码包中的实际实现接入我们的本地工程，不要重新设计一套框架。

1. 先读 README.deepresearch.md、RUNTIME.md 和 EXTENDING.md，确认本地 fork 与基线的目录/API 差异；保留原普通聊天、必需工具和 MCP 凭据拦截器。
2. 先跑 demo 的计划编辑/审批/报告/引用/恢复测试，再启用 extension。复用我们已有的 industry/technical Custom Agent 和 Skill，只对齐 registry、输出契约与 source 策略。
3. 配置真实 MCP 服务、精确暴露工具名、入参和结果字段映射；证明 internal/external 是并行互补、失败不回退，并验证 Credential 缺失 deny。不要把 Cookie/Token 写进 prompt、工具参数、日志或代码。
4. 保持 AgentRunner → ResearchResult → Evidence Pool → StructuredReport → Binder/Renderer 边界；SubAgent 不产生全局 Evidence ID、最终编号和报告 Markdown。复杂旧输出用显式 adapter。
5. 本地 Skill 需要文件、计算或脚本工具时，通过自定义 runner 复用宿主执行器及其沙箱/Skill 中间件，不移除其他工具、不裸挂无隔离工具。只新增研究角度时不要修改 Graph/引用算法/前端枚举。
6. 补好你的源文档实时 ACL hook、模型网关预算及来源域名/发布方规则。默认仅是单进程 SQLite，不要在多 worker/多 Pod 上直接宣称生产可用。
7. 交付前执行后端测试、前端 typecheck、浏览器 E2E，再用真实 MCP 跑一组授权研究问题。报告实际结果和未覆盖项，不把 demo 或结构校验当成真实引用准确率验收。

验收至少包含：计划同 run/thread 编辑后恢复；旧版本批准 409；单元失败只重试失败部分；双源有效证据；未知引用拒绝输出；SSE 重放不导致恢复后的界面提前结束；跨用户读取拒绝；凭据不出现在 checkpoint/事件中。
