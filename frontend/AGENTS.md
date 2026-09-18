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
Hovering an in-text citation or a cited source opens `CitationPreview`: site,
title and the excerpt for that evidence (`excerptPreview` strips Markdown syntax),
labeled as page text only for `fetched_document`. Touch devices keep click-to-locate.
Site icons render through `SiteIcon` from the gateway's `/api/deepresearch/favicon`
(never a third-party favicon URL). A failed icon shows a letter badge and retries
once after 4 s with `retry=1`, because a cold icon may still be fetching. The
source list shows the title, a `sourceSummary` (repeated title removed) and the
URL without scheme.
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

Fetch trace payloads on expansion with the forward cursor, and key the panel by
run ID so responses from a previous selection cannot populate another run's trace.
Keep export requests on the authenticated research API; do not put trace payloads
in localStorage or send them to a telemetry service.

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
rather than implying a running study changes.

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
sources are collapsed separately from cited pages; read status is not a claim
that an entire document or all its assertions have been verified.

For evidence-bearing `RESEARCH_GAPS` failures, the existing research plan card
must expose explicit limited-report consent using the authenticated retry API.
Do not silently opt in, treat citation failures as evidence gaps, or offer this
path for an evidence-free run. The report must still pass backend reference and
source-policy validation. Other unrecoverable failures retain disabled retry.
