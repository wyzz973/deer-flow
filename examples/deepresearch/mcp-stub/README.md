# MCP stub for acceptance runs

A small MCP server (streamable HTTP) that behaves like internal search tools usually do, so a complete research can be rehearsed
without the real ones:

- it requires **both** `Authorization: Bearer <token>` and `Cookie: sid=<value>` and answers 401 otherwise;
- three search tools answer in three shapes: JSON chunks without title or link (`search_docs`), `Title:/URL:` text blocks
  (`search_wiki`) and a structured envelope with its own `name`/`description` (`search_tickets`);
- **no tool can open an original document**, so search results and records are the evidence;
- `find_pages` + `open_page` rehearse a search and a fetch tool exposed to research **directly** (`kind: mcp`, overlay
  `research-overlay-passthrough.yaml`): results under unusual field names, the page as escaped JSON in an envelope, and nothing
  that says "this is a page". Research recognises the page from the call's `url` argument and cites it with title and address;
  both state the page's date in a field (`published_at`), as [the response format](../../../docs/deepresearch/MCP_RESPONSE_FORMAT.md) recommends, so the
  reference list can show it; `find_pages` also states the site's own logo (`logo_url`, same host), which the Gateway fetches and shows
  as the site icon instead of a letter badge;
- `delete_page` exists only to prove that `allowed_tools` keeps research away from it;
- `MCP_STUB_LATENCY` (seconds per call) rehearses slow tools and the time budget; `MCP_STUB_LOG` writes one JSON line per call.

```sh
# from the repository root
cd backend && MCP_STUB_LOG=/tmp/mcp-stub-calls.jsonl uv run --no-sync python ../examples/deepresearch/mcp-stub/server.py 9731

# in another terminal: an isolated acceptance gateway that only has these MCP tools
MCP_STUB_TOKEN=tok-acceptance-12345 uv run --directory backend --no-sync python -m deepresearch.live --allow-live \
  --model <a model name from config.yaml> --research-overlay examples/deepresearch/mcp-stub/research-overlay.yaml --port 8011
curl -X POST http://127.0.0.1:8011/api/deepresearch/settings/secrets -H 'content-type: application/json' \
  -d '{"name": "kb-cookie", "value": "sess-acceptance-67890"}'
```

The overlay reaches the model through a plain OpenAI-compatible Chat Completions endpoint (`provider: openai` + `base_url`);
change `models` to the gateway under test. Results of the 2026-09-19 runs are in `docs/deepresearch/HANDOFF.md`.
