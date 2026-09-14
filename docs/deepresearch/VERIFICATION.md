# 验证记录

## 本地工具环境已实际执行

- Python `pytest tests/deepresearch -q`：**20 passed，1 skipped**。
- `python -m compileall -q backend/deepresearch`：通过。
- 随附 7 个 TypeScript/TSX 文件通过 TypeScript `transpileModule` 语法检查，0 个语法诊断。**这不等于完整 `tsc --noEmit` 类型检查或浏览器验收**。
- 已运行范围：Pydantic/DAG、URL 处理、MCP 结果映射、来源优先级、证据/lineage、引用顺序、HTML 转义、DOCX 内容、SQLite 幂等和并发预算、owner/Origin 权限、实时 ACL 撤销后历史列表隐藏、真实研究适配器的双源调度与成功缓存（transport/LLM 使用测试替身）。
- skipped 是整个 LangGraph 工作流集成测试模块，当前执行环境未安装 LangGraph/checkpointer，也不能通过 pip 获取其依赖。**不把这些集成用例计作通过。**

## 仓库已提供、需要完整依赖环境执行

- LangGraph：计划中断/编辑/批准、持久化与重启、失败单元恢复、缺口停止、凭据不持久化。
- 前端：完整 TypeScript 检查和真实浏览器计划编辑→报告→引用→下载→刷新恢复。
- CI 工作流 `.github/workflows/deepresearch.yml` 安装依赖后执行以上检查。应以 GitHub Actions 对相应提交的实际运行状态为准；本文件不预先宣称 CI 已通过。

## 不能在无授权数据环境中证明

真实内部 MCP 的认证、具体工具返回格式、内部 ACL 撤销、实际模型结构化输出能力、Semantic Citation Accuracy、真实负载与多租户生产稳定性。需要在你的本地授权环境验收；没有用演示来源或猜测的评测数值替代这些结果。

## GitHub 状态

新分支 `feat/deepresearch-v1-fullstack-20260914` 已创建。批量源码写入被工具安全检查拦截，未产生代码提交，未执行 GitHub Actions。上述 CI 文件已包含在源码包中，但不能据此声称 CI 通过。

## 交付补丁检查

补丁对修改前 `.gitignore` 的 Git blob SHA 已与 GitHub 基线核对一致；在仅含原 `.gitignore` 的本地最小 Git 基线中执行 `git apply --check` 与实际应用，全部源码文件逐字节一致。此检查验证补丁格式和文件内容，不代表已对你当前本地的全部改动做冲突检查。
