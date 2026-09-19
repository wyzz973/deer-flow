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
    # A call that reports no usage is charged what it could have written: the
    # part of the output cap its reservation did not cover.
    from deepresearch.store import RESEARCH_TURN_OUTPUT

    assert (await store.get("r"))["usage"]["model_tokens"] == first + 120 + max(0, settings.max_output_tokens - RESEARCH_TURN_OUTPUT)


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
    # Three steps split what research may spend (120k minus the 60k report
    # reserve), less a margin for turning each step's notes into findings.
    assert native_policy.max_tokens == 60000 // 3 - 2 * 4096
    assert native_policy.warn_threshold == pytest.approx(0.5)
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
    run = {"budget": {"max_model_tokens": 120000}, "usage": {"model_tokens": 0}, "units": ["U1"]}
    bounded, _ = model_budget_config(config, role, 4096, run=run, researcher=True)
    native_policy = bounded.subagents.get_token_budget_for("researcher")
    assert native_policy.max_tokens == 20000  # the operator's cap is below this step's share
    assert native_policy.warn_threshold == pytest.approx(0.5)
    assert native_policy.hard_stop_threshold == 0.7
    assert native_policy.max_input_tokens == 16000
    assert bounded.token_budget == config.token_budget


@pytest.mark.asyncio
async def test_unbounded_usage_is_still_reserved_settled_and_traced(settings, tmp_path):
    settings.max_output_tokens = 393216
    store = Store(tmp_path / "unbounded.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "key", "hash")
    callbacks = model_callbacks(LocalTrace(store, "r", settings), scope={"phase": "synthesis"})
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="hello")]], run_id="one")
    assert (await store.get("r"))["usage"]["model_tokens"] >= 8192
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


@pytest.mark.asyncio
async def test_research_sources_are_counted_once_and_never_end_the_run(settings, tmp_path):
    """A source tool accounts for its own call through the step budget.

    Reserving again in the callback would both double-count it and bring back
    the fatal BUDGET_EXHAUSTED the step budget exists to avoid; engine and host
    tools keep the hard reservation.
    """
    from deepresearch.config import SourceSpec
    from deepresearch.trace import LocalTrace, model_callbacks

    configured = settings.model_copy(update={"sources": [SourceSpec(name="web", tool="web_search", role="search", origin="external", providers=[{"id": "ddg", "type": "duckduckgo"}])]})
    store = Store(tmp_path / "self-metered.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": 1}}, "key", "hash")
    callbacks = model_callbacks(LocalTrace(store, "r", configured, ()), scope={})

    await callbacks.on_tool_start({"name": "web_search"}, "{}", run_id="call-1")
    assert (await store.get("r"))["usage"]["tool_calls"] == 0  # the tool itself reserves

    await callbacks.on_tool_start({"name": "read_file"}, "{}", run_id="call-2")
    assert (await store.get("r"))["usage"]["tool_calls"] == 1

    with pytest.raises(ResearchError, match="预算已用尽"):
        await callbacks.on_tool_start({"name": "read_file"}, "{}", run_id="call-3")


@pytest.mark.asyncio
async def test_research_stops_early_enough_to_still_write_the_report(settings, tmp_path):
    """The token budget must wind research down, not end the run.

    Research calls keep a reserve for the report, so a run that spends its
    budget still reaches synthesis; only a call that would exceed the whole
    ceiling is refused.
    """
    store = Store(tmp_path / "reserve.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": 100000, "max_tool_calls": None}}, "key", "hash")

    # Research may spend up to the ceiling minus the report reserve (half of a
    # small budget: one measured report took 8 calls and ~145k tokens).
    await store.reserve("r", model_tokens=45000, purpose="research")
    with pytest.raises(ResearchError) as spent:
        await store.reserve("r", model_tokens=10000, purpose="research")
    assert spent.value.code == "RESEARCH_BUDGET_SPENT"
    assert (await store.get("r"))["usage"]["model_tokens"] == 45000

    # The report writer may use the reserve that was kept for it.
    await store.reserve("r", model_tokens=45000, purpose="report")
    with pytest.raises(ResearchError) as exhausted:
        await store.reserve("r", model_tokens=20000, purpose="report")
    assert exhausted.value.code == "BUDGET_EXHAUSTED"
    assert (await store.get("r"))["usage"]["model_tokens"] == 90000


@pytest.mark.asyncio
async def test_an_unlimited_budget_reserves_nothing(settings, tmp_path):
    store = Store(tmp_path / "unbounded-reserve.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "key", "hash")
    await store.reserve("r", model_tokens=10**9, purpose="research")
    assert (await store.get("r"))["usage"]["model_tokens"] == 10**9


@pytest.mark.asyncio
async def test_research_turns_reserve_what_they_realistically_write(settings, tmp_path):
    """A research turn is a tool call or a short note, not a full-length answer.

    Reserving the whole output cap per turn made a budget run out at a fraction
    of its real spend, and three parallel report sections at 32768 each could
    not fit a 120k budget at all. Every call reserves a realistic output (a
    report call more than a research turn) and settles to the reported usage;
    a provider that reports no usage is still charged the full cap.
    """
    settings.max_output_tokens = 32768
    store = Store(tmp_path / "estimate.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": 1_000_000, "max_tool_calls": None}}, "key", "hash")
    messages = [[HumanMessage(content="short task")]]

    research = model_callbacks(LocalTrace(store, "r", settings), scope={"phase": "dispatch"})
    await research.on_chat_model_start({}, messages, run_id="turn")
    reserved = (await store.get("r"))["usage"]["model_tokens"]
    assert reserved < 4096 + 200  # the input plus a realistic turn, not 32768

    writer = model_callbacks(LocalTrace(store, "r", settings), scope={"phase": "synthesis"})
    await writer.on_chat_model_start({}, messages, run_id="section")
    assert 8192 < (await store.get("r"))["usage"]["model_tokens"] - reserved < 8192 + 200

    # No usage reported for the research turn: charge the conservative amount.
    before = (await store.get("r"))["usage"]["model_tokens"]
    await research.on_llm_end(response(), run_id="turn")
    assert (await store.get("r"))["usage"]["model_tokens"] - before == 32768 - 4096


def test_research_is_spent_once_it_cannot_afford_a_turn():
    """A few tokens above zero are not a research budget.

    Live run 40e5b85a kept dispatching supplements with about 4k research tokens
    left; each one failed on its first model call.
    """
    from deepresearch.store import RESEARCH_TURN_OUTPUT, report_reserve, research_spent

    run = {"budget": {"max_model_tokens": 120000}, "usage": {"model_tokens": 0}}
    assert not research_spent(run)
    run["usage"]["model_tokens"] = 120000 - report_reserve(run) - RESEARCH_TURN_OUTPUT
    assert research_spent(run)
    assert not research_spent({"budget": {"max_model_tokens": None}, "usage": {"model_tokens": 10**9}})
    assert research_spent({"budget": {"max_model_tokens": 10**9}, "usage": {"model_tokens": 0}, "unit_failures": {"R1": "RESEARCH_BUDGET_SPENT"}})


def test_each_step_is_stopped_at_its_share_so_it_still_writes_notes():
    """The native budget middleware ends a step with an answer, not an error.

    Live run 1f2eb215 had three steps reading pages in parallel until the
    shared research budget refused a model turn mid-step; every step failed and
    everything it had read was lost. Each step now gets its share of what
    research may still spend, is warned at half of it, and is stopped at it,
    which strips tool calls so the agent writes its notes.
    """
    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.config import SubagentConfig

    config = AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "models": [{"name": "local", "use": "langchain_openai:ChatOpenAI", "model": "local"}]})
    role = SubagentConfig(name="researcher", description="research", system_prompt="research", model="local")
    units = [{"id": uid} for uid in ("U1", "U2", "U3", "U4")]
    run = {"budget": {"max_model_tokens": 120000}, "usage": {"model_tokens": 12000}, "units": units, "unit_statuses": {"U1": "COMPLETED"}}
    bounded, _ = model_budget_config(config, role, 4096, run=run, researcher=True, concurrency=2)
    policy = bounded.subagents.get_token_budget_for("researcher")
    # 48k left for research, three steps unfinished, two run at a time.
    assert policy.max_tokens == 48000 // 2 - 2 * 4096
    assert policy.warn_threshold == pytest.approx(0.5) and policy.hard_stop_threshold == 1

    spent = {**run, "usage": {"model_tokens": 95000}}
    bounded, _ = model_budget_config(config, role, 4096, run=spent, researcher=True, concurrency=2)
    assert bounded.subagents.get_token_budget_for("researcher").max_tokens == 1000  # stop after one turn


@pytest.mark.asyncio
async def test_turning_a_finished_step_into_findings_may_use_the_report_reserve(settings, tmp_path):
    """The step's research is already paid for; losing it on its last call wastes all of it."""
    store = Store(tmp_path / "conversion.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 95000, "tool_calls": 0}, "budget": {"max_model_tokens": 120000, "max_tool_calls": None}}, "key", "hash")
    messages = [[HumanMessage(content="notes")]]
    turn = model_callbacks(LocalTrace(store, "r", settings), scope={"phase": "dispatch", "purpose": "agent"})
    with pytest.raises(ResearchError) as refused:
        await turn.on_chat_model_start({}, messages, run_id="turn")
    assert refused.value.code == "RESEARCH_BUDGET_SPENT"
    conversion = model_callbacks(LocalTrace(store, "r", settings), scope={"phase": "dispatch", "purpose": "conversion"})
    await conversion.on_chat_model_start({}, messages, run_id="convert")
    reserved = (await store.get("r"))["usage"]["model_tokens"] - 95000
    assert 0 < reserved < 4096 + 200  # a conversion is sized like a research turn, not a report
