# 本地 / 公司 Agent 交接入口

在不联网的机器上继续开发时，从 [OFFLINE_AGENT_GUIDE.md](OFFLINE_AGENT_GUIDE.md) 开始。它包含工作规则、环境检查、运行方式、
改动定位、测试命令和常见错误。配置分三份：[模型配置](MODEL_CONFIGURATION.md)、
[DeerFlow 宿主配置](DEERFLOW_CONFIGURATION.md)、[研究配置](RESEARCH_CONFIGURATION.md)。

当前状态与验收记录见 [HANDOFF.md](HANDOFF.md)，架构与工作流见 [ARCHITECTURE.md](ARCHITECTURE.md)，
原生复用原则见 [NATIVE_RUNTIME.md](NATIVE_RUNTIME.md) 和 [REUSE_AUDIT.md](REUSE_AUDIT.md)，接口见 [API.md](API.md)。

旧指令中的“配置 MCP 结果字段映射”“扩展 query-only 搜索包装器”已废弃。
模型直接使用原生工具及原始返回，研究结束后才关联原生消息和 receipts。
不要重建 MCP 客户端、Agent loop、身份系统或全局工具注册。
