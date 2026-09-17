# 本地 / 公司 Agent 交接入口

请以 [HANDOFF.md](HANDOFF.md) 为当前迁移与验收清单，以 [ARCHITECTURE.md](ARCHITECTURE.md) 为架构与工作流说明，并阅读
[NATIVE_RUNTIME.md](NATIVE_RUNTIME.md)、[API.md](API.md) 和
[REUSE_AUDIT.md](REUSE_AUDIT.md)。

旧指令中的“配置 MCP 结果字段映射”“扩展 query-only 搜索包装器”已废弃。
模型直接使用原生工具及原始返回，研究结束后才关联原生消息和 receipts。
不要重建 MCP 客户端、Agent loop、身份系统或全局工具注册。
