# DeepResearch implementation boundaries

The workflow owns planning approval, dependency scheduling, evidence contracts,
bounded supplementation and report rendering. It does not own a second agent
runtime. Read `../AGENTS.md` and the harness subagents guide for native execution.
The current architecture and workflow are described in `docs/deepresearch/ARCHITECTURE.md`
at the repository root; update it with any change to nodes, events, contracts or storage.

## Configuration independence

DeerFlow is the engine; what research runs is DeepResearch configuration.
Models, sources and their provider chains, MCP servers, roles, methodologies,
prompts, engine tools, compaction and budgets all live in the research profile
(`config.py`), never in the host `config.yaml` models/tools/subagents or in
`extensions_config.json`. The only host-side entry is the `plugins:` registration.

- `models.py` turns `ModelSpec`s into engine `ModelConfig`s inside a **private**
  AppConfig copy (`private_config` rebuilds the name indexes; `model_copy` alone
  leaves stale lookups and silently drops the output cap). Research models are
  placed ahead of same-named host models; the operator's object is never mutated.
- Credentials are references only (`$ENV`, `secret:NAME`). `secrets.py` resolves
  them at use time, keeps saved secrets write-only, and feeds `trace_secrets` so
  the values are scrubbed from traces and the LLM audit.
- Legacy bindings (a role bound to a host subagent, a source bound to a host
  tool or host MCP server) stay readable for older files. Do not add new
  behavior that depends on them.
- `catalog.py` publishes only research-owned choices (model providers, source
  provider presets, engine tools, fixed roles, prompt defaults) for the settings
  page. It must not expose a host catalog.
- `profile.py` layers settings-page overrides over the operator file: `EDITABLE`
  fields only, snapshots inline each role's methodology (front matter stripped by
  `Settings.methodology`), and every run stores the snapshot it executes with.
  Editing settings never changes a run that already exists. `LATER_FIELDS` keeps
  old fingerprints stable when new fields are added.

- `native.py` submits each role to `SubagentExecutor.execute_async`, polls the
  server-generated execution ID, and cancels/drains before registry cleanup.
  Do not replace it with a bare `create_agent` or copy the middleware chain.
- Candidate tools are research-owned: the source tools built from `channels.py`,
  DeepResearch MCP tools from `mcp.py`, and the engine tools named in
  `engine_tools` (read_file/ls/glob/grep). The host tool list is not inherited;
  a role's `tools` allowlist can only narrow that set. Unrelated MCP discovery,
  nested delegation and implicit source fallback stay disabled.
- The native executor accepts narrow request-secret and execution-callback
  parameters. Never pass secrets through messages/configurable/checkpoints or
  merge arbitrary caller context into native authorization/sandbox ownership.
- `structured.py` isolates output conversion. Use ordinary chat messages and
  bounded retries; no provider JSON-mode requirement. Invalid evidence IDs
  remain failures; parsing must never manufacture references or research facts.
- Every direct model call (contract conversion, request rewriting, the doctor
  probe) goes through `models.complete`, which consumes the stream like the
  agent loop and falls back to a plain request only when streaming is refused or
  returns nothing. Many local OpenAI-compatible servers answer *only* in
  streaming mode and return an empty message for a plain request; treating that
  as a refused contract made research unusable on them. A reply whose only
  content is a tool call counts as an answer.
- `observations.py` projects native ToolMessage/receipt envelopes after execution.
  For MCP-bound and host-bound sources it must not inspect business payload
  fields or replace the tool's input schema: the original tool object reaches the
  native loop unchanged. Provider-backed sources are different by construction —
  `channels.py` owns the model-facing schema, so `extract.py` reads provider
  payloads deliberately and format-agnostically (JSON walk, Markdown links,
  Title/URL blocks, HTML title) and emits the same artifacts a native tool would.
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
- A conversational plan revision carries `start: true`: the planner writes an
  `acknowledgement`, and `plan_review` approves the revised plan without another
  countdown unless clarification is still required (ChatGPT-style). Structured
  `plan/edit` keeps review semantics.
- Messages in `STEERABLE_STATUSES` are durable `steering` updates, not graph
  input: no admission, no credentials, no restart. Units that start later and
  the writer read them; completed work is never repeated. Later phases return
  `RUN_BUSY` rather than implying an update was applied.
- `favicons.py` is the only outbound fetch outside native research tools. Keep the
  browser off third-party sites: the route requires a user, accepts a bare
  hostname, screens every hop with `validate_public_http_url`, bounds bytes and
  time, identifies raster images by signature (never SVG/HTML) and caches hits
  and misses in `research_favicon`. A slow site returns a `no-store` pending 404
  and finishes in the background. `favicons: false` disables all fetching.
- Metrics (`research_model_call`, `research_agent_run`, tool `output_chars`, `queued_ms`,
  `metrics.cache_hit`) are recorded beside execution and summarized by `metrics.py`.
  A metrics write must never raise into research. Keep phase attribution via
  `trace.metric_scope` (set in `traced()`), normalize usage with `usage_details`,
  leave unknown usage empty with `usage_reported=false`, and never invent prices:
  `pricing` is operator configuration excluded from the fingerprint and from the
  acceptance launcher's resume comparison. Tool records
  add `request_key` (a hash of redacted arguments, never the arguments) and
  `error_type` (exception class, or the coarse `returned_error_type` label for an
  error result). These labels only group metrics; never branch research on them.
- `doctor.py` checks configuration without calling models; only `--probe-model NAME`
  sends two short requests (a plain reply and a tool call) through the host model
  factory with thinking disabled. Pass the loaded `AppConfig` to registry lookups:
  the global subagent config stays empty until something loads it.
- Offline templates in `examples/deepresearch/offline/` are validated by
  `tests/deepresearch/test_offline_config.py`; keep them consistent with each other.
  With `require_dual_source: false`, `Settings.fit_origins` drops planned origins no
  configured source serves, because weak planners keep the internal+external default.
- Report messages retain immutable versions. A historical card must request its
  own `version` when exporting, not silently download the latest report.
- `SourceSpec.kind` selects a provider chain (`channel`), a DeepResearch MCP tool
  (`mcp`), or a legacy host tool (`native`, which respects the `native_tools`
  ceiling and the executor's Agent/Skill authorization and discovers nothing).
- `channels.py` gives every provider-backed source one stable model-facing schema
  per role and tries providers in order. Provider-level failures (rate_limit,
  quota, auth, timeout, network, server, config) cool that provider down for the
  process; request-level failures (invalid, not_found, blocked, empty,
  unsupported) only advance to the next provider. A site's 403 or timeout is
  request-level — never cool a working reader because one page refused. Health is
  process-wide, the page cache is run-scoped, and every attempt is recorded for
  `tools.by_provider` metrics and the settings page's provider test.
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
- Give the planner and report outline their output schema in normal prompt text.
  Keep provider JSON mode optional and use conversion only when needed. Research
  conversion receives compact native receipt/source identities, not another copy
  of every raw tool body the researcher has already consumed.
- The planner rewrites the request into `brief` (focus areas, time anchor, source
  preferences, evidence distinctions, report structure), a short `title`, short
  unit `title`s and explicit `assumptions`. Clarification is reserved for requests
  without an identifiable subject; do not add units that only synthesize others.

- Request credentials (`request_secret_headers`) reach research-owned MCP servers and
  custom HTTP providers through `SECRETS.resolve(ref, request)`: a request's value
  overrides the saved `secret:` of the same name for that request only. Keep them out
  of the store, the model and the trace, and keep the MCP discovery cache keyed by the
  resolved connection so two users never share discovered tools.
- `audit.py` + `trace.py` record every model call when `llm_audit` is on: the
  normalized request (messages content-addressed as zlib blobs, tool schemas,
  allowlisted params), the response with reasoning, the langgraph node and the
  execution group. Diffs against the previous call in the same group produce
  `new_message_indexes`/`repeated_prefix` so the audit UI can show one turn's new
  input. Scrub known secret values, `Bearer` tokens and URL secret parameters;
  never store raw provider exceptions.
- Compaction is research configuration: `compaction_config` builds the engine's
  summarization settings per role from `CompactionSpec` and the role model's
  declared context window, with `prompts.compaction` as the summary template
  (it must keep opened URLs, verbatim quotes, dates and receipt ids). The host's
  chat summarization thresholds never apply to research.
  The engine keeps a subagent's leading system prompt out of compaction
  (`_leading_system_messages` in `summarization_middleware.py`); without that a
  researcher loses its methodology mid-run and the human-anchored summary trimmer
  reduces the window to that prompt, discarding the actual research turns.
- The graph starts at `rewrite`: the conversation becomes one complete
  `ResearchRequest` (`user_query`, optional `acknowledgement`, at most three
  clarification questions) before planning, mirroring ChatGPT deep research. A
  plan edit and a follow-up that needs new research both re-enter `rewrite`,
  which merges the change into the previous request and writes the
  acknowledgement. `plan.brief` is the rewritten request.

Regression commands, from `backend/`:

```sh
uv run --no-sync python -m pytest ../tests/deepresearch -q
uv run --no-sync python -m pytest tests/test_subagent_executor.py tests/test_summarization_middleware.py -q
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
- Exhausted gaps write a report with disclosed limitations by default
  (`allow_limited_report: true`, event `report.limitations.auto`). A run without
  eligible evidence fails as `NO_EVIDENCE`. Strict deployments keep owner consent
  through retry; persist that run-scoped choice and an audit event, and never
  send `false` on an ordinary retry.
- Gap review counts only citable evidence. `open_questions` are publicly
  researchable; unknown user context is `assumptions_needed`. High-risk claims
  supported by one site are writing hedges (`single_source`), not gaps.
  Supplements are ranked by gap severity and truncation emits
  `research.supplement.deferred`.
- `activity.py` projects the durable event log into the user-facing timeline
  (`GET /activity`): progress notes (visible researcher text beside tool calls,
  never hidden reasoning), searches grouped per unit with queries/domains taken
  from role-declared tool arguments, pages read, step summaries, writing and
  completion. Report `stats` come from the same projection; the publication
  fence excludes them.

- Report quality is distinct from workflow completion. `SourcePolicy` is derived
  from the user's plan (domains, exclusions, original-text requirement) and gates
  both conversion references and final report references. It does not parse or
  replace arbitrary MCP business returns. Opaque tools remain supported under
  unrestricted policy; a discovered URL alone never establishes an original read.
- `SourceSpec.role` (`search`/`read`/`data`) is operator-declared. `report_policy.citable`
  excludes search result bodies and `observed_source` links unless
  `cite_search_results` is enabled; a raw read-tool envelope is superseded by the
  page it registered. Eligibility gates conversion, gap coverage and writing.
- The native Jina `web_fetch` now exposes bounded excerpts, continuation offsets,
  literal section lookup and optional `deerflow.web_page.v1` artifacts. Observe
  those native retrieval facts alongside the untouched ToolMessage. Keep other
  links in a page classified as discovered, not fetched. Errors use native tool
  error status and cannot acquire read provenance.
- Host `ToolOutputBudgetMiddleware` may externalize a long native fetch to
  `/mnt/user-data/outputs/.tool-results/*.log` (transform kind `externalized`).
  `observations.py` links a later `read_file` of that path to the fetched page
  and supersedes the runtime envelope. `browser_get_text` is a page read only
  after a successful `browser_navigate` in an earlier AI turn with no
  intervening leave-page action. Undeclared runtime tools are working material:
  `citable` returns false for them. Never infer a URL from file contents.
- `report.excerpt` strips the externalization synopsis and fetch headers from
  citation excerpts; the frontend mirrors this for older reports.
- Preserve each evidence record and document hash. Citation binding gives one
  display number per page: the URL ignoring scheme, `www.` and a trailing slash
  (a query still distinguishes pages); records without a URL stay per record.
  Each excerpt keeps its evidence ID and `document_hash`, so reads through
  different tools remain auditable. `report.references` picks the first real
  title in the group, else the URL without scheme; never show "Untitled".
- Reports are Markdown (`format: markdown-v2`) written by `DeerFlowRunner.write_report`:
  an outline (`ReportOutline`, every original unit covered), sections in parallel
  (distinct native task IDs, each cached) and then the executive summary. Writers
  cite only with `[[E###]]` markers from the evidence catalog they received.
  `report.py` gives one precise repair, then `sanitize` removes statements, bullets
  or table cells with unknown/non-citable markers and strips links or numeric
  citations. Never substitute a guessed ID or URL. Code blocks (Mermaid) are not
  rewritten, and a marker after a full stop belongs to the preceding sentence.
- The outline never plans the executive summary, scope/limitations or references:
  those parts are assembled. `report.reserved_heading` drives validator feedback
  and a final-attempt repair that drops such sections while keeping unit
  coverage. `plain_heading` removes numbering; `clean_answer` drops only a whole
  first line narrating the writing (`PREAMBLE`), never a real opening sentence.
- Length targets per style guide writing and never fail a run. Raw limitations stay
  in `audit`; the outline merges at most five reader-facing caveats. Historical AST
  reports remain readable; `render.docx_report` exports them.

- `dispatch` degrades a non-fatal unit failure (for example a native timeout)
  into a zero-confidence placeholder with a disclosed limitation,
  `unit_failures` and `research.unit.failed`. `FATAL_UNIT_ERRORS`, non-`Exception`
  errors, `OSError`/`sqlite3.Error`, and a batch where every unit failed still
  fail the run; a result already committed for the unit is reused as success.
  Keep example research-role timeouts at 600 s; long reads are normal.
- A unit result holds at most `RAW_EVIDENCE_LIMIT` records. `bound_evidences`
  keeps referenced evidence, read pages and tool records, and trims surplus
  discovery links (then superseded envelopes) with `research.evidence.trimmed`.
  Never fail a long unit on discovery volume. `result_error` reports
  `EVIDENCE_REFERENCE` only for unknown references; other contract overflows are
  `RESULT_CONTRACT` with field paths, never model or tool content.
- Research payloads name the reader language (`language_name`, e.g.
  `Simplified Chinese (简体中文)`), and `output_instruction` repeats it for progress
  sentences: English skill methodology otherwise pulls notes into English.
  `activity.visible_note` still hides notes that are not in the reader's language
  or that narrate skill files and tool names; the trace keeps them.
- The planner node persists `units` with the plan. A revision that starts at once
  never reaches the review interrupt, so do not rely on the interrupt handler to
  refresh the run snapshot.
- `display_markdown` and exports escape `$`; the report reader does not load
  remark-math. The events SSE sends `Cache-Control: no-store, no-transform` so
  compressing proxies (the Next.js dev rewrite) do not buffer progress.

- Syntax repair should return safe, bounded field paths and validation messages,
  never full invalid inputs. Technical angle-bracket placeholders are plaintext,
  escaped at rendering; they are not evidence failures. Final-validation retries
  reset a bounded repair opportunity and advance the draft-cache generation so
  a rejected draft cannot be repeatedly reused as a supposedly new attempt.

- Conversational revision passes the current Markdown document to
  `revise_report` and edits it in place; markers are validated and sanitized the
  same way. Do not attach that revision context to a new research cycle.

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
