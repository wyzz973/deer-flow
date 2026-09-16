# DeepResearch computer-use acceptance, 2026-09-15

## Status

Partial acceptance only. This is not a release sign-off and does not establish
that the configured DeepSeek provider or a real research source is reachable.
No live DeepSeek request was made in the initial pass. See the follow-up below
for the subsequent minimal connectivity check; full live research is still open.

## Environment and scope

- Checkout: `/Users/sd3/Desktop/project/deer-flow`.
- Synthetic backend: loopback port 8022, `deepresearch.demo:app`.
- Frontend: loopback port 3100. The pre-existing service on port 3000 was left alone.
- Isolated synthetic data configuration: `.deerflow/deepresearch/computer-use`.
- Synthetic run: `99db1b81-a0a1-47a7-be62-1cc30dc310ee`.
- Browser actions used the Codex computer-use API, not injected page state.
- Both `/deepresearch-demo` and `/workspace/deepresearch` were visited.
- The mobile viewport was 390 by 844; the override was reset afterward.

The local host configuration contains DeepSeek model entries and nonempty
credential configuration fields. Credential values were not printed. The host
has no configured DeepResearch plugin, no `deepresearch.local.yaml`, and no
enabled MCP server in `extensions_config.json`. Native `web_search` and
`web_fetch` tools are configured, but their availability was not tested.

## Automated checks

| Check | Result |
| --- | --- |
| Research and native SubagentExecutor pytest suites | 183 passed in 2.93 seconds |
| Backend research Ruff check | Passed |
| Frontend full ESLint and TypeScript check | Passed after import and type-style corrections |

Commands were run from the repository root:

```sh
uv run --directory backend --no-sync ruff check deepresearch ../tests/deepresearch
uv run --directory backend --no-sync python -m pytest tests/test_subagent_executor.py ../tests/deepresearch -q
python3 scripts/pnpm.py check
```

The older `frontend/tests/deepresearch/workbench.spec.ts` still targets the
superseded form-based interface. It was not run or counted as passing here.

## Observed browser results

| Interaction | Observation | Status |
| --- | --- | --- |
| Submit a research question | A user message and a plan card appeared in one conversation | Passed, synthetic |
| Edit a pending plan | Countdown controls changed to an editing state | Passed |
| Reload while editing | Editing state and the same run ID were restored | Passed |
| Revise through the composer | Original plan collapsed; a new plan version appeared in the same run | Passed, synthetic |
| Allow countdown to expire | Research started without clicking the start button and produced a report | Passed, synthetic |
| Activity panel | Four recorded synthetic calls, Agent labels, durations, and external domain chips appeared | Passed, synthetic |
| Source panel | Four references were grouped separately from two discovered external links | Passed, synthetic |
| Expanded report | Full report and a return-to-conversation control appeared | Passed, synthetic |
| Inline citation | Clicking citation 1 did not select or locate its source | Failed |
| Trace | Timeline, hierarchical node records, search, and dispatch input/output details appeared | Passed, synthetic |
| Trace export | Clicked export, but no browser download event arrived within 15 seconds | Unconfirmed |
| Native workspace history | The run appeared in the native sidebar and its route restored the report | Passed, synthetic |
| Mobile welcome and details | Composer and native sidebar rendered; research details opened in a sheet | Passed |
| Mobile width | Document width equaled viewport width (390); details sheet stayed within the viewport | Passed |

## Issues and remaining work

1. The configured 45-second countdown initially displayed 46 seconds in the
   observed run. Check server-time alignment and display rounding separately
   from the authoritative start deadline.
2. Inline citations were rendered with anchors such as
   `#user-content-citation-E002`. The click did not activate the source panel.
   Investigate the native Markdown sanitizer and citation-renderer boundary;
   do not weaken Markdown sanitization to fix the interaction.
3. Trace export remains unconfirmed. No browser console warning or error was
   observed after the click. A missing automation download event alone does
   not prove that the export API failed.
4. On mobile, navigating from research history left the sidebar open. The
   header toggle did not dismiss it in the observed interaction; Escape did.
   Compare with the existing native sidebar behavior before changing ownership.
5. The synthetic planner records revision text but does not demonstrate model
   understanding of the requested three-step research scope.
6. Real DeepSeek execution, real tool results, report quality, and original
   MCP-result compatibility still require live acceptance. Resolve whether to
   use native web tools or a user-specified MCP source first. Never silently
   fall back to synthetic results.
7. Update repeatable browser tests for the conversation-based UI, cover
   follow-up/report-version behavior, and complete the remaining goal checks
   before committing, publishing, or reporting the overall goal complete.

The user was asked whether to repair the citation/countdown issues and which
source configuration to use for live acceptance. Those decisions were pending
when this record was written.

## Follow-up: repaired interactions and additional evidence

- Backend regression: 188 passed in 3.19 seconds. Added native-source identity
  and ceiling checks, report-version exports, follow-up routing, and duplicate
  approval coverage. Rewrites and explanations do not repeat synthetic search;
  new research advances the cycle and does not reuse the earlier unit cache.
- Presentation regression: six tests passed for native-sanitized citation
  anchors, unregistered IDs, monotonic countdown display and reload snapshots.
- Full frontend ESLint/TypeScript passed after the interaction changes.
- Computer use on the first run now renders citation buttons. Clicking reference
  1 opens the Sources tab, selects `E002`, and shows its actual recorded snippet.
  Native Markdown sanitization remains enabled.
- A fresh synthetic run, `0a09393c-9e85-43aa-aea5-30ff58e7736c`, displayed 45 seconds
  initially and 39 seconds after reload, then completed without manual approval.
- Selecting that run in the mobile native sidebar closes the mobile sheet; the
  closed state was awaited and the report was restored at its workspace route.
- The earlier Trace download did succeed: the uniquely named file in the local
  Downloads directory contains 32 valid JSONL events for the expected run,
  comprising 16 starts and 16 ends. The automation download-event wait was the
  incomplete observation, not evidence of an export API failure.
- Existing `deepseek-v4-flash` credentials were used through the native model
  factory, with external tracing disabled and bounded output/time. The provider
  returned exactly `OK` in 0.93 seconds (13 input, 1 output, 14 total tokens).
  An earlier probe had a local constructor argument error and sent no request;
  the successful probe used the factory's documented `model_overrides` path.
- The local config loader warned that config version 36 is older than 40. No
  automatic config migration or credential rewrite was performed.
- The repeatable browser suite now targets conversation cards, source linking,
  follow-ups, exports, server deadlines and mobile history. Its scenarios are
  written and typechecked, but its automated execution is not yet counted here.

Still pending: real Gateway extension/source setup and full DeepSeek research,
final browser/regression gates, any agreed in-progress steering behavior, and
the scoped commit/push with CI for that exact revision.
