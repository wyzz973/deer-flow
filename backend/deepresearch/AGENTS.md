# DeepResearch implementation boundaries

The workflow owns planning approval, dependency scheduling, evidence contracts,
bounded supplementation and report rendering. It does not own a second agent
runtime. Read `../AGENTS.md` and the harness subagents guide for native execution.
The current architecture and workflow are described in `docs/deepresearch/ARCHITECTURE.md`
at the repository root; update it with any change to nodes, events, contracts or storage.

## Taking over, debugging or auditing a run

`.agents/skills/deepresearch-engineering/` is the working skill for this module
(Codex loads `.agents/skills` natively, Claude Code via
`ln -s ../../.agents/skills/deepresearch-engineering .claude/skills/`). It maps
the module, lists what must not be done, and carries two offline scripts:
`scripts/audit_run.py --run <id|prefix|page URL|thread|latest> [--baseline <run>]`
turns a run's records into a fact sheet and an offline HTML page (timeline; time,
tokens and tools per stage, node and step; prompt cache; yield of each research
round; rule findings with the setting that changes each),
and `scripts/show_call.py` opens or replays one model call. Both read the store
through `Store` and `metrics.collect`; `tests/deepresearch/test_audit_skill.py`
runs them against a synthetic store, so change them together with any table,
record field or metrics key they read.

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
- `observations.py` projects native ToolMessage/receipt envelopes after
  execution. For `kind: mcp` and host-bound sources the tool object reaches the
  native loop unchanged: never replace its schema or rewrite what the model
  reads. Such a tool emits no artifact of ours, so evidence is derived
  afterwards: a `read` tool's page becomes `fetched_document` addressed from the
  call's own arguments (`sources.opened_pages`; an identifier becomes
  `mcp://<source>/<id>`), and structured `search`/`data` answers become
  per-record evidence by shape (`extract.records`; the whole answer stays
  citable unless records carry ≥80% of it). Without this, MCP-only research
  ended with NO_EVIDENCE: the converter saw no URL to match the notes to.
  Provider-backed sources own the model-facing schema (`channels.py`) and emit
  their artifacts themselves, reading payloads with the same `extract.py`.
  Native completed executions are cached; individual tool calls are not replayed.
- `wire.py` records every MCP invocation and HTTP request a source makes; its
  docstring says why it binds inside the tool coroutine. Two rules: the
  enclosing call is `callbacks.parent_run_id` (the `research_tool_call` row id),
  and that manager is never passed on — a call inheriting it is billed twice (25
  searches once became 41), so `config={"callbacks": []}` stays. `trace.py`
  writes a tool's arguments and answer to `research_tool_exchange`; the row
  keeps only metrics and a hash. Bodies are bounded by `audit_max_chars` and say
  `truncated` rather than clip silently. `logbook.py` exports a run as one
  JSONL and owns the only delete path (`store.prune`).
- `trace.py` records bounded, redacted payloads and paired spans locally, with
  no telemetry-service dependency. Callbacks stay loop-independent: native
  subagents run on an isolated loop.
- Trace and export APIs inherit owner/ACL checks. Operational logs rotate under
  the data directory; never log raw prompts, headers or provider exceptions to
  stdout. Unknown failures retain type/status/stack locations.
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
- Requests are built for prompt-prefix caching (providers reuse a prompt only
  from its first token on; on a slow self-hosted model a hit also skips that
  prefill). Three rules, each pinned by a test:
  1. A role's conversation only appends. `model_budget_config` turns the
     engine's `verification.receipts_enabled` off unless `tool_receipt_ledger`
     is set: `ToolReceiptMiddleware` rewrites its ledger after the system
     prompt every turn, which left researcher loops at 4-5% cached input
     against 77-82% without it. The switch also stops stamping, so
     `observations.derived_receipts` rebuilds `r1..rN`/status from the archived
     tool messages. Never insert or edit before a running role's newest message.
  2. Payloads read from what every call shares to what only this call has:
     `instructions`, run-level context, `sources`, `unit`, and last the counters
     that change between steps (`search_budget`, `shared_run_budget`). Sections
     put `findings`/`evidence` before `section`; conversion sends
     `{"task", "answer"}`; a follow-up sends plan, report, conversation, then
     the new `message`; a revision sends findings and evidence, then the report,
     then the request; planning leads with what a deployment keeps constant.
     `structured_task` puts `output_schema` after `instructions` when they lead,
     and leaves both last in long writer tasks, next to the point of generation.
     `recent_messages` moves a window's head once per ten messages, not every
     turn. `tests/deepresearch/test_prompt_cache.py` states the property on the
     text a model receives. `digest` sorts keys, so reordering never changes a
     cache key.
  3. Nothing that changes per call goes into a system prompt.
  `LocalCallbacks` records `prefix_messages`/`prefix_chars` per call and metrics
  report `prefix_reuse_ratio`: the ceiling a prefix cache could serve,
  independent of provider reporting. A low ceiling means our request changed
  early; a high ceiling with low `cache_read_ratio` means the provider or
  gateway (no prefix caching, no replica affinity, an expired entry).
  `session_overrides` adds the conversation's id as
  `extra_body[session_param]` / `default_headers[session_header]` only where a
  `ModelSpec` names them (OpenAI's own endpoint gets `prompt_cache_key`
  unasked); `merged_overrides` merges dictionary fields with the profile's,
  because the engine's `model_overrides` replaces whole fields.
- Compaction is research configuration: `compaction_config` builds the engine's
  summarization settings per role from `CompactionSpec` and the role model's
  declared context window, with `prompts.compaction` as the summary template
  (it must keep opened URLs, verbatim quotes and dates). The host's
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

- Every model-calling node is tunable on its own (`config.NodeSpec`, `nodes:`):
  rewrite, plan, research, conversion, outline, section, summary, revision and
  follow_up. `models.model_for` resolves the model (a researcher's own role model,
  then `nodes.research`; for the fixed roles the node first, then the role),
  `native.node_of` names the node from role and task, and sampling parameters are
  applied by `models.with_node` to the execution's private model profile, so the
  engine's factory applies them and metrics keep the model's real name. Only
  `rewrite` and `summary` can be disabled. Record `config_node` on every model
  call: `metrics.breakdown.by_node` (calls, tokens, latency, truncated answers,
  contract retries) is what tuning is judged by. `json_mode` is optional and only
  for direct calls; the contract is always requested in prompt text as well.
- Model gateways that only speak Chat Completions are a first-class target:
  never require JSON mode, structured output or the Responses API. For
  `provider: openai` with a non-OpenAI `base_url`, `chat_completions.ChatCompletionsModel`
  sends `max_tokens` (LangChain always renames it to `max_completion_tokens`,
  which such gateways ignore or reject) and `engine_model` turns `stream_usage`
  on (LangChain switches it off once `base_url` is set, leaving no usage at all).
- What a step may cite is decided once, where the evidence is gathered, and
  travels with it (`RawEvidence.citable`). `report_policy.results_citable` is the
  single rule for search results: citable when the operator says so or when no
  enabled source has `role: read`. A step without a read tool gets
  `prompts.research_records` / `conversion_records` and the findings-only
  contract, its search results become one evidence record each, references say
  "search excerpt; the original was not opened", and `require_original` becomes a
  stated limitation instead of `NO_EVIDENCE`. Never tell a researcher to open a
  page when it has no tool that can.
- MCP: `McpServerSpec.allowed_tools` is an allowlist checked at load time and
  again in `McpManager.tool`. Both `kind: mcp` sources and `type: mcp` providers
  account for their calls through `channels.SearchBudget`; the inner MCP
  invocation runs with `callbacks: []` so the research callbacks neither record
  it a second time nor charge the tool budget twice. `mcp.connection_failure`
  unwraps the client's exception group into auth/timeout/network/server without
  echoing transport text. Header and environment values may interpolate
  `${ENV}` and `${secret:NAME}`; interpolated values are redacted like whole references.
- Time is a budget that winds research down (`store.research_seconds_left`,
  `report_time_reserve`): a step gets a tool-stop time and a hard timeout
  (`research.unit.deadline`), source calls are bounded by the time left, a step
  that cannot start degrades with `RESEARCH_TIME_SPENT`, supplementing stops and
  the report is still written. The run-wide timeout is the ceiling plus one
  report reserve. Budgets are per task: a follow-up after the report archives
  `usage` into `usage_history` and starts from zero.
- `validator` decides `NO_EVIDENCE` and saturation from citable evidence that a
  finding refers to, never from the size of the pool. `open-questions` gaps earn
  one supplement per planned step, `supplement_gap_codes` chooses which gap kinds
  are chased at all, and supplements are matched to their step by `depends_on`.
- Writer output is normalized before it is validated: reasoning that ends with a
  lone `</think>`, near-miss markers (`normalize_markers`), a code fence left open
  by a cut-off answer (`close_fences`), any link or image that does not point
  inside the document, control characters. Labels the outline model writes are
  cleaned with `plain_text` before assembly, and `runner.reader_caveats` makes
  sure a report written after failed or cut-short steps always says so.
- `profile.guard` is the boundary of the settings API whoever is signed in: no
  Skill file paths, no stdio MCP servers, no custom model classes and no literal
  credentials in headers or environment. Those belong in the operator's file.
  `profile.masked` hides literal credential values from users who cannot edit.
- Naming: the intranet deployment (slow gateway model, MCP-only search without
  originals) is why these features exist, but it is not a variant. No
  deployment-specific folders, templates, identifiers or example values; extend
  the generic templates (`deepresearch.example.yaml`, `examples/deepresearch/offline/`,
  `examples/deepresearch/mcp-sources.fragment.yaml`).

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

- Budgets wind research down instead of ending it. A research step is told how
  many searches it may make (`max_searches_per_unit`, in its payload); when the
  allowance or the run's tool ceiling is gone, `channels.SearchBudget` answers
  the model with a stop instruction, so the step writes its notes rather than
  raising `BUDGET_EXHAUSTED`. The tool enforces the allowance, not the model's
  compliance; a search claims its slot before awaiting the ledger, because
  searches from one turn run concurrently. Page reads never count against the
  step allowance — only an opened page is citable, and charging reads left a
  live run with no page read and `NO_EVIDENCE` — only against the run-wide
  ceiling. A stop reply carries the `BUDGET_STOP` artifact (with the allowance
  it refused) and `research_observations` skips it: it is never evidence. Source
  tools account for their own calls — the model callback must not reserve them
  again. Model tokens keep a report reserve (`store.report_reserve`). Each
  research step gets a share of what research may still spend (left ÷ steps
  running now, less a conversion margin) through the native
  `TokenBudgetMiddleware` (`native.model_budget_config`): warned at half, stopped
  at the share, which strips tool calls so the step ends with its notes. The
  step's conversion may draw on the report reserve — its research is already
  paid for. A research call that would still spend the reserve fails with the
  non-fatal `RESEARCH_BUDGET_SPENT`, the step degrades, supplementation stops
  (`store.research_spent`: a step already failed to reserve, or less than a
  turn is left) and the writer still has capacity. Only a batch that failed for
  other reasons, or a run with no citable evidence at all, still fails.
- A research-phase model call reserves its input plus `RESEARCH_TURN_OUTPUT`
  (4096), not the whole `max_output_tokens`: a research turn is a tool call or a
  short note, and reserving the full cap made a 120k budget look spent at half of
  real use. Report calls (`phase` synthesis/follow_up) still reserve the full cap.
  Reported usage replaces the estimate; when a provider reports no usage, the
  unreserved remainder of the cap is charged after the call, so unknown spend
  stays conservative.
- `dispatch` starts a unit once its `depends_on` are done and a
  `max_concurrency` slot is free; never in waves (`gather` over the ready
  set): a dependent then waits for the slowest sibling. A non-fatal unit
  failure becomes a zero-confidence placeholder with a disclosed limitation,
  `unit_failures` and `research.unit.failed` once any unit succeeded or
  findings exist; its dependents go on.
  `FATAL_UNIT_ERRORS`, non-`Exception` errors, `OSError`/`sqlite3.Error`
  (nothing new starts, running units finish) and a run where every runnable
  unit failed still fail the run; a committed result is reused as success.
  Leaving early cancels and awaits running units.
  `runner.keep_declared_dependencies` restores an explicit `plan/edit`'s
  `depends_on` after normalisation.
  Keep example research-role timeouts at 600 s; long reads are normal.
- Evidence is derived from open-web payloads, so build it through validation
  (`observations.derive`), never `model_copy`, which skips it: an over-long
  title or empty text otherwise reaches the store and only fails later, in
  `evidence_merge`, taking a completed unit with it. Display metadata (titles,
  publisher, excerpt, plan and step labels) is bounded by the contract rather
  than rejected; locators, ids, claims and briefs stay strict.
- `evidence_merge` revalidates stored results through `evidence.valid_result`:
  records the current schema cannot keep are dropped with the fields that
  rejected them (`research.evidence.dropped`), findings keep the references that
  survive, and a finding left without any reference goes with them. A
  structurally invalid result still fails the run.
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
- A stopped or failed follow-up leaves its published report in place, and the
  conversation goes on: `conversation.message` accepts a message for a
  `CANCELLED`/`FAILED` run that has a report, clears `cancel_requested` and
  starts a new task budget. Without a report it raises `RUN_STOPPED` (409, not
  recoverable); `RUN_BUSY` stays reserved for runs that are executing. The
  frontend mirrors this in `composerAccepts({status, hasReport})`.
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
  only the lead-agent `token_budget`. Preserve stricter configured caps and
  native opt-outs; unbounded acceptance stays explicitly opt-in.

### Interrupted callbacks

Close the research callback handler only after native worker drainage, even when
the native result was already terminal. Keep uncertain model reservations; never
turn a missing tool callback into successful source evidence. On workflow entry,
reconcile open descendants of terminal parents with explicit interruption events
and an atomic tool-activity update; leave live roots alone, keep unknown
durations null.

AIO directory listing must execute its exit-bearing script in an isolated child
shell, use unique temporary files, and have its own remote hard timeout plus a
bounded HTTP request. Do not reuse the long interactive-command timeout for this
small read operation or modify operator sandbox configuration to hide failures.

See `docs/deepresearch/STABILITY_AUDIT_2026-09-16.md` at the repository root for
failure injection, real-model recovery evidence, and deployment boundaries.
