"""Every model call keeps its complete request and response for the owner's audit."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from deepresearch.audit import audit_request, openai_request, request_summary, scrub
from deepresearch.store import Store
from deepresearch.trace import LocalTrace, metric_scope, model_callbacks

SCHEMA = [{"type": "function", "function": {"name": "web_search", "description": "Search the web", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]


async def _store(tmp_path):
    store = Store(tmp_path / "audit.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "cycle": 0, "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "k", "h")
    return store


def test_requests_keep_every_role_tool_schema_and_parameter_without_credentials():
    long_prompt = "方法论。" * 20000  # far beyond trace payload limits
    messages = [
        SystemMessage(content=long_prompt),
        HumanMessage(content='{"unit": {"id": "R1"}, "token": "private-token-value"}'),
        AIMessage(content="先搜索官方文档。", tool_calls=[{"id": "c1", "name": "web_search", "args": {"query": "sqlite wal"}}], additional_kwargs={"reasoning_content": "hidden plan"}),
        ToolMessage(content="Error: HTTP 429 https://api.example.com/search?api_key=abc123", tool_call_id="c1", name="web_search", status="error"),
    ]
    params = {"model": "deepseek-v4-flash", "temperature": 0.2, "max_tokens": 8192, "tools": SCHEMA, "tool_choice": "auto", "api_key": "sk-never", "http_client": object()}
    normalized, tools, kept = audit_request(messages, params, ["private-token-value"])
    assert [message["role"] for message in normalized] == ["system", "user", "assistant", "tool"]
    assert normalized[0]["content"] == long_prompt
    assert "private-token-value" not in str(normalized) and "abc123" not in str(normalized)
    assert normalized[2]["tool_calls"] == [{"id": "c1", "name": "web_search", "args": {"query": "sqlite wal"}}]
    assert normalized[2]["reasoning"] == "hidden plan"
    assert normalized[3] == {**normalized[3], "tool_call_id": "c1", "name": "web_search", "status": "error"}
    assert tools == SCHEMA
    assert kept == {"model": "deepseek-v4-flash", "temperature": 0.2, "max_tokens": 8192, "tool_choice": "auto"}
    summary = request_summary(normalized, tools)
    assert summary["tools"] == ["web_search"] and summary["message_count"] == 4 and summary["roles"]["tool"] == 1
    assert summary["system_chars"] == len(long_prompt) and summary["last_input"]["role"] == "tool"
    body = openai_request({"messages": normalized, "tools": tools, "params": kept})
    assert body["model"] == "deepseek-v4-flash" and body["tools"] == SCHEMA
    assert body["messages"][2]["tool_calls"][0]["function"] == {"name": "web_search", "arguments": '{"query": "sqlite wal"}'}
    assert body["messages"][3]["tool_call_id"] == "c1"
    assert scrub({"headers": {"Authorization": "Bearer abc"}, "note": "Bearer xyz"}) == {"headers": {"Authorization": "[redacted]"}, "note": "Bearer [redacted]"}


@pytest.mark.asyncio
async def test_agent_turns_are_stored_once_and_show_what_each_turn_added(settings, tmp_path):
    store = await _store(tmp_path)
    token = metric_scope.set({"phase": "dispatch", "cycle": 0})
    try:
        scope = {"unit_id": "R1", "agent_name": "researcher", "skill": "technical-route", "purpose": "agent"}
        callbacks = model_callbacks(LocalTrace(store, "r", settings, ["request-secret"]), model_name="deepseek-v4-flash", scope=scope)
    finally:
        metric_scope.reset(token)
    scope["execution_id"] = "exec-1"
    system = SystemMessage(content="你是研究员。" * 5000)
    task = HumanMessage(content="研究 SQLite 并发写入 request-secret")
    first_answer = AIMessage(
        content="检查官方文档。",
        tool_calls=[{"id": "c1", "name": "web_search", "args": {"query": "sqlite"}}],
        response_metadata={"finish_reason": "tool_calls", "model_name": "deepseek-v4-flash"},
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
    )
    await callbacks.on_chat_model_start({}, [[system, task]], run_id="m1", invocation_params={"model": "deepseek-v4-flash", "tools": SCHEMA})
    await callbacks.on_llm_end(LLMResult(generations=[[ChatGeneration(message=first_answer)]]), run_id="m1")
    history = [system, task, first_answer, ToolMessage(content="SQLite 使用 WAL……", tool_call_id="c1", name="web_search")]
    final = AIMessage(content="研究笔记：WAL 允许并发读。", response_metadata={"finish_reason": "stop"}, usage_metadata={"input_tokens": 150, "output_tokens": 20, "total_tokens": 170})
    await callbacks.on_chat_model_start({}, [history], run_id="m2", invocation_params={"model": "deepseek-v4-flash", "tools": SCHEMA})
    await callbacks.on_llm_end(LLMResult(generations=[[ChatGeneration(message=final)]]), run_id="m2")

    listing = await store.llm_exchanges("r")
    assert [item["id"] for item in listing] == ["m1", "m2"]
    expected = {"status": "ok", "model": "deepseek-v4-flash", "execution_id": "exec-1", "unit_id": "R1", "phase": "dispatch", "message_count": 2, "tools": ["web_search"], "tool_calls": ["web_search"], "finish_reason": "tool_calls"}
    assert listing[0] == {**listing[0], **expected}
    assert listing[1]["preview"] == "研究笔记：WAL 允许并发读。" and "response" not in listing[1]

    detail = await store.llm_exchange("r", "m2")
    assert [message["role"] for message in detail["messages"]] == ["system", "user", "assistant", "tool"]
    assert detail["messages"][0]["content"] == system.content
    assert "request-secret" not in str(detail)
    assert detail["tools"] == SCHEMA
    assert detail["previous_call_id"] == "m1" and detail["repeated_prefix"] == 2
    assert detail["response"]["generations"][0]["content"] == "研究笔记：WAL 允许并发读。"
    assert detail["usage"]["total_tokens"] == 170
    # The system prompt and task are stored once for both turns.
    blobs = await store.call(lambda db: db.execute("SELECT COUNT(*) FROM research_llm_blob WHERE run_id='r'").fetchone()[0])
    assert blobs == 5  # four distinct messages plus one tool schema list


@pytest.mark.asyncio
async def test_failures_and_disabled_capture(settings, tmp_path):
    store = await _store(tmp_path)
    callbacks = model_callbacks(LocalTrace(store, "r", settings), model_name="m", scope={"purpose": "conversion", "contract": "ResearchAnalysis"})

    class Limited(Exception):
        status_code = 429

    await callbacks.on_chat_model_start({}, [[HumanMessage(content="convert")]], run_id="m1")
    await callbacks.on_llm_error(Limited("Authorization: Bearer secret-in-provider-error"), run_id="m1")
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="retry")]], run_id="m2")
    await callbacks.close()
    failed, interrupted = await store.llm_exchanges("r")
    assert failed["status"] == "error" and failed["error"] == {"code": "MODEL_RATE_LIMIT", "type": "Limited", "status_code": 429}
    assert "secret-in-provider-error" not in str(await store.llm_exchange("r", "m1"))
    assert interrupted["status"] == "interrupted" and interrupted["group"] == failed["group"]

    settings.trace_capture_content = False
    quiet = model_callbacks(LocalTrace(store, "r", settings), model_name="m")
    await quiet.on_chat_model_start({}, [[HumanMessage(content="private")]], run_id="m3")
    await quiet.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]]), run_id="m3")
    assert [item["id"] for item in await store.llm_exchanges("r")] == ["m1", "m2"]
    assert (await store.model_call("r", "m3"))["status"] == "ok"


@pytest.mark.asyncio
async def test_llm_call_api_is_owner_scoped_and_lists_legacy_calls(settings, monkeypatch):
    import json
    import sys
    import types

    import httpx
    from fastapi import FastAPI

    from deepresearch.api import build_router
    from deepresearch.service import ResearchService

    service = ResearchService(settings)
    await service.store.start()
    await service.store.create({"run_id": "r", "owner": "alice", "status": "COMPLETED", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "k", "h")
    callbacks = model_callbacks(LocalTrace(service.store, "r", settings), model_name="flash", scope={"purpose": "agent", "skill": "deepresearch"})
    await callbacks.on_chat_model_start({}, [[SystemMessage(content="plan rules"), HumanMessage(content="task")]], run_id="m1", invocation_params={"model": "flash", "max_tokens": 4096})
    await callbacks.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content='{"goal": "x"}', response_metadata={"finish_reason": "stop"}))]]), run_id="m1")
    await service.store.record_model_call("r", "legacy", {"model": "flash", "status": "ok", "started_at": "2000-01-01T00:00:00+00:00", "total_tokens": 9})
    monkeypatch.setitem(sys.modules, "deerflow_extension_api", types.SimpleNamespace(resolve_principal=lambda req: types.SimpleNamespace(user_id=req.headers.get("x-test-user")) if req.headers.get("x-test-user") else None))
    app = FastAPI()
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        for path in ("/api/deepresearch/r/llm-calls", "/api/deepresearch/r/llm-calls/m1", "/api/deepresearch/r/llm-calls/export"):
            assert (await client.get(path, headers={"x-test-user": "bob"})).status_code == 404
        listing = (await client.get("/api/deepresearch/r/llm-calls", headers={"x-test-user": "alice"})).json()["items"]
        assert [(item["id"], item["audited"]) for item in listing] == [("legacy", False), ("m1", True)]
        assert listing[1]["status"] == "ok" and listing[1]["message_count"] == 2 and "messages" not in listing[1]
        detail = (await client.get("/api/deepresearch/r/llm-calls/m1", headers={"x-test-user": "alice"})).json()
        assert detail["messages"][0] == {"role": "system", "content": "plan rules"}
        assert detail["openai_request"]["max_tokens"] == 4096 and detail["openai_request"]["messages"][1]["content"] == "task"
        assert detail["prompt_messages"] == 2  # metrics fields are merged in
        assert (await client.get("/api/deepresearch/r/llm-calls/missing", headers={"x-test-user": "alice"})).status_code == 404
        export = await client.get("/api/deepresearch/r/llm-calls/export", headers={"x-test-user": "alice"})
        (record,) = [json.loads(line) for line in export.text.splitlines()]
        assert record["id"] == "m1" and record["response"]["generations"][0]["content"] == '{"goal": "x"}'
