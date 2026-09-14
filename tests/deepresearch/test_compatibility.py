"""Regression contracts for heterogeneous search results and plain chat output."""

import json

import pytest

from deepresearch.contracts import ResearchPlan
from deepresearch.output import parse_contract
from deepresearch.store import Store
from deepresearch.trace import LocalTrace, redact


@pytest.mark.parametrize("wrap", [lambda s: s, lambda s: "Here is the plan:\n```json\n" + s + "\n```", lambda s: "<think>private reasoning</think>\n" + s])
def test_plain_chat_contract_parser(plan, wrap):
    value = parse_contract(wrap(plan.model_dump_json()), ResearchPlan)
    assert value == plan


def test_parser_does_not_invent_required_fields():
    with pytest.raises(ValueError):
        parse_contract('The answer is {"goal":"valid goal"}', ResearchPlan)


def test_trace_redaction_keeps_diagnostics():
    value = redact({"authorization": "Bearer abc", "secrets": {"cookie": "private"}, "response": "returned private-value at https://example.org/?token=abc&x=1", "reasoning_content": "hidden", "count": 4}, secrets=["private-value"])
    text = json.dumps(value)
    assert "private-value" not in text and "Bearer abc" not in text and "hidden" not in text
    assert "token=abc" not in text
    assert value["count"] == 4


@pytest.mark.asyncio
async def test_local_trace_hierarchy_failure_and_durable_paging(settings, tmp_path):
    store = Store(tmp_path / "trace.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "k", "h")
    trace = LocalTrace(store, "r", settings, ["private-value"])
    async with trace.span("workflow", "workflow"):
        with pytest.raises(ValueError):
            async with trace.span("search", "tool", {"query": "research"}):
                raise ValueError("provider rejected private-value")
    await store.event("r", "ordinary.event")
    first = await store.trace_events("r", limit=2)
    second = await store.trace_events("r", after=first[-1]["seq"], limit=2)
    assert len(first + second) == 4
    assert first[1]["data"]["parent_span_id"] == first[0]["data"]["span_id"]
    assert second[0]["data"]["status"] == "error"
    assert "private-value" not in json.dumps(first + second)
    reopened = Store(tmp_path / "trace.sqlite")
    assert await reopened.trace_events("r") == first + second


@pytest.mark.asyncio
async def test_plain_chat_conversion_retries_without_json_mode(settings, plan, tmp_path, monkeypatch):
    import sys
    import types

    import langgraph.runtime

    from deepresearch.structured import run_structured

    calls = []
    replies = iter(["not a data object", "```json\n" + plan.model_dump_json() + "\n```"])

    class Model:
        async def ainvoke(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return types.SimpleNamespace(content=next(replies))

    def model_factory(**kwargs):
        assert "response_format" not in kwargs
        return Model()

    async def role(*args, **kwargs):
        from deepresearch.observations import NativeExecution

        return NativeExecution("A normal research planning answer.", "test-execution")

    async def agent_config(name):
        return settings.skills[name], types.SimpleNamespace(model="local-chat")

    monkeypatch.setitem(sys.modules, "deerflow.models", types.SimpleNamespace(create_chat_model=model_factory))
    monkeypatch.setattr(langgraph.runtime, "get_runtime", lambda: types.SimpleNamespace(context={}))
    monkeypatch.setattr("deepresearch.structured.execute_role", role)
    monkeypatch.setattr("deepresearch.structured.model_callbacks", lambda *a, **kw: object())
    store = Store(tmp_path / "conversion.sqlite")
    await store.start()
    run = {"run_id": "r", "owner": "u"}
    await store.create(run, "k", "h")
    runner = types.SimpleNamespace(settings=settings, store=store, _agent_config=agent_config)
    actual = await run_structured(runner, run, "deepresearch", {"goal": plan.goal}, ResearchPlan)
    assert actual == plan
    assert len(calls) == 2
    assert calls[0][0][0]["role"] == "system"
    assert all("response_format" not in kwargs for _, kwargs in calls)
