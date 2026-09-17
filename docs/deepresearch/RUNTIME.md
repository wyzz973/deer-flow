# DeepResearch runtime

The authoritative current architecture and workflow are in [ARCHITECTURE.md](ARCHITECTURE.md);
[NATIVE_RUNTIME.md](NATIVE_RUNTIME.md) explains the native-runtime reuse decisions. This page is a 2026-09-14 summary.
See [API.md](API.md) and the generated [openapi.json](openapi.json) for HTTP contracts.

Research uses native SubagentExecutor, MCP cache/session pool and original tools.
There is no per-provider business-result mapping or query-only wrapper. Evidence
references are projected from captured native messages/receipts after execution.

Business snapshots and LangGraph checkpoints have separate responsibilities.
Completed native executions and research units are durable; interrupted child
agents restart, and arbitrary individual tool calls are not replay-cached.

The runtime remains single-worker SQLite. Cancellation forwards to the native
execution and drains before cleanup. Credentials stay in runtime context; the
requesting principal owns run, report, evidence, SSE and trace access. Config
fingerprint changes reject stale resumes rather than silently changing a run.

Tool/iteration/unit budgets are code constraints. Model budget is conservative
admission, not exact billing. Time excludes plan-approval waits. Native cap
reasons and researcher open questions trigger bounded supplementation. Opaque
receipt provenance does not verify source dates, independence or semantic
support; reports disclose this distinction.

Local trace records persist under the research data directory. Operational logs
rotate; detailed trace content is bounded/redacted and has no automatic TTL.
No LangSmith account or remote collector is required.
