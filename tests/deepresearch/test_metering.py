"""Reserve model capacity, settle reported usage, and keep unknown calls charged."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from deepresearch.contracts import ResearchError
from deepresearch.native import model_budget_config
from deepresearch.store import Store
from deepresearch.trace import LocalTrace, model_callbacks


def response(total=None):
    usage = None if total is None else {"input_tokens": total - 1, "output_tokens": 1, "total_tokens": total}
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content="OK", usage_metadata=usage))]])


@pytest.mark.asyncio
async def test_reported_usage_replaces_reservation_without_refunding_other_calls(settings, tmp_path):
    store = Store(tmp_path / "meter.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": 100000, "max_tool_calls": 10}}, "key", "hash")
    callbacks = model_callbacks(LocalTrace(store, "r", settings))
    messages = [[HumanMessage(content="Research notes " * 300)]]
    await callbacks.on_chat_model_start({}, messages, run_id="one")
    first = (await store.get("r"))["usage"]["model_tokens"]
    await callbacks.on_chat_model_start({}, messages, run_id="two")
    await callbacks.on_llm_end(response(120), run_id="one")
    state = (await store.get("r"))["usage"]
    assert state["model_tokens"] == first + 120
    assert state["reported_model_tokens"] == 120
    await callbacks.on_llm_end(response(120), run_id="one")
    assert (await store.get("r"))["usage"] == state
    await callbacks.on_llm_end(response(), run_id="two")
    assert (await store.get("r"))["usage"]["model_tokens"] == first + 120


@pytest.mark.asyncio
async def test_actual_usage_overrun_is_recorded_and_stops_further_work(settings, tmp_path):
    settings.max_output_tokens = 128
    store = Store(tmp_path / "overrun.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": 500, "max_tool_calls": 10}}, "key", "hash")
    callbacks = model_callbacks(LocalTrace(store, "r", settings))
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="hello")]], run_id="one")
    with pytest.raises(ResearchError, match="max_model_tokens"):
        await callbacks.on_llm_end(response(700), run_id="one")
    assert (await store.get("r"))["usage"]["model_tokens"] == 700
    assert callbacks.budget_error is not None


def test_native_output_cap_does_not_modify_operator_profile():
    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.config import SubagentConfig

    config = AppConfig.model_validate(
        {"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "models": [{"name": "local", "use": "langchain_openai:ChatOpenAI", "model": "local", "max_tokens": 32768, "when_thinking_disabled": {"max_tokens": 16384}}]}
    )
    role = SubagentConfig(name="researcher", description="research", system_prompt="research", model="local")
    bounded, name = model_budget_config(config, role, 4096)
    assert name == "local"
    assert config.models[0].model_extra["max_tokens"] == 32768
    assert config.models[0].when_thinking_disabled["max_tokens"] == 16384
    assert bounded.models[0].model_extra["max_tokens"] == 4096
    assert bounded.models[0].when_thinking_disabled["max_tokens"] == 4096

    run = {"budget": {"max_model_tokens": 120000}, "usage": {"model_tokens": 0}, "units": ["U1", "U2", "U3"]}
    coordinated, _ = model_budget_config(config, role, 4096, run=run, researcher=True)
    assert not config.token_budget.enabled
    native_policy = coordinated.subagents.get_token_budget_for("researcher")
    assert native_policy.enabled
    assert native_policy.max_tokens == 120000
    assert native_policy.warn_threshold == pytest.approx(0.1)
    assert native_policy.hard_stop_threshold == 1
    assert not coordinated.token_budget.enabled


def test_native_operator_budget_is_not_weakened():
    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.config import SubagentConfig

    config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [{"name": "local", "use": "langchain_openai:ChatOpenAI", "model": "local"}],
            "token_budget": {"enabled": True, "max_tokens": 20000, "warn_threshold": 0.5, "hard_stop_threshold": 0.7, "max_input_tokens": 16000},
        }
    )
    role = SubagentConfig(name="researcher", description="research", system_prompt="research", model="local")
    run = {"budget": {"max_model_tokens": 120000}, "usage": {"model_tokens": 60000}, "units": ["U1", "U2", "U3"]}
    bounded, _ = model_budget_config(config, role, 4096, run=run, researcher=True)
    native_policy = bounded.subagents.get_token_budget_for("researcher")
    assert native_policy.max_tokens == 20000
    assert native_policy.warn_threshold == pytest.approx(0.3)
    assert native_policy.hard_stop_threshold == 0.7
    assert native_policy.max_input_tokens == 16000
    assert bounded.token_budget == config.token_budget


@pytest.mark.asyncio
async def test_unbounded_usage_is_still_reserved_settled_and_traced(settings, tmp_path):
    settings.max_output_tokens = 393216
    store = Store(tmp_path / "unbounded.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "key", "hash")
    callbacks = model_callbacks(LocalTrace(store, "r", settings))
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="hello")]], run_id="one")
    assert (await store.get("r"))["usage"]["model_tokens"] >= 393216
    await callbacks.on_llm_end(response(2500000), run_id="one")
    await store.reserve("r", tool_calls=2000)
    run = await store.get("r")
    assert run["usage"] == {"model_tokens": 2500000, "reported_model_tokens": 2500000, "tool_calls": 2000}
    assert callbacks.budget_error is None
    assert len(await store.trace_events("r")) == 2


def test_unbounded_research_does_not_reenable_native_token_warning():
    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.config import SubagentConfig

    config = AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "models": [{"name": "local", "use": "langchain_openai:ChatOpenAI", "model": "local", "max_tokens": 393216}]})
    role = SubagentConfig(name="researcher", description="research", system_prompt="research", model="local")
    bounded, _ = model_budget_config(config, role, 393216, run={"budget": {"max_model_tokens": None}}, researcher=True)
    assert not bounded.token_budget.enabled
    assert bounded.models[0].model_extra["max_tokens"] == 393216


@pytest.mark.asyncio
async def test_tool_reservation_failure_is_terminal_and_not_left_running(settings, tmp_path):
    store = Store(tmp_path / "denied-tool.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 2}, "budget": {"max_model_tokens": 100000, "max_tool_calls": 2}}, "key", "hash")
    callbacks = model_callbacks(LocalTrace(store, "r", settings))
    with pytest.raises(ResearchError, match="max_tool_calls"):
        await callbacks.on_tool_start({"name": "search"}, "query", run_id="call")
    await callbacks.on_tool_error(RuntimeError("duplicate"), run_id="call")
    await callbacks.on_tool_end("late result", run_id="call")
    calls = await store.calls("r")
    assert calls[0]["status"] == "error" and calls[0]["tool_name"] == "search"
    events = await store.events("r")
    assert sum(e["type"] == "activity.tool.completed" for e in events) == 1
