"""Interrupted callbacks remain distinguishable from successful tool calls."""

import pytest
from langchain_core.messages import HumanMessage

from deepresearch.store import Store
from deepresearch.trace import LocalTrace, model_callbacks


@pytest.mark.asyncio
async def test_drained_native_callbacks_are_closed_once_without_refunding_unknown_usage(settings, tmp_path):
    store = Store(tmp_path / "callbacks.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "key", "hash")
    callbacks = model_callbacks(LocalTrace(store, "r", settings))
    await callbacks.on_tool_start({"name": "ls"}, "{}", run_id="tool")
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="research")]], run_id="model")
    reserved = (await store.get("r"))["usage"]
    await callbacks.close()
    assert not callbacks.active
    assert not callbacks.reservations
    assert (await store.get("r"))["usage"] == reserved
    assert (await store.calls("r"))[0]["status"] == "error"
    events = await store.events("r")
    assert {event["data"]["span_id"] for event in events if event["type"] == "trace.ended"} == {"tool", "model"}
    await callbacks.close()
    await callbacks.on_tool_error(RuntimeError("late callback"), run_id="tool")
    assert await store.events("r") == events


@pytest.mark.asyncio
async def test_trace_reconciliation_only_closes_descendants_of_terminal_parents(settings, tmp_path):
    store = Store(tmp_path / "orphan.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "key", "hash")
    trace = LocalTrace(store, "r", settings)
    await trace.write("started", "old-agent", None, "agent", "agent")
    await trace.write("started", "old-tool", "old-agent", "ls", "tool")
    await store.record_call("r", "old-tool", {"tool_name": "ls", "status": "running"})
    await trace.write("ended", "old-agent", None, "agent", "agent", status="error")
    await trace.write("started", "active-agent", None, "agent", "agent")
    await trace.write("started", "active-tool", "active-agent", "web_fetch", "tool")
    assert await store.reconcile_trace("r") == 1
    assert await store.reconcile_trace("r") == 0
    events = await store.events("r")
    ends = {event["data"]["span_id"]: event["data"] for event in events if event["type"] == "trace.ended"}
    assert set(ends) == {"old-agent", "old-tool"}
    assert ends["old-tool"]["status"] == "cancelled"
    assert ends["old-tool"]["reconciled"] is True
    assert ends["old-tool"]["duration_ms"] is None
    assert (await store.calls("r"))[0]["error_type"] == "InterruptedTrace"
    assert any(event["type"] == "activity.tool.completed" for event in events)
