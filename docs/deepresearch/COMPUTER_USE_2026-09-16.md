# Native DeepSeek acceptance, 2026-09-16

## Current status

In progress, not a release sign-off. The real Gateway, existing DeepSeek model,
native tools, and native chat UI were exercised. Four research runs ended at
the shared model budget before a final report. Later budget-coordination and
payload-reduction changes have offline coverage but are not yet live-accepted.
No final commit, push, or CI result is claimed for this worktree.

## Isolation and real connectivity

- `python -m deepresearch.live --allow-live --model deepseek-v4-flash --jina-no-key`
  launches the actual `app.gateway.app` and registered research extension, not
  a synthetic API or replacement agent loop.
- Gateway listens on loopback port 8001; Next.js runs on loopback port 3100.
  The pre-existing service on port 3000 was not stopped.
- Configuration copies, databases, and skills live under the ignored
  `.deerflow/deepresearch/live-*` directories. Operator files are not overwritten.
  Inline credentials in generated config are replaced by process-only env refs.
- This is the host's development auth-disabled mode, not enterprise SSO proof.
- The existing Tavily native search returned real results. Jina with the existing
  key returned 401; the same native client's public/no-key mode successfully
  fetched SQLite documentation. The operator's key was not edited.
- The native readability helper installed its runtime npm dependencies on first
  use. Model and tool outputs are retained privately, not committed as fixtures.

## Recorded real runs

| Run | Private data directory | Tool calls | Reported model tokens | Outcome |
| --- | --- | ---: | ---: | --- |
| `8348e59a-7876-40bb-a0c4-f0a6ebabbb29` | `live-94ehg_m4` | 0 | 11968 | Old byte-based accounting reached 57306; recursion caps also interrupted native execution |
| `a580309b-2644-4e78-9377-4fee4a41012a` | `live-wm8qzd2m` | 21 | 53572 | Correctly stopped at the launcher's reduced 60000-token budget |
| `145d39e3-d318-4e46-9b00-2423e5e4d62f` | `live-e6oo8zt1` | 45 | 106456 | Default 120000-token budget reached before final results; Web-only policy omitted native Skill reading |
| `cc1ecdde-f49a-4792-8d78-1b53c90d8d33` | `live-ocdwzg3g` | 31 | 104866 | Skill reads restored; still stopped before synthesis under the shared budget |

The fourth run recorded four `read_file`, twenty-one `web_fetch`, and six
`web_search` invocations. The four research runs reported 276862 model tokens
in total. This is recorded research usage, not an account-wide billing total.

The real edit path was exercised on the second run: Edit paused the countdown,
and a natural-language request produced three revised research units in the
same run. It did not produce a plan document masquerading as a research report.

## Native chat comparison

The ordinary chat route was exercised through computer use, with the existing
DeepSeek Flash selected in the native model picker. Chat
`82788be4-c58b-4964-a2e4-50ddbd73d367` successfully read the provided
`technical-route` Skill using the native file tool and explained its citation
requirements, without a web search or file modification. The UI reported about
22.7K tokens and seven seconds. Its data is in `live-e6oo8zt1`.

This is evidence that the shared chat surface and native Skill/file path work;
it is not evidence that the entire research workflow has completed.

## Corrections derived from the live evidence

1. **Native recursion semantics:** the former Skill cap of 8/12 was applied to
   graph recursion steps, which include middleware nodes. Default Skill limits
   now inherit the actual native Agent limit; examples allow 128/256 steps.
2. **Accounting:** offline estimation replaces byte counting. Reservations are
   settled against reported usage; unknown/failed usage stays conservatively
   charged, and actual overrun is recorded and stops subsequent work.
3. **Output caps:** a private AppConfig model profile applies the research output
   ceiling without changing the operator's model profile.
4. **Tool completeness:** native Skill instructions require `read_file`. Planner
   and writer may read their methodology; researchers inherit the host toolset
   subject to existing authorization, instead of a Web-only allowlist. Earlier
   traces showed `web_fetch` being used incorrectly for `/mnt/skills/...` paths.
5. **Budget coordination:** the existing native budget middleware now gives
   researchers an early soft wrap-up warning, leaving room for sibling tasks and
   synthesis. Stronger operator limits remain intact. This latest change still
   needs live acceptance.
6. **Repeated context:** conversion receives compact receipt/URL identities,
   not a duplicate of every tool body; synthesis receives evidence referenced
   by findings, while the full evidence and trace remain stored for audit.
7. **Output contract:** planner/writer see the schema in ordinary prompt text.
   Valid native JSON avoids an unnecessary second model call; ordinary-text
   conversion remains the fallback, without provider JSON mode.
8. **Trace refresh:** a terminal revision refreshes the inspector even when a
   fast failure skips the UI's intermediate active state.

## Latest offline checks

- Combined research and native executor tests: **197 passed**, 3.12 seconds.
- Frontend presentation/trace DOM tests: **7 passed**.
- Full frontend ESLint/TypeScript: passed.
- Research Ruff check/format: passed in this iteration.
- A service-test timing race was corrected by waiting for driver bookkeeping
  before submitting a direct decision; production still guards against busy
  duplicate execution and the server countdown retries transient RUN_BUSY.
- CI backend setup now uses the locked host workspace, because these native
  configuration/bridge tests must not be evaluated against a reduced dependency
  environment. The CI frontend job also runs the new presentation/DOM tests.

## Remaining acceptance

### Subsequent UI-only pass (no additional model API calls)

The frontend was connected to a separate synthetic backend on port 8022, using
`.deerflow/deepresearch/ui-regression-20260916`. Its countdown was accelerated to
five seconds only for browser automation; the deployment default remains 45.

- `59d26d5f-5248-491e-952d-894395f3dc57` completed after automatic countdown.
- `c7a2022a-968f-4dd1-a546-734505c5a9d5` exercised pause, reload while paused,
  conversational plan revision, manual start, follow-up, and report rewrite.
  It completed with report versions 1 and 2 and exactly four synthetic tool calls.
- The historical Markdown downloaded through the UI matched version 1 from the
  API byte-for-byte. The Word download was a valid package with document XML.
- The Trace download contained 48 valid events, with 24 starts and 24 ends.
  The inspector exposed follow-up input/output and searchable lifecycle records.
- Desktop citation clicks selected the correct grouped source and its excerpt.
  A mobile failure was found and fixed: returning to a citation now closes the
  Sheet, and the report is visible afterward. The temporary viewport was reset.
- On a fresh test tab, only the capability request was fulfilled with a 404.
  The UI displayed the missing-extension instructions, and Enter produced zero
  research POST requests. Interception was cleared and the test tab was closed.
- Fifteen frontend tests now cover receipt-anchored cached countdowns, delayed
  GET/command ordering, abandoned creates, idempotent message retries, reconnect
  reconciliation, terminal history refresh, readiness, and trace finalization.
  Full frontend lint/typecheck passed. This does not validate model quality.

The initial delayed-GET and unavailable-submit tests failed before the fixes.
Assertions check query-cache state as well as rendered state to avoid passing
on a temporarily stale React render. The standalone Playwright suite has been
updated with the same cases; its complete automated run/CI is still pending.

### Open gates

- One complete real research run after the latest coordination/context fixes,
  including report content, citation/source linking, and trace inspection.
- Complete repeatable browser execution and desktop/mobile acceptance on the
  final revision; do not reuse old synthetic screenshots as proof of this run.
- Confirm caching/navigation/terminal-history edge cases in the conversation UI.
- Finish the scoped commit/push to the authorized feature branch and inspect CI
  for that exact commit. Keep the unrelated AIO sandbox edit out of the change.

## Resumed real-model acceptance: 2026-09-16 09:28 Asia/Shanghai

The user explicitly requested another real-model test. This run loaded the latest
worktree backend, including the native soft-budget warning, compact conversion
catalog, role output schema, and synthesis evidence selection changes. No budget
increase or synthetic fallback was used.

- Run: `5a7d6145-00bf-4c41-bb5f-5db60d9ef48e`.
- Gateway mode: `deerflow`; persisted run `demo=false`.
- Model: configured `deepseek-v4-flash`, through native SubagentExecutor.
- Limits unchanged: 120,000 model tokens, 60 tool calls, 900 seconds.
- Private acceptance data: `.deerflow/deepresearch/live-_7dzsj4p` (not published).
- Sources: native search/fetch; Jina no-key mode was process-local. No operator
  credentials or configuration files were changed. This was not a deployed MCP
  compatibility test.
- Request: compare SQLite and PostgreSQL for a 5-15 person internal knowledge
  application across concurrent writes, operations, and full-text search; read
  official originals and produce a concise report with traceable citations.

### Browser observations

Computer Use submitted the request through the native research conversation.
The real model produced three research units. Clicking Edit paused the countdown;
reloading preserved the paused state. A natural-language revision in the same
composer kept the same run and three-unit scope, requested a Chinese report and
separation of official facts from scenario inferences. The updated plan then
started automatically after the server-owned 45-second countdown.

The Activity panel updated with real tool calls. The failure appeared in the
conversation instead of being presented as a completed report. Local Trace
pagination, error search, and model error details were exercised in the browser.
The final browser page was left on the failed run with Trace available.

### Result: FAILED, not accepted end to end

The run stopped at `dispatch` with `BUDGET_EXHAUSTED` / `max_model_tokens`.
Provider-reported usage was 106,797 tokens across 14 completed model calls.
Three subsequent model starts were rejected while reserving their projected
usage against the 120,000-token shared ceiling; the settled total being below
the ceiling does not mean another request could fit.

| Execution | Completed model calls | Reported tokens |
| --- | ---: | ---: |
| Initial planner | 2 | 7,075 |
| Plan revision | 2 | 9,363 |
| Concurrent-write researcher | 4 | 37,089 |
| Operations researcher | 3 | 25,749 |
| Full-text researcher | 3 | 27,521 |

All three researchers had distinct native child-thread IDs, scoped by research
unit. Recorded tool calls: 5 `read_file`, 5 `web_search`, 11 `web_fetch`,
1 `browser_navigate`, and 1 `browser_get_text`, for 23 calls in total.
Tool-call completion alone does not establish original-source quality or claim
correctness. No final report or accepted evidence pool was produced.

The local export contained 110 trace records: 55 starts and 55 ends. It retained
model/tool/agent relationships and the terminal error stack, including
`trace.py:on_chat_model_start` -> `store.py:reserve`.

The latest soft-budget changes did not deliver a complete run within the default
budget. The remaining issue is graceful budget-aware researcher completion and
preservation of useful partial results for synthesis, not basic DeepSeek
connectivity. Precise middleware-warning effectiveness still needs diagnosis;
this test alone does not prove whether a warning was absent or ignored.
No additional paid retry or functional code change was made after this failure.
Real report rendering, citation correctness, and report export remain unaccepted
for this latest real-model path. Prior synthetic UI passes do not close that gap.

## Explicit unbounded real acceptance: 2026-09-16, follow-up

The user requested disabling the budget ceiling, checking DeepSeek's documented
maximum output, and trying the complete real workflow. Official Chat Completions
documentation lists `max_tokens` up to 393,216 (384K); the pricing page lists a
1M context window. These are per-request model limits, not cumulative run usage.
Sources: https://api-docs.deepseek.com/api/create-chat-completion/ and
https://api-docs.deepseek.com/quick_start/pricing .

The selected host profile had `max_tokens=8192`, additionally constrained by
DeepResearch's 4096 output default. The isolated acceptance now uses
`--unlimited-budget --max-output-tokens 393216`, with that value in both the
native model profile and conversion settings. Cumulative tokens, tool calls and
elapsed-time ceilings are null. Acceptance subagent token policies are disabled
through the native per-agent policy; operator files remain unchanged. Structural
iteration/unit bounds, cancellation and native timeouts remain enabled.

- Run: `750bb873-1562-4dd3-ae48-14cafde2baae`, mode `deerflow`, `demo=false`.
- Private data: `.deerflow/deepresearch/live-3j8hr_hv` (not published).
- Computer Use submitted the same three-subject comparison through the UI and
  manually started the real generated plan. Activity updates were observed.
- The three initial units completed, populated 171 evidence records, and entered
  one supplementary research round. These records are not 171 verified sources.
- Six native researcher executions were cached. Five normalized unit results
  were saved; supplementary unit `S1-6ae0eb0f` failed during result validation
  with `EVIDENCE_REFERENCE` ("Findings refer to an unobserved native tool call").
- A single UI checkpoint retry reused the cached native work. Tool-call count
  remained 154, but the same conversion/reference failure recurred.
- Final provider-reported usage: 1,918,747 tokens. No final report was produced.
- This proves the old cumulative ceiling no longer interrupts the run. It does
  not establish end-to-end success, final citation quality, or deployed MCP
  compatibility. No validation was bypassed and no further retry was made.

Trace also showed long official pages being truncated by the native fetcher,
repeated research attempts, and notes relying partly on search snippets or
third-party material despite the official-originals request. Source quality and
constraint adherence therefore remain acceptance gaps independently of the
reference validation error. Local native tool bodies were not reshaped to
conceal these results.

Regression results for the unbounded settings change: 63 DeepResearch tests
passed; scoped Ruff lint and formatting passed; frontend lint/type check passed.
The generated ResearchBudget OpenAPI schema and frontend nullable resource-limit
types were updated. This change does not fix the separate reference failure.

## Real workflow completed after reference repair

Run `750bb873-1562-4dd3-ae48-14cafde2baae` reached `COMPLETED` at
2026-09-16 10:01:45 Asia/Shanghai using the configured real DeepSeek Flash model.
This was a checkpoint recovery of the existing real study, not a clean new run
without prior failures, and no synthetic data or fabricated references were used.

The reference failure had two concrete causes. The failed supplement cited six
real parent-unit evidence IDs absent from the child-only catalog; one additional
ID was absent from all recorded executions. The repair carries only declared,
same-run/same-cycle dependency evidence with its saved findings and validates
reference membership within the bounded conversion-repair loop. Unknown IDs get
precise feedback, never a guessed replacement. The native MCP/tool loop remains
unchanged. A code-only launcher restart preserves the private config and cached
work; it rejects config drift rather than rewriting checkpoint fingerprints.

The user explicitly approved reports that disclose unresolved limitations. The
owner retry body `{ "allow_limited_report": true }` recorded this decision on the
run through the authenticated API and `report.limitations.policy` audit event.
It did not change operator configuration or citation validation. After the last
supplementary round, unresolved gaps were supplied to the writer and rendered.

### Observed completion

- All nine units completed: three initial units and six supplementary units.
- Provider-reported cumulative usage, including earlier attempts and recovery:
  3,255,861 tokens; 219 tool calls.
- Evidence pool: 560 records, not a claim of 560 verified original documents.
- Report version 1: 134 bound evidence references, five explicit limitations,
  three research-dimension chapters and a separate limitations chapter.
- Final synthesis, citation binder, final validator and renderer trace spans
  all ended `ok`. Export: 820 records, 410 starts and 410 ends.
- Deterministic report checks found no dangling references or uncited fact-type
  segments. This is provenance/structure validation, not semantic fact checking.

### Computer Use and export checks

The final report opened in the native reader. Clicking citation 1 selected its
record in Sources; the reverse link returned to the正文 citation. At 390x844,
the source sheet opened and the reverse link dismissed it; viewport override was
reset. The Trace timeline/search/export controls were visible. No browser console
errors were observed in the final check.

MD and Word export were triggered through the UI, but no matching download was
found in the normal Downloads directory, so browser file-save completion is not
claimed. The versioned API exports were separately downloaded to the private
acceptance `exports/report-v1.md` and `exports/report-v1.docx`; Markdown matched
the stored report exactly, and DOCX passed archive integrity and document-entry
checks. The report tab was retained as a deliverable.

### Regression status and remaining quality gaps

67 DeepResearch tests passed. Scoped Ruff lint and formatting passed. New tests
cover parent-evidence provenance, cycle isolation, semantic correction feedback,
persistent fabrication rejection, owner-only limitation consent, and code-only
resume/config-drift rejection. Test fixture initialization errors were corrected
before this passing run. API documentation and generated schema were updated.

The workflow is now proven through a real-model checkpoint recovery to a limited
report. It is not yet a quality acceptance of all original product requirements:
the report is verbose (47,830 Markdown characters including references), some
citations expose tool-result receipts rather than polished page identities,
long-page truncation caused repeated searches and incomplete original-text
coverage, and third-party material appears despite the official-only request.
The report discloses limitations, but disclosure does not make those quality gaps
pass. Browser-managed file saving also remains unconfirmed. No new commit/push or
CI run is claimed by this acceptance record.

## ChatGPT-reference report quality iteration

The existing ChatGPT research conversation was inspected through Computer Use:
`https://chatgpt.com/c/6aa945dd-e910-83e9-880e-d34b2dbeeed0`. Observed patterns were
a recommendation-led narrative, compact comparison tables, distinct research
sections, domain-grouped titled references, and separation of cited sources (24)
from scanned sources (74). Its conclusions were not copied into this feature.

### Implemented changes

- Native `web_fetch` no longer silently truncates at 4096 characters. It supports
  bounded 16,000-character excerpts, continuation positions and literal section
  lookup. Standard native artifacts carry URL/title/page hash/coordinates; tool
  failures have error status. Arbitrary MCP schemas/results are not rewritten.
- Intent-derived source scope gates conversion and final citations. Read pages
  remain distinct from incidental/discovered links. Original-read requirements
  cannot be met by a discovered URL alone.
- Brief reports get an editorial brief and a deterministic body-length gate.
  Comparison-table cells share the normal citation contract. Decision-relevant
  limitations are separate from blocking, researchable gaps.
- Citations group excerpts from the same scoped page snapshot while preserving
  all evidence aliases and selectable excerpts. Titles/URLs replace receipt-only
  presentation for fetched pages. Discovered-source lists are collapsed.
- Schema repair now returns bounded field-level errors without input values.
  This repaired a real table-header mismatch (the model counted the implicit
  dimension column as an alternative).
- Literal technical placeholders such as `<name>_docsize` are safely escaped by
  renderers instead of falsely rejected as HTML/reference errors. Explicit final
  validation retry remains bounded and cannot reuse a rejected draft cache.

### Real test results and current blocker

New real run: `ed5d5da5-f155-4a60-bf1f-00d69b822764`, same private acceptance
home `live-3j8hr_hv`, DeepSeek Flash, no cumulative resource ceilings. The prompt
kept the three original dimensions, requested a concise comparison table,
restricted references to the two official documentation sites, and explicitly
excluded forums/third-party products and HA scope.

All three research units completed without supplementary rounds. Their cached
native executions retained 54 page artifacts and 52 citation-eligible fetched
excerpts (counts include multiple reads of a page). The final source gate was
exercised on real native artifacts, not just fixtures.

| Artifact | Body characters | Cited page records | Comparison rows |
| --- | ---: | ---: | ---: |
| Previous acceptance report | 16,911 | 134 | none |
| Quality run version 1 | 4,980 | 26 | 8 |
| Quality run version 2 | 5,881 | 31 | 5 |

Both quality versions cite only `www.sqlite.org` and `www.postgresql.org`, with
`fetched_document` provenance. Citation-map IDs equal the aggregate cited-record
aliases with no dangling references. Version 2 has separate concurrency,
operations and full-text-search sections. The source-panel UI showed a real page
title, URL and selected original excerpt; the discovered-source list was collapsed.

These structural improvements are NOT a claim that editorial quality is finished.
Review of version 2 found inconsistent recommendation language: it suggested
PostgreSQL for out-of-box Chinese search while also saying neither option provides
it, and overgeneralized same-host multiprocess SQLite access. A targeted no-search
rewrite was submitted through the normal conversation to correct those claims.
That follow-up failed before useful generation: the real provider returned HTTP
402 `Insufficient Balance` at 2026-09-16 10:46:13 Asia/Shanghai. No further model
requests were made after identifying the billing failure. The run's latest status
is FAILED at follow-up; version 2 and all evidence/checkpoints remain available.
Provider-reported cumulative tokens are 2,158,977 and tool calls are 103. The later
rewrites did not repeat web research.

Versioned API exports were saved as private
`exports/report-quality-v2-draft.md` and `.docx`; the Word archive passed integrity
checking. Their draft names are deliberate: the final content correction is still
pending API balance restoration. Do not present them as fully reviewed reports.

Final offline gates: 120 backend tests passed (research plus native Jina/paging),
17 frontend tests passed, frontend lint/type check passed, and scoped Ruff lint
and format checks passed. The explicit final-validation recovery regression
confirms a rejected draft is not reused and successful research is not repeated.
No publication/CI pass is claimed. The configured API needs restored balance or
an explicitly selected working account/model before final real-model editorial
acceptance can continue.

Post-blocker UI-only check: version 2 was opened in the reader; source selection
showed a titled official page and its actual excerpt. At 390x844 the report kept
the document viewport at 390 pixels (no page-level horizontal overflow); the
comparison table uses the native horizontally scrollable table presentation.
The viewport override was reset and the report tab retained for handoff. No model
calls were made during these checks. The known recommendation inconsistencies
remain pending and the current version must continue to be treated as a draft.

## Post-recharge completion: report version 4

The user confirmed recharging DeepSeek and requested continued optimization.
The same real run `ed5d5da5-f155-4a60-bf1f-00d69b822764` was resumed through the
browser. Provider requests succeeded again; no API key or operator configuration
was changed and no search session was restarted.

An attempted brief rewrite still expanded from 6,659 to 8,973 body characters.
The fix was an internal brief-generation contract with paragraph-level and
comparison-cell limits, not a reduced provider-token budget. Public/stored report
contracts remain backward-compatible for historical reading and export.
Version 3 then completed at 1,949 body characters with 17 cited page records.

A conversational revision now receives the existing report AST (confirmed in
its real trace) rather than only a report title and research notes. A targeted
follow-up corrected overstatements about zero operations, busy-timeout behavior,
multiple application hosts and PostgreSQL lock scope. This is context-aware
regeneration, not a byte-preserving patch editor; exact preservation of every
untouched sentence is not guaranteed.

Version 4 reached COMPLETED with no current error:
- 2,118 body characters, versus 16,911 in the earlier acceptance report.
- 17 cited page records, all `fetched_document` from `www.sqlite.org` or
  `www.postgresql.org`, with no dangling citation aliases.
- Two short summary paragraphs, five comparison rows, separate concurrency,
  operations and full-text sections, and a conditional decision conclusion.
- Native model/tool history remains auditable. The three tool calls added after
  recharge were all `read_file`; web research was not repeated.

The report explicitly distinguishes same-host multiprocess SQLite access from
its single-writer constraint, does not treat default Chinese word segmentation
as PostgreSQL's exclusive advantage, and retains backup/restore responsibilities
for SQLite. PostgreSQL's table-level lock cases are noted in the comparison,
and retry discussion keeps its isolation-level conditions. These focused content
checks are not a claim of exhaustive independent verification of every sentence.

Computer Use verified the latest report reader, comparison table, source title,
URL and selected excerpt. Mobile citation return was checked after the sheet's
closing animation: zero visible dialogs and document width equal to the 390-pixel
viewport. The viewport was reset and the report tab retained as a deliverable.

Version 4 MD and Word API exports are saved privately at
`exports/report-quality-v4.md` and `.docx`. Markdown matches the stored version;
Word passed ZIP integrity and its six-row table (header plus five rows) was read
successfully. Browser-managed download file saving is not newly claimed.

This turn's final focused regression: 75 DeepResearch tests passed, scoped Ruff
lint passed, and formatting passed. Earlier frontend/native-fetch gates remain
recorded above; they were not silently relabelled as new runs. The real acceptance
still uses the explicitly authorized unbounded cumulative-resource configuration.
Production budget tuning, exact patch-based editing, and publication/CI remain
separate from this report-quality acceptance.
