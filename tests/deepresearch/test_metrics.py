"""Research cost and efficiency: every model call, tool call and subagent is measured."""

import types

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from deepresearch.store import Store
from deepresearch.trace import LocalTrace, metric_scope, model_callbacks, request_key, returned_error_type, usage_details


def test_usage_is_normalized_across_provider_shapes():
    langchain = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 900, "output_tokens": 100, "total_tokens": 1000, "input_token_details": {"cache_read": 600}, "output_token_details": {"reasoning": 40}},
    )
    assert usage_details(langchain) == {"input_tokens": 900, "output_tokens": 100, "total_tokens": 1000, "cache_read_tokens": 600, "reasoning_tokens": 40}
    openai = AIMessage(content="ok", response_metadata={"token_usage": {"prompt_tokens": 50, "completion_tokens": 7, "prompt_tokens_details": {"cached_tokens": 20}, "completion_tokens_details": {"reasoning_tokens": 3}}})
    assert usage_details(openai) == {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57, "cache_read_tokens": 20, "reasoning_tokens": 3}
    deepseek = AIMessage(content="ok")
    assert usage_details(deepseek, {"token_usage": {"prompt_tokens": 80, "completion_tokens": 9, "total_tokens": 89, "prompt_cache_hit_tokens": 64}})["cache_read_tokens"] == 64
    assert usage_details(AIMessage(content="ok")) == dict.fromkeys(("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "reasoning_tokens"))
    assert usage_details(AIMessage(content="ok", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}), {"token_usage": "not a dict"})["total_tokens"] == 2


def test_tool_requests_have_stable_keys_and_returned_errors_have_labels():
    assert request_key('{"url": "https://a.com", "max_length": 8000}') == request_key({"max_length": 8000, "url": "https://a.com"})
    assert request_key({"url": "https://a.com", "start_index": 8000}) != request_key({"url": "https://a.com"})
    assert request_key({"query": "pricing private-token"}, ["private-token"]) == request_key({"query": "pricing [redacted]"})
    # Messages observed from the native web tools during acceptance runs.
    assert returned_error_type('Error: Jina API returned status 429: {"data":null,"retryAfter":2}') == "HTTP 429"
    assert returned_error_type("Error: Request to Jina API failed: ConnectError:") == "ConnectError"
    assert returned_error_type("Error: No readable page content was extracted; do not cite this response as original text.") == "EmptyContent"
    assert returned_error_type({"message": "quota exhausted"}) == "ToolReturnedError"


@pytest.mark.asyncio
async def test_model_and_tool_calls_are_recorded_with_phase_tokens_and_sizes(settings, tmp_path):
    store = Store(tmp_path / "metrics.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "cycle": 0, "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "k", "h")
    token = metric_scope.set({"phase": "dispatch", "cycle": 0})
    try:
        scope = {"unit_id": "R1", "agent_name": "researcher", "skill": "technical-route", "purpose": "agent"}
        callbacks = model_callbacks(LocalTrace(store, "r", settings), model_name="deepseek-v4-flash", scope=scope)
    finally:
        metric_scope.reset(token)
    scope["execution_id"] = "exec-1"  # assigned after the callbacks are built
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="system prompt"), HumanMessage(content="task")]], run_id="m1")
    message = AIMessage(
        content="I will check the pricing page.",
        tool_calls=[{"id": "t1", "name": "web_fetch", "args": {"url": "https://example.com"}}],
        usage_metadata={"input_tokens": 1200, "output_tokens": 80, "total_tokens": 1280, "input_token_details": {"cache_read": 1000}},
        response_metadata={"finish_reason": "tool_calls", "model_name": "deepseek-v4-flash"},
    )
    await callbacks.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id="m1")
    await callbacks.on_tool_start({"name": "web_fetch"}, "{}", run_id="t1", inputs={"url": "https://example.com"})
    await callbacks.on_tool_end(types.SimpleNamespace(content="x" * 321, status="success", artifact=None, tool_call_id="t1"), run_id="t1")
    await callbacks.on_tool_start({"name": "web_fetch"}, "{}", run_id="t2", inputs={"url": "https://example.com"})
    await callbacks.on_tool_end(types.SimpleNamespace(content="Error: Jina API returned status 429: slow down", status="error", artifact=None, tool_call_id="t2"), run_id="t2")
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="again")]], run_id="m2")

    class RateLimited(Exception):
        status_code = 429

    await callbacks.on_llm_error(RateLimited("limited"), run_id="m2")
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="cut off")]], run_id="m3")
    await callbacks.close()

    first, second, third = await store.model_calls("r")
    assert third["status"] == "interrupted" and third["estimated_tokens"] > 0
    assert first == {
        **first,
        "phase": "dispatch",
        "cycle": 0,
        "purpose": "agent",
        "skill": "technical-route",
        "unit_id": "R1",
        "execution_id": "exec-1",
        "model": "deepseek-v4-flash",
        "status": "ok",
        "input_tokens": 1200,
        "output_tokens": 80,
        "total_tokens": 1280,
        "cache_read_tokens": 1000,
        "finish_reason": "tool_calls",
        "tool_calls": 1,
        "prompt_messages": 2,
        "usage_reported": True,
    }
    assert first["prompt_chars"] == len("system prompt") + len("task") and first["duration_ms"] >= 0 and first["estimated_tokens"] > 0
    assert second["status"] == "error" and second["error_code"] == "MODEL_RATE_LIMIT" and second["estimated_tokens"] > 0
    tool, retry = await store.calls("r")
    assert tool["output_chars"] == 321 and tool["phase"] == "dispatch" and tool["execution_id"] == "exec-1"
    assert tool["request_key"] == retry["request_key"] == request_key({"url": "https://example.com"}) and "error_type" not in tool
    assert retry["status"] == "error" and retry["error_type"] == "HTTP 429"
    assert callbacks.totals == {
        **callbacks.totals,
        "model_calls": 3,
        "model_errors": 2,
        "tool_calls": 2,
        "tool_errors": 1,
        "input_tokens": 1200,
        "output_tokens": 80,
        "cache_read_tokens": 1000,
        "max_input_tokens": 1200,
        "unreported_model_calls": 0,
    }


def _profile():
    run = {
        "run_id": "r",
        "status": "COMPLETED",
        "query": "比较三种搜索引擎",
        "cycle": 0,
        "iteration": 1,
        "created_at": "2026-09-17T00:00:00+00:00",
        "updated_at": "2026-09-17T00:20:00+00:00",
        "units": [{"id": "R1", "title": "技术路线", "skill": "technical-route"}, {"id": "R2"}, {"id": "S1-a", "parent_gap_id": "g", "depends_on": ["R1"]}],
        "usage": {"model_tokens": 17000, "tool_calls": 4, "elapsed_seconds": 810},
        "budget": {"max_model_tokens": 68000, "max_tool_calls": None, "max_elapsed_seconds": 1620},
        "unit_failures": {"S1-a": "NATIVE_AGENT_TIMEOUT"},
        "evidence_count": 12,
        "report": {
            "document": "# T\n\n## 执行摘要\n\nx\n\n## 对比\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```mermaid\ngraph LR\n```\n",
            "citations": [{"domain": "a.com", "unit_ids": ["R1", "S1-a"]}, {"domain": "a.com", "unit_ids": ["R1"]}, {"domain": "b.com", "unit_ids": ["R1", "R1"]}, {"domain": "c.com"}],
            "audit": {"dropped_statements": 2},
        },
    }

    def model(call_id, phase, purpose, skill, unit, **values):
        return {"id": call_id, "phase": phase, "purpose": purpose, "skill": skill, "unit_id": unit, "cycle": 0, "model": "flash", "status": "ok", "usage_reported": True, **values}

    model_calls = [
        model("m1", "planner", "agent", "deepresearch", "deepresearch", input_tokens=1000, output_tokens=200, total_tokens=1200, cache_read_tokens=0, duration_ms=2000, finish_reason="stop", execution_id="e0"),
        model("m2", "dispatch", "agent", "technical-route", "R1", input_tokens=10000, output_tokens=500, total_tokens=10500, cache_read_tokens=8000, duration_ms=4000, finish_reason="tool_calls", execution_id="e1"),
        model("m3", "dispatch", "conversion", "technical-route", "R1", input_tokens=3000, output_tokens=300, total_tokens=3300, duration_ms=3000),
        model("m4", "dispatch", "agent", "technical-route", "S1-a", status="error", error_code="MODEL_TIMEOUT", usage_reported=False, estimated_tokens=5000, duration_ms=60000, execution_id="e2"),
        {**model("m5", "synthesis", "agent", "report-synthesis", "report-section-1", input_tokens=500, output_tokens=1500, total_tokens=2000, duration_ms=5000), "model": "other"},
    ]
    tool_calls = [
        {"id": "t1", "tool_name": "web_search", "role": "search", "status": "success", "duration_ms": 900, "phase": "dispatch", "cycle": 0, "unit_id": "R1", "output_chars": 4000, "request_key": "q1"},
        {"id": "t2", "tool_name": "web_fetch", "role": "read", "status": "success", "duration_ms": 1500, "phase": "dispatch", "cycle": 0, "unit_id": "R1", "url": "https://a.com/1", "output_chars": 8000, "request_key": "a1"},
        {"id": "t3", "tool_name": "web_fetch", "role": "read", "status": "error", "duration_ms": 300, "phase": "dispatch", "cycle": 0, "unit_id": "R1", "url": "https://www.b.com/1", "output_chars": 50, "request_key": "b1"},
        # Another agent fetches the same page with the same arguments.
        {"id": "t4", "tool_name": "web_fetch", "role": "read", "status": "success", "duration_ms": 1000, "phase": "dispatch", "cycle": 0, "unit_id": "S1-a", "url": "https://a.com/1", "request_key": "a1"},
    ]

    def agent(execution, skill, unit, phase, status, start, end, seconds, **values):
        return {"id": execution, "skill": skill, "unit_id": unit, "phase": phase, "status": status, "started_at": f"2026-09-17T00:{start}:00+00:00", "ended_at": f"2026-09-17T00:{end}:00+00:00", "duration_ms": seconds * 1000, **values}

    agent_runs = [
        agent("e1", "technical-route", "R1", "dispatch", "completed", "01", "05", 240, model_calls=1, tool_calls=3, total_tokens=10500),
        agent("e2", "technical-route", "S1-a", "dispatch", "failed", "02", "12", 600, error_code="NATIVE_AGENT_TIMEOUT", model_calls=1, tool_calls=1, total_tokens=0),
        agent("e3", "report-synthesis", "report-section-1", "synthesis", "completed", "13", "14", 60, model_calls=1, tool_calls=0, total_tokens=2000),
    ]

    def event(kind, data=None, at="2026-09-17T00:10:00+00:00"):
        return {"type": kind, "at": at, "data": data or {}}

    events = [
        event("research.unit.started", {"unit_id": "R1", "queued_ms": 0}),
        event("research.unit.started", {"unit_id": "S1-a", "queued_ms": 1500}),
        event("metrics.cache_hit", {"kind": "native-unit"}),
        event("metrics.cache_hit", {"kind": "report-section"}),
        event("research.output.retry"),
        event("research.evidence.trimmed", {"dropped": 35}),
        event("report.draft.repair"),
        event("report.completed", {"version": 1}, at="2026-09-17T00:15:00+00:00"),
    ]
    spans = [
        {"kind": "workflow", "name": "workflow", "status": "paused", "duration_ms": 30000},
        {"kind": "workflow", "name": "workflow", "status": "ok", "duration_ms": 780000},
        {"kind": "node", "name": "synthesis", "status": "ok", "duration_ms": 120000},
        {"kind": "node", "name": "planner", "status": "ok", "duration_ms": 25000},
        {"kind": "node", "name": "dispatch", "status": "ok", "duration_ms": 600000},
    ]
    units = [{"id": "0:R1", "result": {"raw_evidences": [{}, {}, {}]}}, {"id": "0:S1-a", "result": {"raw_evidences": []}}]
    return run, dict(model_calls=model_calls, tool_calls=tool_calls, agent_runs=agent_runs, events=events, spans=spans, unit_results=units)


def test_summary_quantifies_time_tokens_cost_tools_agents_and_efficiency():
    from deepresearch.config import ModelPricing
    from deepresearch.metrics import percentiles, summarize

    run, records = _profile()
    pricing = {"flash": ModelPricing(input_per_million=1.0, cached_input_per_million=0.1, output_per_million=2.0, currency="USD")}
    summary = summarize(run, pricing=pricing, **records)

    assert summary["metered"] is True
    time = summary["time"]
    assert (time["wall_seconds"], time["active_seconds"], time["waiting_seconds"]) == (900, 810, 90)
    assert [(phase["phase"], phase["seconds"]) for phase in time["phases"]] == [("planner", 25), ("dispatch", 600), ("synthesis", 120)]
    assert (time["model_seconds"], time["tool_seconds"], time["queue_seconds"]) == (74, 3.7, 1.5)

    assert summary["tokens"] == {"input": 14500, "output": 2500, "cache_read": 8000, "reasoning": 0, "total": 17000, "unreported_calls": 1, "estimated_unreported": 5000, "cache_read_ratio": 0.552}
    # m1 1400 + m2 (2000 + 8000*0.1 + 500*2) + m3 3600 micro-dollars; "other" has no price.
    assert summary["cost"] == {"currency": "USD", "total": 0.0088, "by_currency": {"USD": 0.0088}, "priced_calls": 3, "unpriced_models": ["other"]}

    calls = summary["model_calls"]
    assert (calls["count"], calls["errors"], calls["error_codes"], calls["finish_reasons"]) == (5, 1, {"MODEL_TIMEOUT": 1}, {"stop": 1, "tool_calls": 1})
    assert calls["latency_ms"] == {"count": 4, "avg": 3500, "p50": 3000, "p95": 5000, "max": 5000} and calls["max_input_tokens"] == 10000

    tools = summary["tools"]
    assert {key: tools[key] for key in ("count", "errors", "error_rate", "searches", "reads", "read_errors", "pages_read", "output_chars")} == {
        "count": 4,
        "errors": 1,
        "error_rate": 0.25,
        "searches": 1,
        "reads": 3,
        "read_errors": 1,
        "pages_read": 1,
        "output_chars": 12050,
    }
    assert tools["by_tool"][0] == {**tools["by_tool"][0], "tool": "web_fetch", "count": 3, "errors": 1, "repeats": 1, "error_types": {"ToolReturnedError": 1}}
    assert (tools["repeat_calls"], tools["repeat_calls_same_agent"], tools["error_types"]) == (1, 0, {"ToolReturnedError": 1})
    assert tools["failing_domains"] == [{"domain": "b.com", "reads": 1, "errors": 1, "error_types": {"ToolReturnedError": 1}}]

    agents = summary["agents"]
    assert (agents["count"], agents["completed"], agents["failed"], agents["max_parallel"]) == (3, 2, 1, 2)
    assert agents["failure_codes"] == {"NATIVE_AGENT_TIMEOUT": 1}
    first = agents["runs"][0]
    assert (first["id"], first["seconds"], first["cost"]) == ("e1", 240, 0.0038)
    assert agents["by_skill"][0] == {**agents["by_skill"][0], "skill": "technical-route", "count": 2, "failed": 1}

    assert summary["research"] == {
        "planned_units": 2,
        "supplement_units": 1,
        "iterations": 1,
        "failed_units": 1,
        "raw_evidence": 3,
        "evidence_pool": 12,
        "trimmed_evidence": 35,
        "pruned_references": 0,
        "conversion_retries": 1,
        "deferred_supplements": 0,
    }
    report = summary["report"]
    assert (report["versions"], report["sections"], report["tables"], report["diagrams"], report["citations"], report["cited_domains"]) == (1, 2, 1, 1, 4, 3)
    assert (report["draft_repairs"], report["dropped_statements"]) == (1, 2)
    assert summary["cache"] == {"hits": 2, "by_kind": {"native-unit": 1, "report-section": 1}}
    first_unit, _, supplement = summary["units"]
    assert first_unit == {
        "unit_id": "R1",
        "title": "技术路线",
        "skill": "technical-route",
        "supplement": False,
        "failed": False,
        "seconds": 240,
        "model_calls": 2,
        "total_tokens": 13800,
        "cost": 0.0074,
        "tool_calls": 3,
        "searches": 1,
        "pages_read": 1,
        "evidence": 3,
        "citations": 3,
        "tokens_per_citation": 4600,
    }
    assert (supplement["supplement"], supplement["failed"], supplement["citations"], supplement["tokens_per_citation"]) == (True, True, 1, 0)
    assert summary["budget"] == {"max_model_tokens": 68000, "model_tokens_used": 0.25, "max_tool_calls": None, "tool_calls_used": None, "max_elapsed_seconds": 1620, "elapsed_used": 0.5}

    efficiency = summary["efficiency"]
    assert efficiency == {
        **efficiency,
        "tokens_per_citation": 4250,
        "cost_per_citation": 0.0022,
        "active_seconds_per_citation": 202.5,
        "pages_read_per_citation": 0.25,
        "citations_per_page_read": 4.0,
        "searches_per_unit": 0.3,
        "repeat_tool_call_ratio": 0.25,
        "conversion_token_share": 0.194,
        "failed_agent_token_share": 0.0,
    }
    by_phase = {row["key"]: row for row in summary["breakdown"]["by_phase"]}
    assert list(by_phase) == ["planner", "dispatch", "synthesis"]
    assert (by_phase["dispatch"]["model_calls"], by_phase["dispatch"]["tool_calls"], by_phase["dispatch"]["total_tokens"], by_phase["dispatch"]["cost"]) == (3, 4, 13800, 0.0074)
    assert [row["key"] for row in summary["breakdown"]["by_cycle"]] == ["0"]
    assert {row["key"]: row["cost"] for row in summary["breakdown"]["by_model"]} == {"flash": 0.0088, "other": None}
    assert percentiles([]) == {"count": 0, "avg": None, "p50": None, "p95": None, "max": None}

    running = summarize({**run, "status": "RESEARCHING", "report": None}, **records)
    assert running["time"]["in_progress"] and running["cost"]["total"] is None and running["efficiency"]["tokens_per_citation"] is None


def test_repeats_count_only_identical_requests_after_a_success():
    from deepresearch.metrics import summarize

    def fetch(call_id, status, key, execution, **values):
        return {"id": call_id, "tool_name": "web_fetch", "role": "read", "status": status, "request_key": key, "execution_id": execution, "started_at": f"2026-09-17T00:00:0{call_id}+00:00", "url": "https://www.x.com/a", **values}

    tools = summarize(
        {"run_id": "x", "status": "COMPLETED"},
        tool_calls=[
            fetch("5", "success", "page-2", "e1"),  # paging uses new arguments
            fetch("1", "error", "page-1", "e1", error_type="HTTP 429"),
            fetch("2", "success", "page-1", "e1"),  # a retry after a failure is not a repeat
            fetch("3", "success", "page-1", "e1"),  # same agent: already in its context
            fetch("4", "success", "page-1", "e2"),  # another agent: a shared cache could serve it
            {"id": "6", "tool_name": "web_search", "role": "search", "status": "success", "started_at": "2026-09-17T00:00:06+00:00"},
        ],
    )["tools"]
    assert (tools["repeat_calls"], tools["repeat_calls_same_agent"]) == (2, 1)
    assert tools["error_types"] == {"HTTP 429": 1} and tools["failing_domains"] == [{"domain": "x.com", "reads": 5, "errors": 1, "error_types": {"HTTP 429": 1}}]
    # A run from before per-call metering keeps its ledger total; the rest is unknown.
    old = summarize(
        {"run_id": "old", "status": "COMPLETED", "units": [{"id": "R1"}], "usage": {"model_tokens": 900000, "reported_model_tokens": 880000}, "report": {"document": "# T", "citations": [{"unit_ids": ["R1"]}, {}]}},
        events=[{"type": "report.completed", "at": "2026-09-16T00:00:00+00:00", "data": {}}],
    )
    assert old["metered"] is False and (old["tokens"]["total"], old["tokens"]["input"], old["tokens"]["cache_read_ratio"]) == (880000, None, None)
    assert (old["model_calls"]["count"], old["agents"]["count"], old["efficiency"]["tokens_per_citation"], old["efficiency"]["conversion_token_share"]) == (None, None, 440000, None)
    assert (old["time"]["model_seconds"], old["time"]["queue_seconds"]) == (None, None)
    assert old["units"][0] == {**old["units"][0], "citations": 1, "total_tokens": None, "tokens_per_citation": None, "seconds": None}
    # Calls recorded before request keys existed: unknown, not zero.
    legacy = summarize({"run_id": "x", "status": "COMPLETED"}, tool_calls=[{"id": "1", "tool_name": "web_fetch", "role": "read", "status": "success"}])
    assert (legacy["tools"]["repeat_calls"], legacy["tools"]["by_tool"][0]["repeats"], legacy["efficiency"]["repeat_tool_call_ratio"]) == (None, None, None)


def test_live_runs_count_the_open_workflow_span_and_pending_calls_are_not_unreported():
    from datetime import UTC, datetime

    from deepresearch.metrics import summarize

    run, records = _profile()
    records["spans"] = [
        {"kind": "workflow", "name": "workflow", "status": "ok", "duration_ms": 30000, "at": "2026-09-17T00:00:30+00:00"},
        # An interrupted attempt's span never ended; only the latest open span is live.
        {"kind": "workflow", "name": "workflow", "status": "running", "duration_ms": None, "at": "2026-09-17T00:01:00+00:00"},
        {"kind": "workflow", "name": "workflow", "status": "running", "duration_ms": None, "at": "2026-09-17T00:05:00+00:00"},
        {"kind": "node", "name": "dispatch", "status": "running", "duration_ms": None, "at": "2026-09-17T00:05:01+00:00"},
    ]
    records["model_calls"] = [*records["model_calls"], {"id": "m6", "phase": "dispatch", "status": "running", "model": "flash"}]
    now = datetime(2026, 9, 17, 0, 6, 0, tzinfo=UTC)
    live = summarize({**run, "status": "RESEARCHING", "report": None}, now=now, **records)
    assert live["time"]["active_seconds"] == 90 and live["time"]["phases"] == [{"phase": "dispatch", "seconds": 59, "runs": 1, "errors": 0}]
    assert live["tokens"]["unreported_calls"] == 1 and live["model_calls"]["running"] == 1
    # The time budget counts the running segment before the drive settles its usage.
    assert summarize({**run, "status": "RESEARCHING", "report": None, "usage": {"elapsed_seconds": 0}}, now=now, **records)["budget"]["elapsed_used"] == 0.056
    records["events"] = [event for event in records["events"] if event["type"] != "report.completed"]
    unpublished = summarize({**run, "status": "RESEARCHING", "report": None}, now=now, **records)
    assert unpublished["efficiency"]["citations_per_page_read"] is None and unpublished["efficiency"]["tokens_per_citation"] is None
    finished = summarize(run, now=now, **records)
    assert finished["time"]["active_seconds"] == 30


@pytest.mark.asyncio
async def test_metrics_api_and_export_are_owner_scoped(settings, monkeypatch):
    import json
    import sys

    import httpx
    from fastapi import FastAPI

    from deepresearch.api import build_router
    from deepresearch.service import ResearchService

    service = ResearchService(settings)
    await service.store.start()
    run, records = _profile()
    await service.store.create({**run, "owner": "alice"}, "k", "h")
    for call in records["model_calls"]:
        await service.store.record_model_call("r", call["id"], call)
    for call in records["tool_calls"]:
        await service.store.record_call("r", call["id"], call)
    for agent in records["agent_runs"]:
        await service.store.record_agent_run("r", agent["id"], agent)
    await service.store.event("r", "report.completed", {"version": 1})

    def resolver(request):
        user = request.headers.get("x-test-user")
        return types.SimpleNamespace(user_id=user) if user else None

    monkeypatch.setitem(sys.modules, "deerflow_extension_api", types.SimpleNamespace(resolve_principal=resolver))
    app = FastAPI()
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/api/deepresearch/r/metrics", headers={"x-test-user": "bob"})).status_code == 404
        assert (await client.get("/api/deepresearch/r/metrics/export", headers={"x-test-user": "bob"})).status_code == 404
        summary = (await client.get("/api/deepresearch/r/metrics", headers={"x-test-user": "alice"})).json()
        assert summary["model_calls"]["count"] == 5 and summary["tools"]["count"] == 4 and summary["agents"]["count"] == 3
        exported = await client.get("/api/deepresearch/r/metrics/export", headers={"x-test-user": "alice"})
        assert exported.headers["content-disposition"].endswith('-metrics.jsonl"')
        lines = [json.loads(line) for line in exported.text.splitlines()]
        assert lines[0]["record"] == "summary"
        assert [line["record"] for line in lines].count("model_call") == 5 and [line["record"] for line in lines].count("agent_run") == 3


def test_metrics_cli_compares_runs_from_a_data_directory(tmp_path, capsys):
    import asyncio
    import csv
    import io
    import json

    from deepresearch.metrics import main

    async def seed():
        store = Store(tmp_path / "research.sqlite3")
        await store.start()
        run, records = _profile()
        await store.create({**run, "owner": "alice"}, "k", "h")
        for call in records["model_calls"]:
            await store.record_model_call("r", call["id"], call)

    asyncio.run(seed())
    main(["--data-dir", str(tmp_path), "--format", "csv"])
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert rows[0]["run"] == "r" and rows[0]["model_calls"] == "5" and rows[0]["citations"] == "4" and rows[0]["cost"] == ""
    main(["--data-dir", str(tmp_path), "--run", "r", "--format", "json"])
    assert json.loads(capsys.readouterr().out)[0]["tokens"]["total"] == 17000
    main(["--data-dir", str(tmp_path), "--format", "table"])
    assert "tokens/cite" in capsys.readouterr().out
