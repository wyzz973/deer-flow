---
name: report-synthesis
description: 只基于 Findings 与 Evidence Pool 生成可确定性渲染的报告 AST。
---
# Report Synthesis
先列研究目标覆盖情况，再组织执行摘要、正文各章节和结论，合并重复观点但保留冲突、适用范围和研究限制。
严格输出 StructuredReport。sections.unit_ids 覆盖原始 ResearchPlan 中每个单元；不得用补研单元 ID 替代原始目标。
以独立事实或紧密相关事实为 Segment。事实用 fact 且必须有支持它的 evidence_ids；分析用 analysis；建议用 recommendation。分析与建议涉及可检验事实时也应绑定证据。
证据只有弱相关、摘要不足、日期不明或相互冲突时，明确表达不确定性，不把其包装为已证实结论。
不搜索、不调用 MCP、不引入 Evidence Pool 之外的新来源。不要输出 Markdown、HTML、URL 或 [1] 引用编号；引用绑定由代码执行。
收到 validation_errors_to_fix 时仅修正相应局部问题，保留其他正确内容。
