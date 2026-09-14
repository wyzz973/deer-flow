# DeepResearch API

Base: `/api/deepresearch`，由 DeerFlow Gateway 扩展路由提供。生产请求使用宿主登录身份及 CSRF；run owner 不匹配返回 404，可选 access_policy 支持读取时 ACL 复核。演示后端仅接受回环请求。

## 接口目录

| Method | Path | 结果 |
|---|---|---|
| GET | `/capabilities` | mode、ready、Skill、来源和部署预算 |
| POST | `/`（实际无尾斜杠亦可） | 202，创建 run/thread；支持 Idempotency-Key |
| GET | `/` | 当前用户最近任务；limit 1–100 |
| GET | `/{id}` | 当前快照、计划、单元状态、预算与报告 |
| POST | `/{id}/plan/approve` | 202，确认指定计划版本 |
| POST | `/{id}/plan/edit` | 202，规范化新计划并重新等待审核 |
| POST | `/{id}/plan/reject` | 202，拒绝计划 |
| POST | `/{id}/cancel` | 取消；已完成报告不会被覆盖 |
| POST | `/{id}/retry` | 202，恢复 recoverable FAILED，预算不重置 |
| GET | `/{id}/events` | SSE，after / Last-Event-ID 持久事件游标 |
| GET | `/{id}/evidences` | 证据目录与 lineage |
| GET | `/{id}/report?format=json\|md\|html\|docx` | 完成态报告/下载 |
| GET | `/{id}/trace?after=0&limit=100` | 本地 trace 分页；limit 1–200 |
| GET | `/{id}/trace/export` | owner/ACL 保护的 JSONL 导出 |

## 创建

```json
{
  "query": "比较研发平台的 AI 化路线",
  "constraints": ["区分现状与建议"],
  "source_names": [],
  "budget": {
    "max_iterations": 2,
    "max_units": 12,
    "max_tool_calls": 60,
    "max_elapsed_seconds": 900,
    "max_model_tokens": 120000
  }
}
```

`Idempotency-Key` 同用户同请求可复用，不同请求体复用返回 409。预算不可超过部署上限。`source_names` 表示优先顺序，不代表放开未注册工具或取消来源要求。模型预算是保守准入单位，reported_model_tokens 才是提供方返回的统计，不承诺精确计费硬限。

## 计划

approve/reject body：`{"plan_version":1}`。

edit body：`{"plan_version":1,"plan":{...}}`；plan 包含 goal、constraints、expected_output、research_units 和 plan_version。单元包含 id、skill、objective、priority、depends_on、source_strategy。依赖必须构成 DAG，Skill/来源必须已注册。保存编辑后重新确认新版本，旧版本返回 409。

## 事件与 Trace

事件包含 `seq/type/run_id/at/data`，SSE id 为 seq。Trace 的 data 另包含 trace_id、span_id、parent_span_id、kind、name、status、duration_ms 及可选 payload/error_type。

trace 分页返回 `{"trace_id":"...","items":[...],"next_cursor":123}`。导出每行一个完整事件。started 无对应 ended 可能是活动调用或进程中断，不能推断成功。载荷可能被截断；设置关闭内容采集后仅保存元数据。

## 证据语义

`E001` 是报告内部 ID，不是外部文档 ID。`provenance=tool_output` 表示真实原生工具调用，`source_uri=mcp-result://...`；原生文件/沙箱等调用为 `origin=runtime`、`tool-result://...`。raw_content_ref 关联 execution/call。receipt/调用证明来源发生过，不自动证明文档日期、独立性或结论正确。前端和导出必须保留该限定。

## 错误

401 未登录；403 ACL 撤销/演示 Origin 拒绝；404 不存在或不属于当前用户；409 计划版本、配置指纹、幂等或状态冲突；422 契约/配置无效；429 容量已满。

后台错误写入 run.error `{code,message,recoverable}` 并发出 run.failed。不可恢复预算/配置/最终校验错误应新建或修正配置后新建任务；不能无限点击 retry。

精确请求 schema 见同目录 `openapi.json`（从实际 FastAPI router 生成）。内部 MCP URL、凭据和 Python 插件配置不接受客户端通过研究 API 注册。
