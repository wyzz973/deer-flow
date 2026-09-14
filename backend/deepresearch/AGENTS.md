# DeepResearch implementation boundaries

The workflow owns planning approval, dependency scheduling, evidence contracts,
bounded supplementation and report rendering. It does not own a second agent
runtime. Read `../AGENTS.md` and the harness subagents guide for native execution.

- `native.py` submits each role to `SubagentExecutor.execute_async`, polls the
  server-generated execution ID, and cancels/drains before registry cleanup.
  Do not replace it with a bare `create_agent` or copy the middleware chain.
- Candidate host tools are explicit and intersected with the Custom Agent
  allow/deny policy and native authorization. Unrelated MCP discovery, nested
  delegation and implicit source fallback are not enabled by this module.
- The native executor accepts narrow request-secret and execution-callback
  parameters. Never pass secrets through messages/configurable/checkpoints or
  merge arbitrary caller context into native authorization/sandbox ownership.
- `structured.py` isolates output conversion. Use ordinary chat messages and
  bounded retries; no provider JSON-mode requirement. Invalid evidence IDs
  remain failures; parsing must never manufacture references or research facts.
- `observations.py` projects native ToolMessage/receipt envelopes after execution.
  Never inspect MCP business payload fields or replace the tool's input schema.
  The original host tool object must reach the native model/tool loop unchanged.
  Native completed executions are cached; individual tool calls are not replayed.
- `trace.py` records bounded, redacted payloads and paired spans in the local
  event store. It has no telemetry-service dependency. Callbacks must remain
  loop-independent because native subagents run on an isolated loop.
- Trace API and export inherit owner/ACL checks. Operational logs rotate under
  the configured data directory; do not log raw prompts, headers or provider
  exceptions to stdout. Unknown failures retain type/status/stack locations.
- Native subagents retain one-shot state semantics. Workflow recovery reuses
  completed units/tool responses, but does not resume an interrupted child
  model turn. Do not claim exactly-once tools or full Gateway chat persistence.

Regression commands, from `backend/`:

```sh
uv run --no-sync python -m pytest ../tests/deepresearch -q
uv run --no-sync python -m pytest tests/test_subagent_executor.py -q
uv run --no-sync ruff check deepresearch
uv run --no-sync ruff format --check deepresearch
```

The DOCX regression needs `python-docx` from `deepresearch/requirements.txt`.
Tests using fake providers establish adapter/lifecycle behavior, not live MCP
authentication, local-model quality or end-to-end deployment readiness.
