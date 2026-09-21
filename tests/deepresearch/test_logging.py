"""A research run keeps what was actually sent and returned, at every layer.

The model layer already did (``research_llm_exchange``). These tests cover the
three layers that did not: a tool call's own arguments and answer, the MCP calls
behind it, and the HTTP calls behind those. A research that only had MCP tools
used to leave `output_chars` and a 16-character hash of its arguments, so the
one question an operator asks — what did we send, what came back — had no
answer. They also pin the two rules the audit must never break: a credential
never reaches the store, and a failed audit never fails the research.
"""

import asyncio
import json
import sys
import types

import httpx
import pytest

from deepresearch import channels, mcp, providers, wire
from deepresearch.config import McpServerSpec, ProviderSpec, SourceSpec
from deepresearch.providers import ProviderError, Request
from deepresearch.store import Store

TOKEN = "tok-never-logged-4f2a9c"
COOKIE = "sid=sess-never-logged-8b71"


@pytest.fixture
def reachable(monkeypatch):
    """`direct` screens every redirect hop against DNS; these hosts do not exist."""
    import deerflow.community.url_safety as safety

    monkeypatch.setattr(safety, "validate_public_http_url", lambda url, **kwargs: None)


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


async def opened(tmp_path, name="wire.sqlite"):
    store = Store(tmp_path / name)
    await store.start()
    budget = {"max_iterations": 1, "max_units": 8, "max_tool_calls": None, "max_elapsed_seconds": None, "max_model_tokens": None}
    await store.create({"run_id": "r", "thread_id": "dr-r", "owner": "u", "query": "q", "created_at": "2026-09-21T10:00:00+00:00", "budget": budget}, "k", "h")
    return store


def recorder_for(store, **extra):
    return wire.WireRecorder(store=store, run_id="r", secrets=(TOKEN,), **extra)


def http_source(*ids, role="search", tool="web_search"):
    return SourceSpec(name="web", tool=tool, role=role, origin="external", providers=[ProviderSpec(id=item, type="http", url=f"https://{item}.example/search?token={TOKEN}") for item in ids])


# ---------------------------------------------------------------------------
# The HTTP layer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_http_provider_call_keeps_its_request_and_its_answer(tmp_path, monkeypatch, settings):
    store = await opened(tmp_path)

    def handler(request):
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"results": [{"title": "Qdrant SLA", "url": "https://wiki.example/sla", "snippet": "99.9%"}]})

    mock_http(monkeypatch, handler)
    spec = ProviderSpec(id="kb", type="http", method="POST", url=f"https://kb.example/search?token={TOKEN}", headers={"Authorization": f"Bearer {TOKEN}"}, body={"q": "{query}"})
    with wire.bind(recorder_for(store), call_id="call-1", unit_id="R1", tool="web_search"):
        await providers.call(spec, Request("search", query="qdrant sla"))
    (row,) = await store.wire_calls("r")
    record = await store.wire_call("r", row["id"])
    assert (record["kind"], record["call_id"], record["unit_id"], record["provider"]) == ("http", "call-1", "R1", "kb")
    assert record["method"] == "POST" and record["http_status"] == 200 and record["status"] == "ok"
    assert record["request"]["body"] == {"q": "qdrant sla"}
    assert record["response"]["body"]["results"][0]["title"] == "Qdrant SLA"
    # The credential is in the URL, the header and the config: none of it is stored.
    assert TOKEN not in json.dumps(record, ensure_ascii=False)
    assert "[redacted]" in record["url"]


@pytest.mark.asyncio
async def test_a_failing_provider_keeps_the_whole_error_body_not_three_hundred_characters(tmp_path, monkeypatch, settings):
    store = await opened(tmp_path)
    body = {"error": {"code": "quota_exhausted", "message": "详细的配额说明。" * 200, "reset_at": "2026-10-01"}}
    mock_http(monkeypatch, lambda request: httpx.Response(429, json=body, headers={"retry-after": "60"}))
    spec = ProviderSpec(id="kb", type="http", url="https://kb.example/search")
    with wire.bind(recorder_for(store), call_id="call-1"):
        with pytest.raises(ProviderError):
            await providers.call(spec, Request("search", query="q"))
    record = await store.wire_call("r", (await store.wire_calls("r"))[0]["id"])
    assert (record["status"], record["http_status"], record["error"]["kind"]) == ("error", 429, "rate_limit")
    assert record["retry_after"] == "60"
    # The whole body, not the 300 characters the attempt message keeps.
    assert record["response"]["body"]["error"]["reset_at"] == "2026-10-01"
    assert len(json.dumps(record["response"]["body"], ensure_ascii=False)) > 1000


@pytest.mark.asyncio
async def test_every_redirect_hop_of_a_page_read_is_its_own_record(tmp_path, monkeypatch, settings, reachable):
    store = await opened(tmp_path)
    hops = {"https://a.example/doc": "https://b.example/doc", "https://b.example/doc": "https://c.example/doc"}

    def handler(request):
        target = hops.get(str(request.url))
        if target:
            return httpx.Response(301, headers={"location": target})
        return httpx.Response(200, text="# Page\n\n" + "正文。" * 40, headers={"content-type": "text/html"})

    mock_http(monkeypatch, handler)
    with wire.bind(recorder_for(store), call_id="call-1"):
        await providers.call(ProviderSpec(id="direct", type="direct"), Request("read", url="https://a.example/doc"))
    urls = [row["url"] for row in await store.wire_calls("r")]
    assert urls == ["https://a.example/doc", "https://b.example/doc", "https://c.example/doc"]


# ---------------------------------------------------------------------------
# The MCP layer
# ---------------------------------------------------------------------------
class FakeMcpTool:
    """What `langchain_mcp_adapters` hands back: a tool whose artifact is often None."""

    args = {"query": {"type": "string"}, "top_k": {"type": "integer"}}

    def __init__(self, name="search_docs", answer="{}", artifact=None, status="success", error=None):
        self.name, self.description, self.args_schema = name, "Search the knowledge base.", None
        self.answer, self.artifact, self.status, self.error = answer, artifact, status, error
        self.seen = []

    async def ainvoke(self, call, config=None):
        self.seen.append((call, config))
        if self.error:
            raise self.error
        return types.SimpleNamespace(content=self.answer, artifact=self.artifact, status=self.status)


@pytest.mark.asyncio
async def test_an_mcp_call_records_the_server_the_real_tool_name_and_what_was_sent(tmp_path, settings):
    store = await opened(tmp_path)
    remote = FakeMcpTool(answer=json.dumps({"chunks": [{"content": "配额 3000 万向量", "doc_id": "D-1"}]}, ensure_ascii=False))
    with wire.bind(recorder_for(store), call_id="call-1", unit_id="R1", tool="kb_docs"):
        content, artifact, status = await mcp.MANAGER.call(remote, {"query": "配额", "top_k": 5}, server="kb", source="kb-docs")
    assert status == "success" and "配额" in content
    record = await store.wire_call("r", (await store.wire_calls("r"))[0]["id"])
    assert (record["kind"], record["server"], record["remote_tool"], record["tool"]) == ("mcp", "kb", "search_docs", "kb_docs")
    # The alias the model sees is `kb_docs`; the tool actually called is `search_docs`.
    assert record["request"]["arguments"] == {"query": "配额", "top_k": 5}
    assert record["response"]["body"] == remote.answer and record["response"]["is_error"] is False
    assert record["call_id"] == "call-1"
    # The inner call still must not reach the research callbacks: that billed 25 searches as 41.
    assert remote.seen[0][1] == {"callbacks": []}


@pytest.mark.asyncio
async def test_an_mcp_server_error_is_distinguishable_from_our_timeout(tmp_path, settings):
    store = await opened(tmp_path)
    with wire.bind(recorder_for(store), call_id="call-1"):
        await mcp.MANAGER.call(FakeMcpTool(answer="permission denied", status="error"), {"query": "q"}, server="kb")
    said_no = await store.wire_call("r", (await store.wire_calls("r"))[0]["id"])
    assert said_no["status"] == "error" and said_no["response"]["is_error"] is True
    assert said_no["response"]["body"] == "permission denied"

    store = await opened(tmp_path, "timeout.sqlite")
    slow = FakeMcpTool()

    async def never(call, config=None):
        await asyncio.sleep(5)

    slow.ainvoke = never
    with wire.bind(recorder_for(store), call_id="call-1"):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(mcp.MANAGER.call(slow, {"query": "q"}, server="kb"), 0.05)
    # A call torn down by the caller's deadline leaves no record; its outcome is on the tool call.
    assert await store.wire_calls("r") == []


@pytest.mark.asyncio
async def test_mcp_discovery_records_the_tools_a_server_offered_and_never_its_credentials(tmp_path, monkeypatch, settings):
    store = await opened(tmp_path)
    spec = McpServerSpec(transport="http", url="https://kb.example/mcp", headers={"Authorization": f"Bearer {TOKEN}", "Cookie": COOKIE})
    tools = [FakeMcpTool("search_docs"), FakeMcpTool("search_wiki")]

    class Client:
        def __init__(self, servers):
            self.servers = servers

        async def get_tools(self, server_name=None):
            return tools

    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", types.SimpleNamespace(MultiServerMCPClient=Client))
    mcp.MANAGER.forget()
    with wire.bind(recorder_for(store)):
        await mcp.MANAGER.tools("kb", spec)
    record = await store.wire_call("r", (await store.wire_calls("r"))[0]["id"])
    assert record["kind"] == "mcp_discovery" and record["server"] == "kb" and record["cache"] == "miss"
    assert [item["name"] for item in record["discovered_tools"]] == ["search_docs", "search_wiki"]
    assert record["url_host"] == "kb.example"
    # Header names are useful; their values never are.
    assert record["request"]["header_names"] == ["Authorization", "Cookie"]
    body = json.dumps(record, ensure_ascii=False)
    assert TOKEN not in body and "sess-never-logged" not in body


@pytest.mark.asyncio
async def test_a_provider_backed_mcp_call_records_the_argument_name_it_guessed(tmp_path, monkeypatch, settings):
    store = await opened(tmp_path)
    remote = FakeMcpTool("find_pages")
    remote.args = {"keyword": {"type": "string"}, "limit": {"type": "integer"}}
    remote.answer = json.dumps({"hits": [{"headline": "SLA", "link": "https://wiki.example/sla", "desc": "99.9%"}]})
    monkeypatch.setattr(mcp.MANAGER, "tool", lambda *a, **k: _ready(remote))
    spec = ProviderSpec(id="kb", type="mcp", server="kb", tool="find_pages")
    with wire.bind(recorder_for(store), call_id="call-1"):
        await providers.call(spec, Request("search", query="sla"), servers={"kb": McpServerSpec(transport="http", url="https://kb.example/mcp")})
    record = await store.wire_call("r", (await store.wire_calls("r"))[0]["id"])
    # `infer_arguments` picked `keyword` by heuristic; a wrong guess used to be invisible.
    assert record["argument_name"] == "keyword"
    assert record["request"]["arguments"] == {"keyword": "sla", "limit": 8}


async def _ready(value):
    return value


# ---------------------------------------------------------------------------
# Correlation, isolation and the switches
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_wire_call_carries_the_id_of_the_tool_call_that_made_it(tmp_path, monkeypatch, settings):
    """LangChain gives the tool coroutine a child callback manager whose parent
    run id is exactly the id `trace.on_tool_start` filed the tool call under."""
    store = await opened(tmp_path)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"results": [{"title": "t", "url": "https://a.example/1", "snippet": "s"}]}))
    tool = channels.build_tool(http_source("kb"), settings, "r", recorder=recorder_for(store))
    seen = {}

    class Handler:
        raise_error = True
        ignore_llm = ignore_chain = ignore_agent = ignore_retriever = ignore_chat_model = ignore_custom_event = False

        async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            seen["call_id"] = str(run_id)

        def __getattr__(self, name):
            async def nothing(*args, **kwargs):
                return None

            return nothing

    await tool.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "q"}, "id": "c1"}, config={"callbacks": [Handler()]})
    (row,) = await store.wire_calls("r")
    assert row["call_id"] == seen["call_id"] and row["call_id"] is not None


@pytest.mark.asyncio
async def test_a_broken_audit_never_breaks_the_research(tmp_path, monkeypatch, settings):
    store = await opened(tmp_path)

    async def explode(*args, **kwargs):
        raise sys.modules["sqlite3"].OperationalError("disk is full")

    monkeypatch.setattr(store, "record_wire_call", explode)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"results": [{"title": "t", "url": "https://a.example/1", "snippet": "s"}]}))
    with wire.bind(recorder_for(store), call_id="call-1"):
        _, outcome, _ = await channels.run_providers(http_source("kb"), Request("search", query="q"), settings)
    assert outcome.records[0]["title"] == "t"


@pytest.mark.asyncio
async def test_nothing_is_recorded_without_a_recorder(tmp_path, monkeypatch, settings):
    store = await opened(tmp_path)
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"results": [{"title": "t", "url": "https://a.example/1", "snippet": "s"}]}))
    tool = channels.build_tool(http_source("kb"), settings, "r")
    await tool.ainvoke({"type": "tool_call", "name": "web_search", "args": {"query": "q"}, "id": "c1"})
    assert await store.wire_calls("r") == []


@pytest.mark.asyncio
async def test_an_oversized_body_says_so_instead_of_being_cut_in_silence(tmp_path, monkeypatch, settings, reachable):
    store = await opened(tmp_path)
    page = "正文。" * 20000
    mock_http(monkeypatch, lambda request: httpx.Response(200, text=page))
    with wire.bind(recorder_for(store, max_chars=2000), call_id="call-1"):
        await providers.call(ProviderSpec(id="direct", type="direct"), Request("read", url="https://a.example/doc"))
    record = await store.wire_call("r", (await store.wire_calls("r"))[0]["id"])
    assert record["response"]["truncated"] is True
    assert record["response"]["chars"] == len(page) and len(record["response"]["body"]) == 2000


# ---------------------------------------------------------------------------
# The tool layer: a call's own arguments and answer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_tool_call_keeps_the_arguments_and_the_answer_it_actually_had(tmp_path, settings):
    store = await opened(tmp_path)
    await store.record_tool_exchange("r", "call-1", {"tool_name": "kb_docs", "role": "data", "started_at": "2026-09-21T10:00:01+00:00"}, payloads={"arguments": {"query": "配额", "top_k": 5, "filters": {"space": "platform"}}})
    artifact = {"schema": "deepresearch.records.v1", "records": [{"title": "配额", "snippet": "3000 万"}]}
    await store.record_tool_exchange("r", "call-1", {"ended_at": "2026-09-21T10:00:03+00:00", "duration_ms": 2000}, payloads={"content": "3 records from kb-docs.", "artifact": artifact})
    record = await store.tool_exchange("r", "call-1")
    # Both halves of the call merged into one record, neither hashed nor summarised.
    assert record["request"]["arguments"]["filters"] == {"space": "platform"}
    assert record["response"]["content"] == "3 records from kb-docs."
    assert record["response"]["artifact"]["records"][0]["snippet"] == "3000 万"
    assert (record["tool_name"], record["duration_ms"]) == ("kb_docs", 2000)


@pytest.mark.asyncio
async def test_a_tool_error_keeps_the_message_the_model_was_given(tmp_path, settings):
    store = await opened(tmp_path)
    message = "Error: every provider failed for this query. tavily: HTTP 432 quota exhausted; serper: HTTP 401 invalid key."
    await store.record_tool_exchange("r", "call-1", {"tool_name": "web_search", "status": "error", "error_type": "ToolReturnedError"}, payloads={"content": message})
    record = await store.tool_exchange("r", "call-1")
    # `error_type` stays the coarse bucket the metrics group by; the text is new.
    assert record["error_type"] == "ToolReturnedError" and record["response"]["content"] == message


# ---------------------------------------------------------------------------
# Reading it back: one file with everything
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_run_exports_as_one_self_contained_file(tmp_path, settings):
    from deepresearch import logbook

    store = await opened(tmp_path)
    await store.event("r", "plan.created", {"plan_version": 1})
    await store.record_call("r", "call-1", {"tool_name": "kb_docs", "role": "data", "status": "success", "output_chars": 23})
    await store.record_tool_exchange("r", "call-1", {"tool_name": "kb_docs"}, payloads={"arguments": {"query": "配额"}, "content": "3 records from kb-docs."})
    await store.record_wire_call("r", "wire-1", {"kind": "mcp", "call_id": "call-1", "server": "kb", "remote_tool": "search_docs"}, request={"arguments": {"query": "配额"}}, response={"body": "chunks"})
    await store.record_model_call("r", "m-1", {"model": "flash", "status": "ok", "input_tokens": 10, "output_tokens": 2})
    await store.save_unit("r", "0:R1", "h", {"unit_id": "R1", "findings": [], "confidence": 0.5})
    records = [json.loads(line) async for line in logbook.export(store, "r")]
    kinds = [item["record"] for item in records]
    assert kinds[0] == "run" and {"event", "tool_call", "wire_call", "model_call", "unit"} <= set(kinds)
    tool = next(item for item in records if item["record"] == "tool_call")
    # The export is self-contained: blobs are already resolved into text.
    assert tool["request"]["arguments"] == {"query": "配额"} and tool["response"]["content"] == "3 records from kb-docs."
    assert tool["output_chars"] == 23  # the metrics row and the bodies, joined
    sent = next(item for item in records if item["record"] == "wire_call")
    assert sent["request"]["arguments"] == {"query": "配额"} and sent["call_id"] == "call-1"


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pruning_removes_an_old_run_entirely_and_leaves_a_recent_one(tmp_path, settings):
    store = await opened(tmp_path)
    await store.create({"run_id": "old", "thread_id": "dr-old", "owner": "u", "query": "q", "created_at": "2026-01-01T00:00:00+00:00", "budget": {}}, "k2", "h2")
    for run_id in ("r", "old"):
        await store.event(run_id, "plan.created", {"plan_version": 1})
        await store.record_call(run_id, "call-1", {"tool_name": "kb_docs", "status": "success"})
        await store.record_tool_exchange(run_id, "call-1", {"tool_name": "kb_docs"}, payloads={"content": "x" * 100})
        await store.record_wire_call(run_id, "wire-1", {"kind": "mcp", "server": "kb"}, response={"body": "y" * 100})
    report = await store.prune("2026-06-01T00:00:00+00:00", dry_run=True)
    assert report["runs"] == ["old"] and await store.get("old") is not None
    report = await store.prune("2026-06-01T00:00:00+00:00")
    assert report["runs"] == ["old"]
    assert await store.get("old") is None and await store.get("r") is not None
    assert await store.wire_calls("old") == [] and await store.tool_exchange("old", "call-1") is None
    assert (await store.tool_exchange("r", "call-1"))["response"]["content"] == "x" * 100


@pytest.mark.asyncio
async def test_the_export_endpoint_streams_the_whole_run_to_its_owner(settings, monkeypatch):
    from fastapi import FastAPI

    from deepresearch.api import build_router
    from deepresearch.service import ResearchService

    service = ResearchService(settings)
    await service.store.start()
    await service.store.create({"run_id": "own-run", "owner": "alice", "status": "COMPLETED", "created_at": "2026-09-21T10:00:00+00:00"}, "k", "h")
    await service.store.record_call("own-run", "call-1", {"tool_name": "kb_docs", "status": "success"})
    await service.store.record_tool_exchange("own-run", "call-1", {"tool_name": "kb_docs"}, payloads={"arguments": {"query": "配额"}, "content": "3 records."})
    await service.store.record_wire_call("own-run", "wire-1", {"kind": "mcp", "call_id": "call-1", "server": "kb"}, request={"arguments": {"query": "配额"}})

    def resolver(req):
        user = req.headers.get("x-test-user")
        return types.SimpleNamespace(user_id=user) if user else None

    monkeypatch.setitem(sys.modules, "deerflow_extension_api", types.SimpleNamespace(resolve_principal=resolver))
    app = FastAPI()
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/api/deepresearch/own-run/log/export", headers={"x-test-user": "bob"})).status_code == 404
        response = await client.get("/api/deepresearch/own-run/log/export", headers={"x-test-user": "alice"})
    assert response.status_code == 200 and "own-run-log.jsonl" in response.headers["content-disposition"]
    records = [json.loads(line) for line in response.text.splitlines()]
    kinds = {item["record"] for item in records}
    assert {"run", "tool_call", "wire_call"} <= kinds
    assert next(item for item in records if item["record"] == "tool_call")["response"]["content"] == "3 records."
