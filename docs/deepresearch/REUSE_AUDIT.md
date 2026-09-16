# DeepResearch native-feature audit

This document tracks the current refactor against the requested delivery. A
green synthetic test does not imply corporate MCP/model acceptance.

## Step-by-step ownership decisions

| Step | Existing capability | Research-specific responsibility | Decision |
|---|---|---|---|
| Identity | Gateway principal and frontend authenticated fetcher | Run owner and optional source ACL | Reuse host identity/CSRF; no new login system |
| Planning | Native Custom Agent executor/model factory | ResearchPlan contract | Keep contract at output boundary; plain chat accepted |
| Approval | LangGraph interrupt/checkpoint/Command | Versioned editable research plan | Reuse framework suspension/resume |
| Scheduling | Native process-wide subagent admission | ResearchUnit dependency DAG | Keep dependency scheduler, reuse execution capacity |
| Agent execution | SubagentExecutor, native middleware | Role and research objective | Delegate directly; do not copy middleware assembly |
| MCP discovery | Native MCP cache/session pool | Select configured source tools | Reuse cache, transports and credential interceptors |
| MCP arguments | Original BaseTool schema | None | Remove query-only wrappers and fixed pre-search |
| MCP result | Original ToolMessage/native output budget | None before model reads it | Remove business-payload normalization |
| Tool evidence | Native captured messages and receipts | Cross-unit citation catalog | Project host envelopes after execution; payload opaque |
| Recovery | Native one-shot execution and Graph checkpoints | Cache completed research units | No arbitrary tool replay cache or exactly-once claim |
| Validation | Pydantic and recorded native call identities | Coverage/gaps and report structure | No claim of semantic proof from receipt presence |
| Synthesis | Native role executor | StructuredReport output | Plain-chat conversion only at report boundary |
| Citations/export | Native receipt IDs plus recorded call IDs | Global report numbering and MD/HTML/DOCX | Keep deterministic report rendering |
| Observability | Native model/tool callbacks and receipts | Local research spans/correlation/export | Local storage, no LangSmith service |
| Workspace | Native sidebar, ChatSurface, MessageList and ChatBox | Conversation route and domain-message renderer | Share the same surface with ordinary chat; no second in-page history |
| Controls | Native PromptInput, Button and mobile Sheet | Conversational plan cards and linked reports | No constraints form; pause server countdown before editing |
| Source selection | Host native tools or MCP cache | Explicit source metadata and classification | Native sources do not require MCP discovery or bypass tool ceilings |
| Follow-up | Existing graph and native role executor | Answer/rewrite/new-research routing and report versions | Preserve old reports; only new research repeats evidence gathering |
| Persistence | Graph SQLite checkpointer | Research-specific plan/report/event tables | Retain explicit single-worker boundary; evaluate host store migration separately |

## Delivery gates and evidence

- Verified by native boundary tests: native MCP tools preserve both original argument schema and return value;
  malformed-for-the-old-adapter data must still reach the research model.
- Verified in the host development auth-disabled workspace, with API authorization separately tested: sidebar navigation, mobile and desktop layout,
  plan approval, report/citations and trace export work with shared components.
- Missing extension gives actionable setup guidance, not an endlessly loading page.
- API.md and generated openapi.json describe creation, approval, recovery, evidence and trace;
  deployment guide lists native host changes and corporate adaptation boundaries.
- CHANGELOG.md and HANDOFF.md include exact changes, tests, limitations and migration.
- Scoped commit excludes the pre-existing local AIO sandbox modification.
- Push completes to the authorized GitHub origin; remote revision is confirmed.

## Removed designs

The former `normalize_mcp`, auto/mapped payload parser and query-only wrappers
are removed from the real execution path. Legacy source mapping configuration
keys are accepted for migration only; they do not rewrite native tools/results.
The host's own protocol/content-block handling remains authoritative.

Tool receipts establish provenance of an execution, not original-document
identity or semantic support. Reports must say that clearly. Advanced semantic
evaluation cannot be replaced by schema checks or fabricated URL metadata.

The previous form-based iteration had 177 combined backend tests and four browser
tests; those results and screenshots do not prove the new conversation UI. The
current worktree has 188 passing backend tests, six passing presentation tests,
full frontend lint/typecheck, and computer-use observations documented in
COMPUTER_USE_2026-09-15.md. Updated repeatable browser scenarios are written but
not yet counted as passing. Full live research and publication remain open.
