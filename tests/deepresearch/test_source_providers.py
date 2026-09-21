"""Research sources: format-independent extraction, provider presets and failover."""

import json
import sys
import types

import httpx
import pytest

from deepresearch import channels, extract, providers
from deepresearch.config import McpServerSpec, ProviderSpec, SourceSpec
from deepresearch.observations import NativeExecution, research_observations
from deepresearch.providers import ProviderError, Request, classify


@pytest.fixture(autouse=True)
def clean_state():
    channels.HEALTH.reset()
    yield
    channels.HEALTH.reset()


def mock_http(monkeypatch, handler):
    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)


def test_records_are_found_in_unrelated_result_formats():
    tavily = {"query": "q", "results": [{"title": "SQLite WAL", "url": "https://sqlite.org/wal.html", "content": "WAL allows readers", "score": 0.9}]}
    serper = {"searchParameters": {"q": "q"}, "organic": [{"title": "Postgres", "link": "https://postgresql.org", "snippet": "MVCC", "date": "2026-01-02"}], "relatedSearches": [{"query": "x"}]}
    bocha = {"code": 200, "data": {"webPages": {"value": [{"name": "博查结果", "url": "https://example.cn/a", "summary": "摘要", "datePublished": "2026-09-01"}]}}}
    # The search/fetch interface OpenAI documents for deep-research MCP servers.
    openai_mcp = json.dumps({"results": [{"id": "doc-1", "title": "Quarterly report", "url": "https://intranet.example/doc-1"}]})
    markdown = "Results:\n- [First](https://a.example/1) is about caching\n- [Second](https://b.example/2)"
    labeled = "Title: Guide\nURL: https://c.example/guide\nPublished: 2026\nA practical guide.\n\nTitle: Other\nURL: https://d.example/x\nMore text."
    assert extract.records(tavily) == [{"title": "SQLite WAL", "url": "https://sqlite.org/wal.html", "snippet": "WAL allows readers"}]
    assert extract.records(serper)[0] == {"title": "Postgres", "url": "https://postgresql.org", "snippet": "MVCC", "published_at": "2026-01-02"}
    assert extract.records(bocha)[0]["title"] == "博查结果" and extract.records(bocha)[0]["published_at"] == "2026-09-01"
    assert extract.records(openai_mcp) == [{"title": "Quarterly report", "url": "https://intranet.example/doc-1", "snippet": "", "id": "doc-1"}]
    assert [item["url"] for item in extract.records(markdown)] == ["https://a.example/1", "https://b.example/2"]
    assert [item["title"] for item in extract.records(labeled)] == ["Guide", "Other"]
    # MCP content blocks carrying JSON text.
    blocks = [{"type": "text", "text": json.dumps({"items": [{"name": "Doc", "href": "https://e.example", "description": "d"}]})}]
    assert extract.records(extract.content_text(blocks))[0]["url"] == "https://e.example"


def test_documents_keep_title_address_and_the_longest_text():
    jina = {"code": 200, "data": {"title": "Write-Ahead Logging", "url": "https://sqlite.org/wal.html", "content": "# WAL\n" + "text " * 50}}
    firecrawl = {"success": True, "data": {"markdown": "body " * 40, "metadata": {"title": "Firecrawl page", "sourceURL": "https://f.example"}}}
    openai_fetch = {"id": "doc-1", "title": "Quarterly report", "text": "Revenue grew " * 10, "url": "https://intranet.example/doc-1", "metadata": {"source": "kb"}}
    assert extract.document(jina) == {"title": "Write-Ahead Logging", "url": "https://sqlite.org/wal.html", "text": jina["data"]["content"], "readable": True}
    # A string answer arrives wrapped by the MCP adapter, and is often JSON itself: the page is two levels down.
    assert extract.document({"result": json.dumps(jina)}) == extract.document(jina)
    # No field held the text: the whole answer stands in, and says so.
    assert extract.document({"status": "ok", "rows": [1, 2]})["readable"] is False
    assert extract.document(firecrawl)["title"] == "Firecrawl page" and extract.document(firecrawl)["url"] == "https://f.example"
    assert extract.document(openai_fetch)["text"].startswith("Revenue grew")
    assert extract.document("# Heading here\n\nParagraph", "https://g.example")["title"] == "Heading here"
    assert extract.document("<html><title>HTML Title</title><body>x</body></html>")["title"] == "HTML Title"


def test_failures_are_classified_without_vendor_schemas():
    assert classify(429) == "rate_limit" and classify(432) == "quota" and classify(402) == "quota"
    assert classify(403, '{"detail": "Invalid API key"}') == "auth" and classify(403, '{"detail":"usage limit exceeded for plan"}') == "quota"
    assert classify(403, "<!DOCTYPE html><title>Just a moment...</title>") == "server"
    assert classify(200 if False else 400, "请求过于频繁") == "rate_limit" and classify(503) == "server" and classify(404) == "not_found"


@pytest.mark.asyncio
async def test_presets_send_their_api_shape_and_report_typed_errors(monkeypatch):
    monkeypatch.setenv("TEST_SEARCH_KEY", "sk-provider-secret")
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content or b"{}")
        if request.url.host == "api.tavily.com":
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t.example", "content": body["query"]}]})
        if request.url.host == "google.serper.dev":
            return httpx.Response(429, headers={"retry-after": "7"}, json={"message": "Too many requests"})
        if request.url.host == "api.bochaai.com":
            return httpx.Response(200, json={"code": 403, "msg": "余额不足 sk-provider-secret", "data": None})
        if request.url.host == "kb.example":
            return httpx.Response(200, json={"hits": [{"doc_name": "内部制度", "text": "审批流程"}]})
        return httpx.Response(500)

    mock_http(monkeypatch, handler)
    tavily = ProviderSpec(id="t", type="tavily", api_key="$TEST_SEARCH_KEY")
    outcome = await providers.call(tavily, Request("search", query="sqlite", max_results=3, time_range="week"))
    assert outcome.records == [{"title": "T", "url": "https://t.example", "snippet": "sqlite"}]
    sent = json.loads(seen[-1].content)
    assert seen[-1].headers["authorization"] == "Bearer sk-provider-secret" and sent["time_range"] == "week" and sent["max_results"] == 3
    with pytest.raises(ProviderError) as limited:
        await providers.call(ProviderSpec(id="s", type="serper", api_key="$TEST_SEARCH_KEY"), Request("search", query="x"))
    assert limited.value.kind == "rate_limit" and limited.value.retry_after == 7
    with pytest.raises(ProviderError) as unpaid:
        await providers.call(ProviderSpec(id="b", type="bocha", api_key="$TEST_SEARCH_KEY"), Request("search", query="x"))
    assert unpaid.value.kind == "quota" and "sk-provider-secret" not in str(unpaid.value)
    with pytest.raises(ProviderError) as missing:
        await providers.call(ProviderSpec(id="m", type="exa", api_key="$NOT_SET_ANYWHERE"), Request("search", query="x"))
    assert missing.value.kind == "config" and "NOT_SET_ANYWHERE" in str(missing.value)
    custom = ProviderSpec(id="kb", type="http", method="POST", url="https://kb.example/search", headers={"X-Key": "$TEST_SEARCH_KEY"}, body={"q": "{query}", "size": "{max_results}"})
    records = await providers.call(custom, Request("data", query="报销", max_results=5))
    assert records.records == [{"title": "内部制度", "url": None, "snippet": "审批流程"}]
    assert json.loads(seen[-1].content) == {"q": "报销", "size": 5} and seen[-1].headers["x-key"] == "sk-provider-secret"


def search_source(*ids):
    return SourceSpec(name="web", tool="web_search", role="search", origin="external", providers=[{"id": item, "type": "http", "url": f"https://{item}.example/search"} for item in ids])


@pytest.mark.asyncio
async def test_search_fails_over_cools_down_and_explains_total_failure(settings, monkeypatch):
    calls = []

    async def fake(provider, request, servers=None, request_secrets=None):
        calls.append(provider.id)
        if provider.id == "first":
            raise ProviderError("rate_limit", "HTTP 429: slow down", status=429, retry_after=60)
        if provider.id == "empty":
            return providers.Outcome(records=[])
        return providers.Outcome(records=[{"title": "Answer", "url": "https://answer.example", "snippet": "found"}], raw="{}")

    monkeypatch.setattr(channels, "call", fake)
    tool = channels.build_tool(search_source("first", "empty", "second"), settings, "run")
    message = await tool.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "q"}, "id": "c1"})
    assert message.status == "success" and "[Answer](https://answer.example)" in message.content
    assert [(item["provider"], item["status"]) for item in message.artifact["attempts"]] == [("first", "error"), ("empty", "empty"), ("second", "ok")]
    assert message.artifact["provider"] == "second"
    # The rate-limited provider cools down: the next call starts with the others.
    calls.clear()
    await tool.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "q2"}, "id": "c2"})
    assert calls == ["empty", "second"]
    cooling = {item["provider"]: item for item in channels.HEALTH.snapshot()}
    assert cooling["first"]["cooling"] and cooling["first"]["last_error_kind"] == "rate_limit"

    async def broken(provider, request, servers=None, request_secrets=None):
        raise ProviderError("quota" if provider.id == "a" else "timeout", f"{provider.id} failed")

    monkeypatch.setattr(channels, "call", broken)
    failing = channels.build_tool(search_source("a", "b"), settings, "run")
    failed = await failing.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "q"}, "id": "c3"})
    assert failed.status == "error" and "All providers failed for web_search" in failed.content and "a (quota" in failed.content
    # A quota failure is not retried as a last resort; a timeout is.
    calls_after = []

    async def recorder(provider, request, servers=None, request_secrets=None):
        calls_after.append(provider.id)
        raise ProviderError("timeout", "again")

    monkeypatch.setattr(channels, "call", recorder)
    await failing.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "q"}, "id": "c4"})
    assert calls_after == ["b"]


@pytest.mark.asyncio
async def test_reads_page_from_one_fetch_and_register_the_original_page(settings, monkeypatch):
    fetches = []

    async def fake(provider, request, servers=None, request_secrets=None):
        fetches.append(request.url)
        return providers.Outcome(document={"title": "Guide", "url": request.url, "text": "Intro. " + "Detail section. " * 400 + "Pricing: 5 USD."})

    monkeypatch.setattr(channels, "call", fake)
    source = SourceSpec(name="reader", tool="web_fetch", role="read", origin="external", providers=[{"id": "reader", "type": "direct"}])
    tool = channels.build_tool(source, settings, "run-read")
    first = await tool.ainvoke({"type": "tool_call", "name": "web_fetch", "args": {"url": "https://docs.example/guide", "max_length": 1000}, "id": "r1"})
    assert first.artifact["schema"] == "deerflow.web_page.v1" and first.artifact["next_start_index"] == 1000 and first.artifact["provider"] == "reader"
    section = await tool.ainvoke({"type": "tool_call", "name": "web_fetch", "args": {"url": "https://docs.example/guide", "query": "Pricing", "max_length": 1000}, "id": "r2"})
    assert "Pricing: 5 USD." in section.content and section.artifact["attempts"][0]["status"] == "cache"
    assert fetches == ["https://docs.example/guide"]

    execution = NativeExecution(
        answer="notes",
        execution_id="exec",
        messages=[
            {"type": "ai", "tool_calls": [{"id": "r1", "name": "web_fetch", "args": {"url": "https://docs.example/guide"}}]},
            {"type": "tool", "name": "web_fetch", "tool_call_id": "r1", "content": first.content, "artifact": first.artifact},
        ],
    )
    evidences, _ = research_observations(execution, [source])
    page = next(item for item in evidences if item.provenance == "fetched_document")
    assert page.url == "https://docs.example/guide" and page.title == "Guide"


@pytest.mark.asyncio
async def test_knowledge_records_become_separately_citable_evidence(settings, monkeypatch):
    async def fake(provider, request, servers=None, request_secrets=None):
        return providers.Outcome(records=[{"title": "报销制度 v3", "url": None, "snippet": "差旅报销需在 30 天内提交。"}, {"title": "采购规范", "url": "https://wiki.example/buy", "snippet": "单笔超过 5 万元需招标。"}])

    monkeypatch.setattr(channels, "call", fake)
    source = SourceSpec(name="kb", tool="knowledge_search", role="data", origin="internal", level="L1", providers=[{"id": "kb", "type": "ragflow", "base_url": "http://127.0.0.1:9380"}])
    tool = channels.build_tool(source, settings, "run-kb")
    message = await tool.ainvoke({"type": "tool_call", "name": "knowledge_search", "args": {"query": "报销"}, "id": "k1"})
    assert message.artifact["schema"] == "deepresearch.records.v1" and "[1] 报销制度 v3" in message.content
    execution = NativeExecution(
        answer="notes",
        execution_id="exec",
        messages=[
            {"type": "ai", "tool_calls": [{"id": "k1", "name": "knowledge_search", "args": {"query": "报销"}}]},
            {"type": "tool", "name": "knowledge_search", "tool_call_id": "k1", "content": message.content, "artifact": message.artifact},
        ],
    )
    evidences, catalog = research_observations(execution, [source])
    records = [item for item in evidences if item.raw_id.startswith("rec_")]
    assert [item.title for item in records] == ["报销制度 v3", "采购规范"]
    assert records[1].url == "https://wiki.example/buy" and records[0].origin == "internal" and records[0].source_level == "L1"
    assert any(entry.get("superseded") for entry in catalog if not entry["raw_id"].startswith("rec_"))


@pytest.mark.asyncio
async def test_mcp_providers_map_arguments_from_the_tool_schema(settings, monkeypatch):
    class Tool:
        name = "search_docs"
        args = {"q": {"type": "string"}, "limit": {"type": "integer"}}

    calls = []

    class Manager:
        async def tool(self, server, spec, name, *, request=None):
            return Tool()

        async def call(self, tool, arguments):
            calls.append(arguments)
            return [{"type": "text", "text": "Found 2 documents:\n\nTitle: Plan\nURL: https://intra.example/plan\nThe plan.\n\nTitle: Budget\nURL: https://intra.example/budget\nNumbers."}], None, "success"

    monkeypatch.setattr("deepresearch.mcp.MANAGER", Manager())
    settings = settings.model_validate({**settings.model_dump(), "mcp_servers": {"kb": {"transport": "http", "url": "http://127.0.0.1:9000/mcp"}}})
    provider = ProviderSpec(id="kb", type="mcp", server="kb", tool="search_docs")
    outcome = await providers.call(provider, Request("search", query="budget", max_results=4), servers=settings.mcp_servers)
    assert calls == [{"q": "budget", "limit": 4}]
    assert [item["title"] for item in outcome.records] == ["Plan", "Budget"]


def test_provider_attempts_are_summarized_for_cost_and_reliability():
    from deepresearch.metrics import summarize

    run = {"run_id": "r", "status": "COMPLETED", "cycle": 0, "created_at": "2026-09-18T00:00:00+00:00", "updated_at": "2026-09-18T00:10:00+00:00", "usage": {"tool_calls": 3}, "budget": {}}
    exhausted = "Error: All providers failed for web_search: tavily (skipped: quota cooldown 3000s); serper (rate_limit: HTTP 429)"
    tavily_quota = {"provider": "tavily", "type": "tavily", "status": "error", "kind": "quota", "ms": 300}
    tavily_skipped = {"provider": "tavily", "type": "tavily", "status": "skipped", "kind": "quota"}
    calls = [
        {"id": "c1", "tool_name": "web_search", "role": "search", "status": "success", "provider": "serper", "failovers": 1, "attempts": [tavily_quota, {"provider": "serper", "type": "serper", "status": "ok", "ms": 700}]},
        {"id": "c2", "tool_name": "web_search", "role": "search", "status": "success", "provider": "serper", "failovers": 0, "attempts": [tavily_skipped, {"provider": "serper", "type": "serper", "status": "ok", "ms": 500}]},
        {"id": "c3", "tool_name": "web_search", "role": "search", "status": "error", "failovers": 2, "attempts": channels.failed_attempts(exhausted)},
    ]
    summary = summarize(run, tool_calls=calls)
    providers = {row["provider"]: row for row in summary["tools"]["by_provider"]}
    assert summary["tools"]["failovers"] == 3
    assert providers["tavily"] == {**providers["tavily"], "attempts": 1, "errors": 1, "skipped": 2, "answered": 0, "error_kinds": {"quota": 2, "skipped": 1}}
    assert providers["serper"] == {**providers["serper"], "attempts": 3, "answered": 2, "errors": 1, "error_rate": 0.333}


@pytest.mark.asyncio
async def test_per_request_credentials_reach_research_mcp_servers_and_http_providers(settings, monkeypatch):
    """Enterprise deployments pass each user's credential with the request.

    A ``secret:NAME`` in a research MCP header or an HTTP provider header must
    resolve from that request's credentials when it carries one, and two users
    must never share a discovered-tool cache entry.
    """
    from deepresearch import mcp
    from deepresearch.secrets import SECRETS

    seen = []

    class FakeClient:
        def __init__(self, connections):
            seen.append(connections["kb"])

        async def get_tools(self, server_name=None):
            class Tool:
                name = "search_docs"
                args = {"q": {"type": "string"}}

            return [Tool()]

    module = types.ModuleType("langchain_mcp_adapters.client")
    module.MultiServerMCPClient = FakeClient
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", types.ModuleType("langchain_mcp_adapters"))
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", module)
    SECRETS.set("kb-token", "saved-token")
    try:
        spec = McpServerSpec(transport="http", url="http://127.0.0.1:9000/mcp", headers={"Authorization": "secret:kb-token"})
        manager = mcp.McpManager()
        await manager.tools("kb", spec)
        await manager.tools("kb", spec, request={"kb-token": "user-a-token"})
        await manager.tools("kb", spec, request={"kb-token": "user-b-token"})
        # The saved value serves the configured case; each request credential
        # gets its own connection and its own cache entry.
        assert [item["headers"]["Authorization"] for item in seen] == ["saved-token", "user-a-token", "user-b-token"]
        await manager.tools("kb", spec, request={"kb-token": "user-a-token"})
        assert len(seen) == 3  # cached per credential, not shared
    finally:
        SECRETS.set("kb-token", None)


@pytest.mark.asyncio
async def test_http_provider_headers_use_the_request_credential(settings, monkeypatch):
    from deepresearch.secrets import SECRETS

    captured = {}

    async def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"results": [{"title": "内部文档", "url": "https://intra.example/doc", "snippet": "正文"}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    SECRETS.set("kb-token", "saved-token")
    try:
        provider = ProviderSpec(id="api", type="http", url="https://intra.example/search?q={query}", headers={"Authorization": "secret:kb-token"})
        outcome = await providers.call(provider, Request("search", query="报销", max_results=3), request_secrets={"kb-token": "user-a-token"})
        assert captured["auth"] == "user-a-token"
        assert [item["title"] for item in outcome.records] == ["内部文档"]
    finally:
        SECRETS.set("kb-token", None)


@pytest.mark.asyncio
async def test_a_step_that_runs_out_of_searches_is_told_to_finish_instead_of_failing(settings, tmp_path):
    """Hitting the search ceiling must wind the step down, not fail the run.

    The researcher is told how many searches a step may make and, once they are
    used, the tool answers with a stop instruction so the model writes its notes
    from what it already has.
    """
    from deepresearch.channels import SearchBudget
    from deepresearch.store import Store

    store = Store(tmp_path / "budget.sqlite")
    await store.start()
    run = {"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": 100}}
    await store.create(run, "key", "hash")
    budget = SearchBudget(store, "r", "u1", limit=2)

    assert await budget.reserve() is None and await budget.reserve() is None
    refusal = await budget.reserve()
    assert refusal and "all 2 of its searches" in refusal.text and "no more searches" in refusal.text.lower()
    assert (await store.get("r"))["usage"]["tool_calls"] == 2  # a refused call costs nothing
    events = [event for event in await store.events("r") if event["type"] == "research.search.limited"]
    assert len(events) == 1 and events[0]["data"]["unit_id"] == "u1"
    assert await budget.reserve()  # still refusing, still one event
    assert len([event for event in await store.events("r") if event["type"] == "research.search.limited"]) == 1


@pytest.mark.asyncio
async def test_the_run_wide_tool_ceiling_also_winds_a_step_down(settings, tmp_path):
    from deepresearch.channels import SearchBudget
    from deepresearch.store import Store

    store = Store(tmp_path / "ceiling.sqlite")
    await store.start()
    run = {"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": 1}}
    await store.create(run, "key", "hash")
    budget = SearchBudget(store, "r", "u1", limit=None)

    assert await budget.reserve() is None
    refusal = await budget.reserve()
    assert refusal and refusal.reason == "run" and "budget" in refusal.text.lower()
    assert (await store.get("r"))["usage"]["tool_calls"] == 1


@pytest.mark.asyncio
async def test_an_exhausted_step_never_reaches_a_provider(settings, tmp_path, monkeypatch):
    from deepresearch import channels
    from deepresearch.store import Store

    store = Store(tmp_path / "tool-budget.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": 50}}, "key", "hash")

    async def unexpected(*args, **kwargs):
        raise AssertionError("A step without searches left must not call a provider")

    monkeypatch.setattr(channels, "call", unexpected)
    budget = channels.SearchBudget(store, "r", "u1", limit=0)
    source = SourceSpec(name="web", tool="web_search", role="search", origin="external", providers=[{"id": "ddg", "type": "duckduckgo"}])
    tool = channels.build_tool(source, settings, "r", budget=budget)
    message = await tool.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "q"}, "id": "c1"})
    assert "write your research notes" in message.content.lower()
    assert message.artifact == {"schema": channels.BUDGET_STOP, "reason": "step"}


@pytest.mark.asyncio
async def test_a_step_out_of_searches_can_still_open_the_pages_it_found(settings, tmp_path, monkeypatch):
    """The search allowance counts searches, not page reads.

    Only an opened page is citable. Charging reads to the search allowance left
    a live run (40e5b85a) with three searches per step, no page ever read and no
    citable evidence. Reads still count toward the run-wide tool ceiling.
    """
    from deepresearch import channels
    from deepresearch.store import Store

    store = Store(tmp_path / "reads.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": 3}}, "key", "hash")

    async def fake(provider, request, servers=None, request_secrets=None):
        if request.role == "read":
            return providers.Outcome(document={"title": "Guide", "url": request.url, "text": "uv.lock pins every platform."})
        return providers.Outcome(records=[{"title": "Guide", "url": "https://docs.example/guide", "snippet": "lock files"}])

    monkeypatch.setattr(channels, "call", fake)
    budget = channels.SearchBudget(store, "r", "u1", limit=1)
    search = channels.build_tool(SourceSpec(name="web", tool="web_search", role="search", origin="external", providers=[{"id": "ddg", "type": "duckduckgo"}]), settings, "r", budget=budget)
    read = channels.build_tool(SourceSpec(name="reader", tool="web_fetch", role="read", origin="external", providers=[{"id": "reader", "type": "direct"}]), settings, "r", budget=budget)

    await search.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "uv lock"}, "id": "s1"})
    refused = await search.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "again"}, "id": "s2"})
    assert "no more searches" in refused.content.lower() and "open" in refused.content.lower()
    page = await read.ainvoke({"type": "tool_call", "name": "web_fetch", "args": {"url": "https://docs.example/guide"}, "id": "p1"})
    assert page.artifact["schema"] == "deerflow.web_page.v1" and "every platform" in page.content
    assert (await store.get("r"))["usage"]["tool_calls"] == 2
    # Past the run-wide ceiling a read is refused too, and the model is told to stop.
    await read.ainvoke({"type": "tool_call", "name": "web_fetch", "args": {"url": "https://docs.example/other"}, "id": "p2"})
    stopped = await read.ainvoke({"type": "tool_call", "name": "web_fetch", "args": {"url": "https://docs.example/third"}, "id": "p3"})
    assert "research-wide tool budget" in stopped.content and stopped.artifact == {"schema": channels.BUDGET_STOP, "reason": "run"}
    assert (await store.get("r"))["usage"]["tool_calls"] == 3


def test_a_budget_stop_reply_is_never_evidence():
    """The stop instruction is a message to the model, not something it read.

    In live run 40e5b85a every refused read became a citable-looking
    ``tool_output`` record whose text was the instruction itself.
    """
    from deepresearch import channels

    source = SourceSpec(name="reader", tool="web_fetch", role="read", origin="external", providers=[{"id": "reader", "type": "direct"}])
    execution = NativeExecution(
        answer="notes",
        execution_id="exec",
        messages=[
            {"type": "ai", "tool_calls": [{"id": "p1", "name": "web_fetch", "args": {"url": "https://docs.example/guide"}}]},
            {"type": "tool", "name": "web_fetch", "tool_call_id": "p1", "content": "No more searches in this step ...", "artifact": {"schema": channels.BUDGET_STOP, "reason": "step"}},
        ],
    )
    evidences, catalog = research_observations(execution, [source])
    assert evidences == [] and catalog == []


@pytest.mark.asyncio
async def test_parallel_searches_in_one_turn_cannot_overrun_the_step_allowance(settings, tmp_path):
    """A model often sends several searches in one turn; they run concurrently.

    Each must claim its slot before waiting on the ledger, otherwise all of
    them see the same remaining allowance and every one is executed.
    """
    import asyncio

    from deepresearch.channels import SearchBudget
    from deepresearch.store import Store

    store = Store(tmp_path / "parallel.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": 100}}, "key", "hash")
    budget = SearchBudget(store, "r", "u1", limit=2)

    outcomes = await asyncio.gather(*(budget.reserve() for _ in range(3)))
    assert sum(outcome is None for outcome in outcomes) == 2
    assert (await store.get("r"))["usage"]["tool_calls"] == 2

    # A slot the run-wide ceiling refuses is given back to the step.
    tight = SearchBudget(store, "r", "u2", limit=5)
    await store.mutate("r", lambda run: run["budget"].update({"max_tool_calls": 2}))
    assert (await tight.reserve()).reason == "run"
    assert tight.used == 0
