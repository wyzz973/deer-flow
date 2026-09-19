# MCP stub for acceptance runs

A small MCP server (streamable HTTP) that behaves like internal search tools usually do, so a complete research can be rehearsed
without the real ones:

- it requires **both** `Authorization: Bearer <token>` and `Cookie: sid=<value>` and answers 401 otherwise;
- three search tools answer in three shapes: JSON chunks without title or link (`search_docs`), `Title:/URL:` text blocks
  (`search_wiki`) and a structured envelope with its own `name`/`description` (`search_tickets`);
- **no tool can open an original document**, so search results and records are the evidence;
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
