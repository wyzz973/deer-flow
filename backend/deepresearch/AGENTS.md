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
- `conversation.py` owns server deadlines, pause/resume, idempotent messages,
  and routing back into the existing graph. Never approve a plan from a browser
  interval. Process restart pauses outstanding timers rather than persisting
  request credentials. Rewrites reuse evidence; new research increments `cycle`.
- Report messages retain immutable versions. A historical card must request its
  own `version` when exporting, not silently download the latest report.
- `SourceSpec.kind` selects either the existing MCP cache or native host tools.
  Native-only selection does not discover MCP servers and must respect the
  `native_tools` ceiling as well as the executor's Agent/Skill authorization.
- `sources.py` observes links beside execution without parsing provider-specific
  business fields. Keep discovered URLs, calls and cited references distinct;
  an observed URL is not evidence that its original page was read or verified.
- Normal progress SSE omits trace payload bodies. Detailed trace reads/exports
  remain owner/ACL-protected, cursor-based and local.
- `live.py` is an explicit opt-in acceptance launcher for the real Gateway,
  never a second runtime. Keep its state and skills isolated, bind loopback,
  preserve operator files, and replace inline credentials in generated config
  with process-only environment references. Do not invoke live providers in CI.
- Inherit the native graph recursion limit by default. Its middleware nodes
  consume steps too; do not translate a small number of model exchanges into
  `max_turns`. Explicit Skill ceilings can only tighten the host limit.
- Model metering reserves an offline estimate and settles actual provider usage
  atomically. Retain reservations when usage is unknown and stop on actual
  overrun. Keep native role output caps aligned with conversion caps via a
  private AppConfig model profile; never mutate the operator's profile.
- Research roles use the native token-budget middleware for an early soft
  wrap-up warning, leaving capacity for sibling tasks and synthesis. Preserve
  stronger operator caps. Feed synthesis only the evidence referenced by
  findings, while retaining the complete evidence pool and raw trace for audit.
- Give Planner/Writer their output schema in normal prompt text. Keep provider
  JSON mode optional and use conversion only when needed. Research conversion
  receives compact native receipt/source identities, not another copy of every
  raw tool body the researcher has already consumed.

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

- Resource ceilings (`max_model_tokens`, `max_tool_calls`,
  `max_elapsed_seconds`) accept explicit null for operator-authorized unbounded
  use. Finite deployment ceilings must reject null from clients. Accounting
  remains enabled even with enforcement disabled; do not use a huge fake limit.
- The live launcher's explicit unbounded option disables native token policy via
  `subagents.agents.<acceptance-role>.token_budget.enabled`, not the lead-agent
  `token_budget` field. It changes only its private configuration copy.

- Supplement findings may reuse parent references. Import only evidence bound to
  the declared dependency's saved findings in the same run and cycle; never
  search unrelated runs for a matching ID. Carry this provenance into the
  conversion catalog and ResearchResult.
- Output conversion validates reference membership inside its bounded repair
  loop. Return precise unknown-ID feedback without guessing replacements or
  weakening the final reference check. Repairs reuse the cached native result.
- Owner retry may explicitly accept a limited report. Persist that run-scoped
  choice and an audit event; keep the configuration fingerprint and citation
  validation unchanged. Pass recorded limitations to the writer and renderer.

- Report quality is distinct from workflow completion. `SourcePolicy` is derived
  from the user's plan (domains, exclusions, original-text requirement) and gates
  both conversion references and final report references. It does not parse or
  replace arbitrary MCP business returns. Opaque tools remain supported under
  unrestricted policy; a discovered URL alone never establishes an original read.
- The native Jina `web_fetch` now exposes bounded excerpts, continuation offsets,
  literal section lookup and optional `deerflow.web_page.v1` artifacts. Observe
  those native retrieval facts alongside the untouched ToolMessage. Keep other
  links in a page classified as discovered, not fetched. Errors use native tool
  error status and cannot acquire read provenance.
- Preserve each evidence record and document hash. Citation binding may group
  multiple excerpts from the same scoped page snapshot into one display number;
  `evidence_ids` and per-evidence excerpts retain reverse-link provenance.
- Writer input is an editorial brief, not a request to concatenate all findings.
  Comparison tables use the same Segment contract and reference validation as
  paragraphs. Brief/standard/detailed report lengths are content bounds, not
  provider token budgets. Separate unavoidable limitations from blocking gaps.

- Syntax repair should return safe, bounded field paths and validation messages,
  never full invalid inputs. Technical angle-bracket placeholders are plaintext,
  escaped at rendering; they are not evidence failures. Final-validation retries
  reset a bounded repair opportunity and advance the draft-cache generation so
  a rejected draft cannot be repeatedly reused as a supposedly new attempt.

- Brief generation uses a dedicated, narrower output schema: short paragraphs,
  at most two per section, compact table cells and limited source references per
  paragraph. The persisted/public StructuredReport remains permissive enough to
  read historical reports. These are editorial constraints, not provider-token
  or total-run-budget limits.
- Conversational revision must pass the current report AST as previous_report.
  Preserve unaffected text and references for targeted edits. Do not attach that
  revision context to a new research cycle; the writer otherwise starts over
  from findings and can undo earlier corrections.

## Stability and control-operation durability

- Native business thread IDs are fixed-length hashes of parent thread, research
  cycle, skill and full unit ID. Validate against the host's canonical thread-ID
  contract; never concatenate or merely truncate model-generated unit names.
  Keep full names, cycle and parent thread in local trace metadata.
- From the first await after native submission, cleanup must cancel/drain the
  worker even if recording its start fails. Repeated cancellation cannot detach
  that cleanup. Do not persist free-form native/provider error strings; expose
  safe provider status categories and typed trace metadata.
- Accepted messages and decisions carry a credentials-free pending_operation.
  A graph operation_id distinguishes an unapplied command from one already in a
  checkpoint. Recovery replays only unapplied input. Cached follow-up decisions
  and a message-scoped cycle marker prevent repeated cycle advancement.
- Report publication commits its immutable version, conversation projection,
  terminal run state and completion event in one SQLite transaction. A stable
  publication cache key fences node replay. Reconcile completed unit projections
  from durable results; a crash between result/status/event writes must not
  rerun a successful researcher or leave a completed unit marked running.
- Unit/pool event keys include cycle. New cycles clear the current report and
  limits, but retain historical report messages and versioned exports.
- Drain in-flight SQLite work before acknowledging cancellation. A persisted
  cancel request fences new calls and publication. Shutdown serializes with
  admission and never launches work after its stop flag is set.
- Idempotent create replay precedes capacity checks, but still rechecks live
  access policy. System message IDs cannot be used as client idempotency keys.
  Invalid plan edits must not stop the valid plan's countdown.
- Budget coordination updates the effective native per-agent token policy, not
  just the lead-agent token_budget field. Preserve stricter configured caps and
  explicit native opt-outs; unbounded acceptance remains explicitly opt-in.

### Interrupted callbacks and persistent-shell directory operations

Close the research callback handler only after native worker drainage, including
when the native result was already terminal. Keep uncertain model reservations;
never turn a missing tool callback into successful source evidence. On workflow
entry, reconcile historical open descendants of terminal parents by appending
explicit interruption events and atomically updating tool activity. Leave live
roots alone and keep unknown durations null.

AIO directory listing must execute its exit-bearing script in an isolated child
shell, use unique temporary files, and have its own remote hard timeout plus a
bounded HTTP request. Do not reuse the long interactive-command timeout for this
small read operation or modify operator sandbox configuration to hide failures.

See `docs/deepresearch/STABILITY_AUDIT_2026-09-16.md` at the repository root for
failure injection, real-model recovery evidence, and deployment boundaries.
