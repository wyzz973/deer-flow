# DeepResearch stability audit: 2026-09-16

## Outcome and scope

The previously failed real-model run
`fa984b8d-91bd-4dd6-8609-6e88d4660a86` reached `COMPLETED` at
`2026-09-16T05:02:51Z`. It used the configured DeepSeek model, real native
research agents, native web tools, and the existing AIO sandbox. No synthetic
report replaced the failed run.

This audit covers the DeepResearch API, lifecycle, checkpoint replay,
publication, local observability, its chat UI, and the native directory-tool
failure encountered during acceptance. It is not a certification that the
entire DeerFlow repository or deployment has no vulnerabilities.

## Failures and hardening

| Area | Failure or risk | Change |
| --- | --- | --- |
| Native agent identity | Concatenated parent and planner-generated unit names exceeded DeerFlow's 64-character thread ID limit | Use a validated 51-character digest identity scoped by parent, cycle, role, and full unit ID; preserve readable identities in trace metadata |
| Persistent sandbox shell | The directory-listing script executed `exit` in a reused shell and could hang until the shared 600-second timeout | Run the script in a child `sh -c`, use unique temporary status files, and bound listing with a 30-second remote hard/no-change timeout and 35-second HTTP timeout |
| Native cleanup | Failure at the first audit write or repeated cancellation could detach a submitted worker | Cover the first await with cleanup; shield and drain the native worker before releasing tool closures and recording terminal callbacks |
| Local trace | A terminal parent could leave a child tool appearing to run forever | Finalize missing callbacks after worker drainage; append explicitly reconciled interruption events for historical descendants of terminal parents, without rewriting past events or inventing durations |
| SQLite cancellation | Cancelling an await does not stop its worker thread | Drain in-flight database operations before acknowledging cancellation; fence new reservations and report publication with persisted cancellation state |
| Replay authorization | An idempotent create replay could skip current access policy | Reauthorize both fresh and replayed requests; let valid replays return at capacity without launching duplicate work |
| Accepted commands | A crash between accepting a plan/message and graph submission could lose the operation | Persist a credential-free pending operation with the accepted mutation; compare its identity against the checkpoint before replay; acknowledge only after graph progress |
| Plan editing | Invalid edits could stop the countdown before validation | Validate version, request identity, and edit eligibility before durably accepting and cancelling the timer |
| Research replay | Completed cache entries could lack status/events after a crash, or different cycles could share event identities | Reconcile completed metadata without rerunning tools; scope event/cache identities by cycle and fence follow-up cycle advancement |
| Report publication | Version, report message, completed state, and completion event could diverge | Publish these records in one SQLite transaction with a replay key; cancellation prevents publication |
| Service lifecycle | Shutdown/admission races and failed bookkeeping could leak task or secret references | Serialize shutdown with admission, reject work while stopping, and always release task/credential bookkeeping |
| Model failure handling | Provider bodies could leak secrets or surface as opaque native failures | Persist safe typed metadata for authentication, billing, rate limits, timeout, and upstream failures; do not copy arbitrary provider exception bodies into research errors |
| Token policy | Updating the lead-agent policy did not configure the native researcher policy | Apply private per-role native overrides while preserving explicit disabled policies and stricter operator caps; retain uncertain usage reservations |
| Evidence-gap UX | `RESEARCH_GAPS` left a disabled retry button and no visible completion path | Add an explicit "generate report with limitations" action for evidence-bearing gap failures; do not offer it for citation failures or evidence-free runs |

MCP tools retain their original input schemas and model-visible results. Source
observation, receipts, and redacted archival copies do not impose a universal
MCP result format or replace the native agent loop.

## Workflow contract

```text
request -> clarify/plan -> review and server-owned countdown
        -> native research agents -> evidence merge and gap review
        -> bounded supplementary research
        -> sufficient evidence OR explicit limited-report consent
        -> synthesis -> final reference/policy validation
        -> atomic immutable report publication

recoverable failure -> durable checkpoint retry and completed-unit reuse
cancel -> persist fence -> drain workers and database I/O -> terminal trace
```

Limited-report consent is not permission to invent citations, erase uncertainty,
or mark unsupported facts as established. The report retains the outstanding
limitations and still passes the final citation and source-policy checks.

## Automated verification

- Backend and native integration regression: **361 passed**.
- Frontend DeepResearch regression: **18 passed** across five test files.
- TypeScript `tsc --noEmit`: passed.
- Scoped ESLint: passed.
- Scoped Ruff lint and formatting: passed; 47 Python files checked for format.
- New trace-finalization and limited-report UI regressions were observed failing
  before their fixes and passing afterwards.

Backend command, from `backend/`:

```bash
uv run --no-sync python -m pytest ../tests/deepresearch \
  tests/test_subagent_executor.py tests/test_jina_client.py \
  tests/test_web_fetch_paging.py tests/test_remote_list_dir.py \
  tests/test_aio_sandbox.py -q --tb=short
```

Frontend commands, from `frontend/`:

```bash
python3 ../scripts/pnpm.py rstest run deepresearch
python3 ../scripts/pnpm.py exec tsc --noEmit
python3 ../scripts/pnpm.py exec eslint \
  src/components/deepresearch/plan-card.tsx \
  src/components/deepresearch/research-conversation.tsx \
  tests/unit/components/deepresearch/plan-card.dom.test.tsx
```

Fault coverage includes audit-write failure, repeated cancellation, publication
rollback/replay/cancellation, revoked ACL on idempotent replay, admission versus
shutdown, invalid edits, accepted-command recovery, cross-cycle events, and
terminal bookkeeping failures. Native timeout/provider errors and original long
unit identifiers are covered separately.

## Real-model and browser acceptance

- Resumed the original task through the actual page, not a replacement demo.
- Preserved the three already-completed original research units. All 12 eventual
  original/supplemental units have exactly one completion event each.
- Research stopped truthfully at `RESEARCH_GAPS` after two supplement iterations.
  The user's previously approved limited-report preference was then applied by
  clicking the new visible action in the browser.
- Final report: version 1, seven chapters, 53 cited sources and 55 referenced
  evidence IDs. All report references resolve through its citation map.
- Exactly one immutable report row was published. The final validator and
  renderer ended successfully.
- All **423** started trace spans have terminal records; no tool-call row remains
  `running`. The historical missing callback is visibly marked as reconciled,
  not retroactively presented as successful.
- Native AIO directory listing ran twice against the real acceptance container
  in approximately 0.36 and 0.30 seconds; the persistent shell remained usable.
  Subsequent model-driven `ls` calls also completed successfully.
- Computer Use confirmed the completed report card, full report view, citation
  selection and original-source excerpt. At a 390-by-844 viewport, returning
  from the source closed the mobile sheet; the normal viewport was restored.
- Versioned JSON and Markdown exports succeeded. Markdown matched the stored
  report exactly. Local trace JSONL export was parsed and checked for closure.

Private, ignored acceptance artifacts are retained under:

```text
.deerflow/deepresearch/live-3j8hr_hv/exports/stability-lEYj4s/
  run.json
  report-v1.json
  report-v1.md
  trace.jsonl
```

The live Gateway log remains in
`.deerflow/deepresearch/live-3j8hr_hv/stability-gateway.log`.

## Boundaries and remaining work

- Remote tools are at-least-once across process death before the successful
  result is committed. Checkpoint recovery does not provide distributed
  exactly-once side effects.
- The SQLite service remains single-process. This does not certify multiworker
  or distributed deployment behavior.
- Enterprise SSO and every third-party MCP service were not live-tested. Scoped
  ACL, source-policy, sanitization, and redaction tests are not a penetration test.
- Report provenance and structural/reference validation are not independent
  semantic fact checking. This broad topic retained substantive uncertainties,
  including secondary-source dependence; its limitations appendix is verbose
  and remains a report-quality improvement opportunity.
- Live acceptance used the user's explicit unbounded cumulative usage setting in
  the private acceptance configuration. Production budgets still need workload
  sizing; native per-operation timeouts and bounded supplement iterations remain.
- Markdown, JSON, and trace exports were checked in this pass. DOCX rendering,
  a production build, publication, and remote CI were not newly verified here.
- Operator credentials/configuration and the unrelated `local_backend.py` user
  modification were preserved. No commit or push was performed in this pass.
