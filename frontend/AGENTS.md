# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with the DeerFlow frontend. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

## Project Overview

DeerFlow Frontend is a Next.js 16 web interface for an AI agent system. It communicates with a LangGraph-based backend to provide thread-based AI conversations with streaming responses, artifacts, and a skills/tools system.

**Stack**: Next.js 16, React 19, TypeScript 5.8, Tailwind CSS 4, pnpm 10.26.2. Requires Node.js 22+ and pnpm 10.26.2+.

### Core dependencies

- **LangGraph SDK** (`@langchain/langgraph-sdk` ^1.5.3) — Agent orchestration and streaming
- **LangChain Core** (`@langchain/core` ^1.1.15) — Fundamental AI building blocks
- **TanStack Query** (`@tanstack/react-query` ^5.90.17) — Server state management
- **UI**: Shadcn UI, MagicUI, React Bits, and Vercel AI SDK elements (generated from registries — see Code Style)

## Commands

| Command          | Purpose                                       |
| ---------------- | --------------------------------------------- |
| `pnpm dev`       | Start the development server with Webpack     |
| `pnpm build`     | Production build                              |
| `pnpm check`     | Lint + type check (run before committing)     |
| `pnpm lint`      | ESLint only                                   |
| `pnpm lint:fix`  | ESLint with auto-fix                          |
| `pnpm format`    | Prettier check (`pnpm format:write` to apply) |
| `pnpm test`      | Run unit tests with Rstest                    |
| `pnpm test:e2e`  | Run E2E tests with Playwright (Chromium)      |
| `pnpm typecheck` | TypeScript type check (`tsc --noEmit`)        |
| `pnpm start`     | Start production server                       |

Unit tests live under `tests/unit/` and mirror the `src/` layout (e.g., `tests/unit/core/api/stream-mode.test.ts` tests `src/core/api/stream-mode.ts`). Powered by Rstest; import source modules via the `@/` path alias.

Webpack is the default development bundler. Use `DEER_FLOW_DEV_BUNDLER=turbo` with `pnpm dev` to opt in to Turbopack when diagnosing a local Next.js bundler issue.

Rstest runs them as two projects (`rstest.config.ts`). `*.test.ts` / `*.test.tsx` run in a plain **node** environment — that is nearly the whole suite, and it is the default for anything that is pure logic. `*.dom.test.ts` / `*.dom.test.tsx` run in **happy-dom**, for tests that need a document: hooks driven through `renderHook` from `@testing-library/react`, and components. Keep the split — a DOM environment costs roughly 3x the runtime of the node suite, so tests that do not render should not opt into it. A hook whose behavior only exists under real React (effect ordering, cleanup on unmount, re-render on store change) belongs in a `.dom.test.*` file rather than a node test that mocks `react` itself.

E2E tests live under `tests/e2e/` and use Playwright with Chromium. They mock all backend APIs via `page.route()` network interception and test real page interactions (navigation, chat input, streaming responses). Config: `playwright.config.ts`. The real-backend auth contract in `tests/e2e-real-backend/auth-disabled-contract.spec.ts` and `backend/tests/test_auth_me_permissions.py` pin the complete route-permission list; update both when adding registered permissions (including `projects:read/write/delete`).

## Architecture

```
Frontend (Next.js) ──▶ LangGraph SDK ──▶ LangGraph Backend (lead_agent)
                                              ├── Sub-Agents
                                              └── Tools & Skills
```

The frontend is a stateful chat application. Users create **threads** (conversations), send messages, set thread-scoped `/goal` completion conditions, and receive streamed AI responses. The backend orchestrates agents that can produce **artifacts** (files/code), **todos**, and goal state updates.

### Source Layout (`src/`)

- **`app/`** — Next.js App Router. Routes include `/` (landing), `/showcase/[thread_id]` (allowlisted public read-only demos), `/workspace/chats/[thread_id]` (authenticated chat), `/workspace/agents/[agent_name]` and `/workspace/agents/new` (custom agents), `/artifacts/view` (chrome-free window that renders Markdown or CSV/TSV artifacts with the panel's own renderer), `/blog/…`, the `(auth)/{login,setup,auth/callback}` flow, `/[lang]/docs/…`, and `/api/…` route handlers (e.g. `/api/memory`).
- **`components/`** — React components:
  - `ui/` — Shadcn UI primitives (auto-generated, ESLint-ignored)
  - `ai-elements/` — Vercel AI SDK elements (auto-generated, ESLint-ignored)
  - `workspace/` — Chat page components (messages, artifacts, settings)
  - `landing/` — Landing page sections
  - `docs/` — Docs / MDX rendering components
- **`core/`** — Business logic, the heart of the app. Domains include `threads/` (creation, streaming, state), `api/` (LangGraph client singleton), `agents/` (custom agents), `subagents/` (runtime worker catalog and administrator mutations), `auth/` (authentication), `artifacts/`, `channels/` (IM connections), `integrations/` (managed third-party integration status/install clients such as Lark CLI), `i18n/` (en-US, zh-CN), `settings/`, `memory/`, `skills/`, `messages/`, `mcp/`, `models/`, `input-polish/` (pre-send draft rewrite API), `voice-input/` (browser speech-recognition helpers), `suggestions/`, `tasks/`, `todos/`, `tools/`, `workspace-changes/` (run-scoped changed-file summaries and diff fetching), `config/`, `notification/`, `blog/`, plus rendering helpers (`rehype/`, `streamdown/`) and `utils/`.
- **`hooks/`** — Shared React hooks
- **`lib/`** — Utilities (`cn()` from clsx + tailwind-merge)
- **`content/`** — MDX content (blog posts, docs) rendered by the app
- **`styles/`** — Global CSS with Tailwind v4 `@import` syntax and CSS variables for theming
- **`typings/`** — Ambient TypeScript declarations
- Root files: `env.js` (env validation), `mdx-components.ts` (MDX component map)

More specific `AGENTS.md` files under `src/` contain the frontend sections split from this file.

## Code Style

DeepResearch reuses `ChatSurface`, `MessageList` (domain-message renderer),
`PromptInput`, and the `ChatBox` extension panel. Keep ordinary chat on the same
shared surface rather than copying its markup into a second workbench. Research
history occupies the native sidebar history slot; do not add an in-page history
list or a separate constraints form. The research stream projection is read-only;
mutations use the authenticated research API, not invented SDK methods.

The research page has its own cool-white palette, measured from ChatGPT deep
research on 2026-09-19: `.deepresearch-surface` in `styles/globals.css` overrides
the theme tokens (`--background #fcfcfc`, `--card #fff`, `--foreground #0d0d0d`,
`--muted-foreground #5d5d5d`, plus `--dr-*` for the third text grey, hairlines,
tracks, chips and the skeleton) inside that scope only, with a `.dark`
counterpart. Never change the global tokens for research. Content portalled out
of the page (hover cards, menus, the mobile Sheet pane) carries the class itself.
Report tables, quotes and Mermaid blocks are styled by unlayered
`.research-report` rules there, because they must win over Streamdown's utility
classes. `prefers-reduced-motion` turns shimmer into plain text and drops every
transition; only the step spinner keeps turning.

Between a sent message and the next card, `waitingPhase` decides what the
conversation shows: `ResearchPlanPending` renders shimmering “正在思考”, then (once
the rewritten request exists, or after 6 s) a 24px-radius skeleton where the plan
card will fade in. It is a synthetic, view-only message; a plan under revision
keeps its own card instead. `planCardHidden` drops a plan's message from the
conversation once its report exists (the stats line and the report card take its
place), except while a follow-up is FAILED or CANCELLED: that card still renders
the error, retry or “研究已停止” notice.

The interaction follows ChatGPT deep research (see
`docs/deepresearch/CHATGPT_BENCHMARK_2026-09-16.md`). The plan card shows the
plan title and short step titles with a ring countdown and “编辑 / 取消 / 开始”.
While research runs it shows step status, the live status from `/activity`, the
search count, a progress bar, stop, and “更新”, which quotes the plan in the
composer and sends a non-interrupting update. Editing uses the same quote bar;
a sent revision starts immediately. Completed reports show the
“研究完成情况” stats line, a report card, and a full-screen reader with a hover TOC.
The side panel tabs are “来源” and “活动 · elapsed”. Use `firstText` for plan/step
labels because historical records store empty strings. Retries send
`retryRequest(...)`: never persist a limited-report refusal the owner did not make.
Failed steps (`unit_failures`, activity `step_failed`) render as failed while the
run continues. The research page passes `runDurationEnabled={false}` to
`MessageList`, because the native per-message duration is not the research time;
the stats line and activity tab own elapsed time. Report Markdown uses GFM
without remark-math (prices contain `$`). Source excerpts go through
`readableExcerpt`, which drops externalization notices and fetch headers.
The “指标” tab (`metrics-panel.tsx`) reads `/metrics`, polling every 5 s only while
the run is active; format numbers with `formatTokens`, `formatCost` and
`formatPercent`, show “—” for unknown values and never compute prices in the client.
Unit citation counts show “—” until a report version exists. Runs with
`metered: false` predate per-call metering: show their unmeasured fields as “—” or
“未记录”, never as zero.
Hovering an in-text citation or a cited source opens `CitationPreview` (340 px,
fade only through `research-fade`): site, a two-line title and a two-line excerpt
for that evidence (`excerptPreview` strips Markdown syntax); several excerpts
under one number switch with ← →. No URL, date or number there. A citation whose
`basis` is `"search excerpt"` shows a small “摘录” note (“检索摘录，未读取原文”) in the
card and in the source list; `"page"`, `"record"` and a missing field show
nothing. Touch devices keep click-to-locate.
Site icons render through `SiteIcon` from the gateway's `/api/deepresearch/favicon`
(never a third-party favicon URL). A failed icon shows a letter badge and retries
once after 4 s with `retry=1`, because a cold icon may still be fetching. The
source list shows the title (a link to the page) and a `sourceSummary` (repeated
title removed), or the URL without scheme when there is no summary.
Source labels go through `sourceTitle` (and `readLabel` in the activity tab): a
missing, "Untitled" or URL-valued title shows the URL without scheme, including
for immutable historical reports.
The activity tab follows new items only while its end sentinel is visible and the
run is live; never pull a reader away from earlier steps.

Plan deadlines are server-owned. Render their remaining time from a server
snapshot plus monotonic elapsed time; do not start research in a browser timer.
Anchor elapsed time when the API response arrives, not when a cached card mounts.
Keep this client-only clock metadata out of server writes and persistent storage.
Fence delayed requests by conversation selection, retain message idempotency keys
after uncertain responses, and compare GET snapshots with the cache after the
request resolves. Reconcile reconnects and refresh terminal sidebar state even
when the backend has no new SSE event. Guard readiness in the send handler as
well as the button so keyboard submit cannot bypass the unavailable state.
Citation recognition must accept the native Markdown sanitizer's anchor prefix
and only bind IDs in the report's citation map. Do not weaken Markdown sanitization.
Historical report cards export their own version.
On mobile, returning from a source to its citation closes the native Sheet before
showing the report location; desktop retains the side-by-side source panel.

`useResearchConversation` opens an event stream only for a run known to be live
(never for a finished run, never before the first snapshot) and starts it at the
snapshot's `last_event_seq`, so opening a running research does not replay its
event history. A stream the browser
gave up on (`readyState` CLOSED after a non-200) is rebuilt with `streamRetryDelay`
(1 s doubling to 15 s, reset by an open or an event); while a live run has no
healthy stream the run and activity queries poll every 5 s. Event-driven refreshes
never cancel the request in flight (`cancelRefetch: false` plus one trailing
refresh), or sustained events starve the view. Terminal status re-reads sources,
activity, metrics and LLM calls once. A 4xx (`isRejection`) drops the message or
creation idempotency key; transport failures and 5xx keep it. `commitUrl` rewrites
the address without a route change, so the hook follows `usePathname()` back to
the root path or to another run (`runIdFromPath`) and ignores a pathname that
trails `window.location`.
The composer (`ResearchComposer`) takes a message only when `composerAccepts`
says the server would: FAILED and CANCELLED disable the textarea and say why, and
the submit handler rejects (keeping the draft) because Enter reaches it even when
no submit button is rendered. Every other button inside the form is
`type="button"`. A failed or stopped follow-up no longer shows its plan (the
report replaced it) but still renders its error, retry or “研究已停止” notice.
Report Markdown never renders `<img>`; `components.img` keeps the alt text only,
because a remote (including protocol-relative) image URL is an exfiltration
channel for injected page text. The reader's table of contents is read from the
rendered headings (h1–h3, not from Markdown lines), follows the scroll position
with `activeHeadingIndex`, and opens from a focusable “目录” button; the open list
replaces the tick rail (the button stays focusable underneath). The article is a
624 px column; the rail shows only while the reader's own container (`@3xl`, not
the viewport: the side panel narrows it) leaves a 64 px gutter, so it can never
overlap the article. The reader fades in and out (300 ms), folds the sidebar
while open without touching the stored sidebar preference, shows no title in
its bar, and draws the bar's hairline only after the article scrolls
(`onScrolledChange`, `ChatSurface.headerClassName`). The side panel opens 375 px
wide through `ChatBox`'s `extensionPanel.defaultSize`; the panel library treats
string sizes passed to `resize()` as percentages, so a pixel default is applied
as a number and re-applied when the open animation ends. A selected source is
only highlighted (rounded tinted block); every entry stays one title line plus
a two-line `sourceSummary`, with the URL only when there is no summary, and the
clicked in-text marker turns solid through a context, never through a new
Markdown `components` map. “已扫描的来源” lists uncited pages in the open, grouped
by site like the citations, deduped with `pageKey`, which mirrors the server's
page identity. The activity tab has two row styles only: a dot with a hairline
to the next entry for progress, and a globe with site pills (four, then “再显示
N 个”) for searches and reads; consecutive reads fold into one entry.

Fetch trace payloads on expansion with the forward cursor, and key the panel by
run ID so responses from a previous selection cannot populate another run's trace.
Keep export requests on the authenticated research API; do not put trace payloads
in localStorage or send them to a telemetry service. One load pages through the
trace (`limit=200`) until a short page or 5000 events, then shows “还有更多”; the
timeline marks are `tabIndex={-1}` because the span list reaches every span.
The metrics tab's “按节点” table reads `breakdown.by_node` (labels from
`nodeLabel`), flags truncated / format-retry / error counts above zero, and lists
`budget.earlier_tasks`, because budgets are per task and a follow-up opens a new one.
Its “缓存命中 / 可复用” column pairs `cache_read_ratio` (what the provider served
from its prompt cache) with `prefix_reuse_ratio` (the share of the prompt that
repeated the previous request of its thread, i.e. the ceiling): a low ceiling
blames request construction, a high ceiling with few hits blames the model
service. Both are optional and render “—” for records that predate them.

The inspector has two tabs: “LLM 调用” (`llm-calls-panel.tsx` + `llm-call-dialog.tsx`)
and “Trace 时间线”. Audit calls group by execution (`groupCalls` in
`core/deepresearch/llm-calls.ts`): a call carrying a `node` label is that node's own
work (context compaction, a graph node) and never counts as an agent turn. The
dialog's “只看本轮新增” filter uses the server's `new_message_indexes`; never
recompute a diff in the client. A run created before auditing shows
`audited: false` — say so instead of rendering an empty prompt. The rewritten
request renders through `request-card.tsx` (message kind `rewrite`), collapsed by
default, with copy and inspect actions.

`research-settings.tsx` and `components/deepresearch/settings/` own the research
settings page (`/workspace/deepresearch/settings`). It edits a draft copy
(`edit()` clones), pins the server version on the first edit so a save can only
succeed against the version the draft was based on (409 shows “载入最新版本”), and
surfaces `draftProblems` before the request. Secrets are write-only: send a value,
never read one back, and show reference status from the server. Field components
in `settings/fields.tsx` keep local text while it still describes the draft value
(numbers typed as text, JSON, one-per-line lists) so typing is never reformatted
mid-keystroke. Settings apply to research created afterwards; say that in the UI
rather than implying a running study changes. `report_length_scale` (0.2–3.0,
`REPORT_LENGTH_SCALE`) is optional in the type because an older gateway omits
it; the field shows 1.0 then and `draftProblems` checks the range.
Conventions the settings page depends on:
- `settings/nodes-section.tsx` renders one card per `catalog.nodes` entry, in
  workflow order. A node has a key in `nodes` only while it differs from full
  inheritance (`editNode` removes the key again); an older gateway without
  `catalog.nodes` gets a notice instead of cards.
- Renames and removals go through the pure helpers in `core/deepresearch/settings.ts`
  (`renameModel`, `removeModel`, `renameSource`, `renameSourceTool`, `removeSource`,
  `renameMcpServer`). They move every reference (default/rewrite/extraction/
  compaction model, `nodes[*].model`, role models and tool allowlists,
  `source_fallback`, MCP `server` bindings) and never take over another entry's
  references while a name typed halfway collides with it.
- Cards are keyed by `rowKey` (object identity carried across `edit()` clones),
  never by array index, and the key never reaches the saved payload.
- `NumberField` and `JsonField` report invalid text through the `InvalidFields`
  context; it counts toward the save bar's problems, so a value the screen shows
  but the draft does not hold cannot be saved around.
- The secrets callbacks merge only `secrets` into the view (`mergeSecrets`);
  restore and reset adopt the server's response and drop the draft.
- Header and environment values whose name looks like a credential must be
  references (`$ENV`, `secret:NAME`, or interpolated `${...}`); the backend
  refuses literals as well.

- **Imports**: Enforced ordering (builtin → external → internal → parent → sibling), alphabetized, newlines between groups. Use inline type imports: `import { type Foo }`.
- **Unused variables**: Prefix with `_`.
- **Class names**: Use `cn()` from `@/lib/utils` for conditional Tailwind classes.
- **Path alias**: `@/*` maps to `src/*`.
- **Components**: `ui/` and `ai-elements/` are generated from registries (Shadcn, MagicUI, React Bits, Vercel AI SDK) — don't manually edit these.

## Environment

Backend API URLs are optional; an nginx proxy is used by default:

```
NEXT_PUBLIC_BACKEND_BASE_URL=http://localhost:8001
NEXT_PUBLIC_LANGGRAPH_BASE_URL=http://localhost:8001/api
```

Leave these unset for the standard `make dev` / Docker flow, where nginx serves the public `/api/langgraph/*` prefix and rewrites it to Gateway's native `/api/*` routes.

`make build-static` creates a standalone read-only demo and copies `.next/static`
and `public` into the output. In static mode, `core/api/static-response.ts`
resolves Gateway REST reads with empty capability/catalog responses or existing
same-origin `/mock/api` fixtures; writes and unknown API routes fail locally.
The homepage client counter calls `/github-stars`, outside the Gateway proxy.
That dynamic route reads the server-only `GITHUB_OAUTH_TOKEN` at runtime, caches
GitHub data for one hour, and returns 204 when the count is unavailable. Start
the standalone server from `frontend/` with `node --env-file=.env
.next/standalone/server.js` to load the current credentials.

To reach a dev server on anything other than localhost — a LAN address, or a proxied hostname — list the host in `DEER_FLOW_DEV_ALLOWED_ORIGINS` (comma-separated; a full URL is reduced to its host). It feeds Next's `allowedDevOrigins`, which gates `/_next/*`, fonts, and HMR. Without it those requests get a 403 and the page renders server-side but never hydrates, so nothing on it — including the login form — responds. Development only; production builds ignore it.

## Resources

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [LangChain Core Concepts](https://js.langchain.com/docs/concepts)
- [TanStack Query Documentation](https://tanstack.com/query/latest)
- [Next.js App Router](https://nextjs.org/docs/app)

## Contributing

When adding features:

1. Follow the established `src/` structure
2. Add TypeScript types and proper error handling
3. Write unit tests under `tests/unit/` (`pnpm test`) and E2E tests under `tests/e2e/` (`pnpm test:e2e`)
4. Run `pnpm check` before committing
5. Update this `AGENTS.md` when architecture, commands, or conventions change

Route asset budgets are enforced with `pnpm perf:check`. The command measures
`/login` from a normal production build, then builds in static-demo mode for the
fixture-backed workspace routes. It starts the production server on temporary local
ports, measures the unique JavaScript and CSS files referenced by representative
routes, writes the detailed result to `.next/performance-results.json`, and compares
totals with `performance-budgets.json`. Fix route ownership or split points when a
budget fails; do not raise a ceiling without documenting and reviewing the measured
regression.

Chat archive is a thread metadata flag (`deerflow_archived === true`), independent
of run status. Sidebar and Chats explicitly request the Gateway's optional
`archived` filter through `searchThreadsByArchive`; the SDK drops this extension,
so use the authenticated REST fetcher. Static demos retain SDK fixture queries.
`core/threads/archive.ts` waits for the write, cancels stale reads, merges only the
owned flag into metadata snapshots, then restarts metadata reads and resets list
pagination. Keep both default and Custom Agent header restore controls in sync.
Pin/archive responses must not merge unrelated metadata flags: out-of-order
organization requests can otherwise roll back each other's confirmed state.
Run-created optimistic snapshots have no archive flag: refresh archive-filtered
lists from the server instead of inserting those snapshots into either view.

### Delimited artifact preview

CSV/TSV previews share `artifact-table-preview.tsx` between the panel and standalone viewer. Papa Parse runs only inside `delimited-preview.worker.ts`; `use-delimited-preview.ts` bounds input before transfer, cancels stale work, and enforces a five-second timeout. The parser detects the first record separator outside quoted fields and passes it explicitly to Papa Parse, so embedded newlines in an incomplete quoted field cannot corrupt newline detection. It retains at most 202 logical records and 50 columns, discarding an incomplete final record from truncated input. UI pagination displays at most 200 data rows in pages of 50. Keep the table mounted but inactive when switching to source so header/pagination state survives; changing file identity resets it. Pending `write_file` content stays in source mode until success.

DeepResearch report citations may share a display number across multiple recorded
excerpts of the same page. Use `uniqueCitationIds` per paragraph/table cell, keep
all citation `evidence_ids` aliases selectable, and choose the selected excerpt in
the source panel. Comparison-table cells retain citation buttons. Discovered
sources are listed separately from cited pages; read status is not a claim
that an entire document or all its assertions have been verified.

For evidence-bearing `RESEARCH_GAPS` failures, the existing research plan card
must expose explicit limited-report consent using the authenticated retry API.
Do not silently opt in, treat citation failures as evidence gaps, or offer this
path for an evidence-free run. The report must still pass backend reference and
source-policy validation. Other unrecoverable failures retain disabled retry.
