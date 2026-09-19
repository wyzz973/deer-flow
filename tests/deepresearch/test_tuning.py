"""Per-node tuning, MCP-only deployments and time as a budget.

These are the controls a deployment on a slow internal model needs: which model
and parameters each workflow node uses, research that only has MCP
tools (often without any way to open an original page), an allowlist of MCP
tools, and a time budget that winds research down instead of failing the run.
"""

import asyncio
import time
import types

import pytest
from pydantic import ValidationError

from deepresearch import extract
from deepresearch.channels import BUDGET_STOP, SearchBudget
from deepresearch.config import Settings
from deepresearch.contracts import CreateResearch, ResearchError, ResearchPlan, ResearchUnit
from deepresearch.models import engine_model, model_for, node_output_cap, node_overrides, with_node
from deepresearch.observations import NativeExecution, research_observations
from deepresearch.providers import Request, _render
from deepresearch.report_policy import eligible_evidence, results_citable
from deepresearch.runner import DemoRunner, fit_plan, plain_request
from deepresearch.secrets import SECRETS, references
from deepresearch.store import research_seconds_left, research_time_spent


def configured(settings, **changes):
    return Settings.model_validate({**settings.model_dump(mode="json"), **changes})


MODELS = [
    {"name": "fast", "provider": "openai", "model": "fast-model", "base_url": "http://gateway.internal/v1", "api_key": "$GATEWAY_KEY"},
    {"name": "strong", "provider": "openai", "model": "strong-model", "base_url": "http://gateway.internal/v1", "api_key": "$GATEWAY_KEY"},
]


# --- nodes ---------------------------------------------------------------


def test_each_node_chooses_its_model_and_researchers_keep_their_own(settings):
    skills = settings.model_dump(mode="json")["skills"]
    skills["technical-route"]["model"] = "strong"
    tuned = configured(
        settings, models=MODELS, default_model="fast", skills=skills, nodes={"outline": {"model": "strong", "temperature": 0}, "research": {"model": "fast"}, "section": {"model": "strong", "temperature": 0.6, "max_tokens": 6000}}
    )
    writer, planner = tuned.skills["report-synthesis"], tuned.skills["deepresearch"]
    assert model_for(tuned, writer, None, "outline") == "strong"
    assert model_for(tuned, writer, None, "summary") == "fast"  # unset node: the default model
    assert model_for(tuned, planner, None, "plan") == "fast"
    # A researcher's own model is more specific than the node shared by all researchers.
    assert model_for(tuned, tuned.skills["technical-route"], None, "research") == "strong"
    assert model_for(tuned, tuned.skills["industry-trend"], None, "research") == "fast"
    assert node_overrides(tuned, "section") == {"temperature": 0.6}
    assert node_output_cap(tuned, "section") == 6000 and node_output_cap(tuned, "plan") == tuned.max_output_tokens


def test_node_parameters_merge_into_the_private_model_profile(monkeypatch, settings):
    monkeypatch.setenv("GATEWAY_KEY", "k-123456789")
    profile = engine_model(configured(settings, models=[{**MODELS[0], "extra": {"extra_body": {"enable_thinking": False}}}], default_model="fast").models[0])
    tuned = with_node(profile, {"temperature": 0.1, "top_p": 0.9, "extra_body": {"repetition_penalty": 1.05}})
    assert tuned.temperature == 0.1 and tuned.model_extra["top_p"] == 0.9
    # The node adds a request field without dropping the profile's own.
    assert tuned.model_extra["extra_body"] == {"enable_thinking": False, "repetition_penalty": 1.05}
    assert profile.model_extra["extra_body"] == {"enable_thinking": False}


def test_only_optional_nodes_can_be_disabled_and_node_models_must_exist(settings):
    with pytest.raises(ValidationError, match="Only the rewrite and summary nodes"):
        configured(settings, nodes={"plan": {"enabled": False}})
    with pytest.raises(ValidationError, match="nodes.section.model=missing"):
        configured(settings, models=MODELS, default_model="fast", nodes={"section": {"model": "missing"}})
    with pytest.raises(ValidationError):
        configured(settings, nodes={"not-a-node": {}})
    assert configured(settings, nodes={"rewrite": {"enabled": False}, "summary": {"enabled": False}}).node("rewrite").enabled is False


def test_a_gateway_with_a_base_url_still_reports_streamed_usage(monkeypatch, settings):
    monkeypatch.setenv("GATEWAY_KEY", "k-123456789")
    specs = configured(settings, models=[MODELS[0], {**MODELS[1], "stream_usage": False}, {"name": "claude", "provider": "anthropic", "model": "claude-x", "api_key": "$GATEWAY_KEY"}], default_model="fast").models
    # LangChain's OpenAI client turns stream_usage off once base_url is set;
    # research streams every call, so usage would never be reported.
    assert engine_model(specs[0]).model_extra["stream_usage"] is True
    assert engine_model(specs[1]).model_extra["stream_usage"] is False
    assert "stream_usage" not in (engine_model(specs[2]).model_extra or {})


def test_rewrite_can_be_switched_off_without_losing_a_plan_edit():
    first = plain_request("对比向量数据库", None, "zh")
    assert first.user_query == "对比向量数据库" and not first.acknowledgement
    edited = plain_request("只看开源的", "对比向量数据库", "zh")
    assert "对比向量数据库" in edited.user_query and "只看开源的" in edited.user_query and edited.acknowledgement


@pytest.mark.asyncio
async def test_a_disabled_rewrite_node_never_calls_a_model(settings):
    from deepresearch.runner import DeerFlowRunner

    runner = DeerFlowRunner(configured(settings, nodes={"rewrite": {"enabled": False}}), store=None)
    request = await runner.rewrite({"query": "Compare databases", "conversation": []}, "Compare databases")
    assert request.user_query == "Compare databases"


# --- planner robustness ---------------------------------------------------


def test_a_weak_planner_last_attempt_is_made_runnable_not_rejected(settings):
    open_settings = configured(settings, require_dual_source=False, sources=[{**source, "enabled": source["name"] != "external-read"} for source in settings.model_dump(mode="json")["sources"]])
    plan = ResearchPlan(
        goal="the goal",
        research_units=[
            ResearchUnit(id="R1", skill="made-up-skill", objective="first objective", source_strategy={"source_names": ["external-web", "no-such-source", "external-read"]}),
            ResearchUnit(id="R2", skill="technical-route", objective="second objective", depends_on=["R3"]),
            ResearchUnit(id="R3", skill="technical-route", objective="third objective"),
        ],
    )
    fitted = fit_plan(plan, open_settings, max_units=2)
    assert [unit.id for unit in fitted.research_units] == ["R1", "R2"]
    assert fitted.research_units[0].skill == next(iter(open_settings.researchers()))
    # Unknown and switched-off sources are dropped; dependencies on removed steps too.
    assert fitted.research_units[0].source_strategy.source_names == ["external-web"]
    assert fitted.research_units[1].depends_on == []
    open_settings.check_plan(fitted, types.SimpleNamespace(max_units=2))


# --- sources: switching off, MCP allowlist ----------------------------------


def mcp_only(settings, **changes):
    """Web tools off, one MCP search tool, no way to open originals."""
    sources = [{**source, "enabled": False} for source in settings.model_dump(mode="json")["sources"]]
    sources.append({"name": "internal-search", "tool": "internal_search", "role": "search", "origin": "internal", "providers": [{"id": "kb", "type": "mcp", "server": "kb", "tool": "search_docs"}]})
    body = {
        "sources": sources,
        "require_dual_source": False,
        "source_fallback": ["internal-search"],
        "mcp_servers": {"kb": {"transport": "http", "url": "http://mcp.internal/mcp", "headers": {"Cookie": "sid=${secret:kb-cookie}", "Authorization": "Bearer ${KB_TOKEN}"}, "allowed_tools": ["search_docs"]}},
        "runner": "deerflow",
    }
    return configured(settings, **{**body, **changes})


def test_disabled_sources_are_not_offered_and_mcp_alone_is_a_valid_deployment(settings):
    internal = mcp_only(settings)
    assert [source.name for source in internal.active_sources()] == ["internal-search"]
    plan = ResearchPlan(goal="the goal", research_units=[ResearchUnit(id="R1", skill="technical-route", objective="the objective", source_strategy={"source_names": ["external-web"]})])
    with pytest.raises(ValueError, match="unknown source"):
        internal.check_plan(internal.fit_origins(plan), types.SimpleNamespace(max_units=5))
    with pytest.raises(ValidationError, match="at least one enabled source"):
        configured(internal, sources=[{**source, "enabled": False} for source in internal.model_dump(mode="json")["sources"]])


def test_the_mcp_allowlist_is_checked_when_configuration_loads(settings):
    with pytest.raises(ValidationError, match="not in mcp_servers.kb.allowed_tools"):
        mcp_only(settings, mcp_servers={"kb": {"transport": "http", "url": "http://mcp.internal/mcp", "allowed_tools": ["something_else"]}})
    direct = {"name": "internal-wiki", "tool": "wiki", "kind": "mcp", "server": "kb", "mcp_tool": "delete_page", "origin": "internal", "role": "data"}
    with pytest.raises(ValidationError, match="Source internal-wiki uses MCP tool delete_page"):
        mcp_only(settings, sources=[*mcp_only(settings).model_dump(mode="json")["sources"], direct])


@pytest.mark.asyncio
async def test_the_mcp_allowlist_holds_at_call_time_and_failures_name_their_cause(settings):
    import httpx

    from deepresearch.mcp import McpManager, connection_failure
    from deepresearch.providers import ProviderError

    spec = mcp_only(settings).mcp_servers["kb"]
    with pytest.raises(ProviderError, match="not in the allowed_tools") as refused:
        await McpManager().tool("kb", spec, "delete_page")
    assert refused.value.kind == "config"

    # An expired cookie or token arrives as an HTTP 401 inside a task-group error.
    response = httpx.Response(401, request=httpx.Request("POST", "http://mcp.internal/mcp"), headers={"set-cookie": "sid=LEAK"})
    grouped = ExceptionGroup("unhandled errors in a TaskGroup", [httpx.HTTPStatusError("401 Unauthorized sid=LEAK", request=response.request, response=response)])
    failure = connection_failure("kb", grouped)
    assert failure.kind == "auth" and failure.status == 401 and "LEAK" not in str(failure)
    assert connection_failure("kb", ExceptionGroup("g", [httpx.ConnectError("refused")])).kind == "network"
    assert connection_failure("kb", ExceptionGroup("g", [TimeoutError()])).kind == "timeout"


def test_headers_can_wrap_a_credential_and_the_value_is_still_redacted(monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "tok-abcdefgh")
    SECRETS.set("kb-cookie", "cookie-12345678")
    try:
        headers = {"Cookie": "sid=${secret:kb-cookie}; lang=zh", "Authorization": "Bearer ${KB_TOKEN}", "X-Whole": "secret:kb-cookie", "Accept": "application/json"}
        assert SECRETS.expand(headers) == {"Cookie": "sid=cookie-12345678; lang=zh", "Authorization": "Bearer tok-abcdefgh", "X-Whole": "cookie-12345678", "Accept": "application/json"}
        # A request's own credential (request_secret_headers) wins for that request only.
        assert SECRETS.expand(headers, {"kb-cookie": "per-user"})["Cookie"] == "sid=per-user; lang=zh"
        assert references(headers) == {"secret:kb-cookie", "$KB_TOKEN"}
    finally:
        SECRETS.set("kb-cookie", None)


class Ledger:
    """The store's budget side: counts reservations and refuses past the ceiling."""

    def __init__(self, ceiling=None):
        self.calls, self.ceiling, self.events = 0, ceiling, []

    async def reserve(self, run_id, tool_calls=0, **_):
        if self.ceiling is not None and self.calls + tool_calls > self.ceiling:
            raise ResearchError("BUDGET_EXHAUSTED", "预算已用尽: max_tool_calls", recoverable=False)
        self.calls += tool_calls

    async def event(self, run_id, kind, data=None, key=None):
        self.events.append((kind, data))


@pytest.mark.asyncio
async def test_a_directly_exposed_mcp_tool_is_metered_like_any_source(monkeypatch, settings):
    """``kind: mcp`` sources used to be exempt from every tool budget.

    The model callback leaves research-owned sources to account for their own
    calls, and only provider-based sources did.
    """
    from langchain_core.tools import StructuredTool

    from deepresearch import mcp

    served = []

    async def remote(query: str):
        served.append(query)
        return "answer for " + query

    async def find(name, spec, tool_name, *, request=None):
        return StructuredTool.from_function(coroutine=remote, name=tool_name, description="Search internal documents")

    monkeypatch.setattr(mcp.MANAGER, "tool", find)
    internal = mcp_only(settings)
    source = types.SimpleNamespace(name="internal-wiki", tool="wiki", server="kb", mcp_tool="search_docs", role="data", description="")
    ledger = Ledger(ceiling=10)
    tool = await mcp.source_tool(source, internal.mcp_servers, None, SearchBudget(ledger, "run", "R1", limit=2))
    for query in ("a", "b", "c"):
        message = await tool.ainvoke({"type": "tool_call", "name": "wiki", "args": {"query": query}, "id": "call-" + query})
    assert served == ["a", "b"] and ledger.calls == 2
    assert message.artifact == {"schema": BUDGET_STOP, "reason": "step"} and "No more searches" in message.content
    assert [kind for kind, _ in ledger.events] == ["research.search.limited"]


@pytest.mark.asyncio
async def test_time_winds_a_step_down_through_its_tools():
    ledger = Ledger()
    budget = SearchBudget(ledger, "run", "R1", limit=30, deadline=time.monotonic() - 1)
    stop = await budget.reserve("read")
    assert stop.reason == "time" and "Write your research notes now" in stop.text and ledger.calls == 0
    assert [data["scope"] for _, data in ledger.events] == ["time"]
    assert await SearchBudget(ledger, "run", "R1", limit=30, deadline=time.monotonic() + 60).reserve() is None


# --- research without any way to open an original --------------------------------


def test_search_results_are_the_evidence_when_nothing_can_open_a_page(settings):
    assert results_citable(settings) is False  # the example has a read source
    internal = mcp_only(settings)
    assert internal.cite_search_results is False and results_citable(internal) is True
    artifact = {
        "schema": "deepresearch.search.v1",
        "results": [
            {"title": "差旅报销标准", "url": "https://wiki.internal/travel", "snippet": "一线城市住宿每晚不超过 600 元。"},
            {"title": "年假规定", "url": None, "snippet": "入职满一年的员工享有 10 天带薪年假。", "id": "D-2"},
        ],
    }
    execution = NativeExecution(
        "notes",
        "exec-1",
        [
            {"type": "ai", "tool_calls": [{"id": "c1", "name": "internal_search", "args": {"query": "报销 标准"}}]},
            {
                "type": "tool",
                "tool_call_id": "c1",
                "name": "internal_search",
                "status": "success",
                "artifact": artifact,
                "content": "1. [差旅报销标准](https://wiki.internal/travel)\n   一线城市住宿每晚不超过 600 元。\n2. 年假规定\n   入职满一年的员工享有 10 天带薪年假。",
            },
        ],
        [{"id": "r1", "tool_call_id": "c1", "tool_name": "internal_search", "status": "success"}],
    )
    source = next(item for item in internal.sources if item.name == "internal-search")
    evidences, catalog = research_observations(execution, [source], cite_search_results=True)
    records = [item for item in evidences if item.raw_id.startswith("rec_")]
    # Each result is cited on its own, with its title; one without a link still counts.
    assert [(item.title, item.url) for item in records] == [("差旅报销标准", "https://wiki.internal/travel"), ("年假规定", None)]
    # The combined output says what was asked, and the link is not listed twice.
    envelope = next(item for item in evidences if item.raw_id.startswith("raw_"))
    assert envelope.title == "internal-search: 报销 标准"
    assert not [item for item in evidences if item.provenance == "observed_source"]
    assert next(item for item in catalog if item["raw_id"] == envelope.raw_id)["superseded"] is True
    pool = {f"E{index:03d}": {**item.model_dump(mode="json"), "evidence_id": f"E{index:03d}"} for index, item in enumerate(records, 1)}
    assert set(eligible_evidence(pool, {}, internal)) == set(pool)
    # With a read source the same results only prove discovery.
    default, _ = research_observations(execution, [source])
    assert not [item for item in default if item.raw_id.startswith("rec_")]


@pytest.mark.asyncio
async def test_a_step_without_a_read_tool_is_not_told_to_open_pages(monkeypatch, settings):
    from deepresearch import runner as runner_module
    from deepresearch import structured
    from deepresearch.runner import DeerFlowRunner, ResearchAnalysis

    internal = mcp_only(settings)
    seen = {}

    class Store:
        async def get(self, run_id):
            return {"query": "差旅标准", "plan": {"brief": "b"}, "budget": {"max_tool_calls": None, "max_elapsed_seconds": None}, "usage": {}}

        async def event(self, *args, **kwargs):
            pass

        async def cached(self, run_id, key):
            return None

        async def cache(self, run_id, key, body):
            pass

    async def native(settings, store, run, name, payload, tools, agent, context, **kwargs):
        seen.update(instructions=payload["instructions"], tools=[(tool.name, tool.description) for tool in tools], timeout=kwargs.get("timeout_seconds"))
        return NativeExecution("notes", "exec-1", [], [])

    async def convert(runner, run, name, payload, schema, answer, context, **kwargs):
        seen["conversion"] = payload["instructions"]
        return ResearchAnalysis(confidence=0.5)

    async def agent_config(skill):
        return internal.skills[skill], types.SimpleNamespace(tools=None, disallowed_tools=[])

    monkeypatch.setattr("deepresearch.native.execute_role", native)
    monkeypatch.setattr(structured, "convert_answer", convert)
    runner = DeerFlowRunner(internal, Store())
    monkeypatch.setattr(runner, "_agent_config", agent_config)
    unit = ResearchUnit(id="R1", skill="technical-route", objective="the objective", source_strategy={"required_origins": ["internal"]})
    run = {"run_id": "r", "source_names": [], "budget": {"max_tool_calls": None, "max_elapsed_seconds": None}, "cycle": 0}
    await runner.research(run, unit, {})
    assert seen["instructions"] == internal.prompts.research_records and "no tool that opens a full page" in seen["instructions"]
    assert seen["conversion"] == internal.prompts.conversion_records
    assert seen["tools"][0][0] == "internal_search" and "can be cited as evidence" in seen["tools"][0][1]
    assert seen["timeout"] is None  # no time ceiling, no deadline
    assert runner_module.NOTES_SHARE < 1


def test_untitled_chunks_and_wrapped_lists_are_still_records():
    chunks = {"code": 0, "data": {"chunks": [{"content": "差旅报销标准：一线城市住宿每晚不超过 600 元，需在出差结束后 15 个工作日内提交。", "score": 0.91, "doc_id": "D-1"}, {"content": "短", "score": 0.1}]}}
    found = extract.records(chunks)
    assert [(item["id"], item["url"]) for item in found] == [("D-1", None)] and found[0]["title"].startswith("差旅报销标准")
    # An envelope with a name and description of its own is not the single result.
    envelope = {"name": "kb_search", "description": "Search the knowledge base", "items": [{"title": "A", "url": "https://wiki.corp/a", "summary": "aaa"}, {"title": "B", "url": "https://wiki.corp/b", "summary": "bbb"}]}
    assert [item["title"] for item in extract.records(envelope)] == ["A", "B"]
    assert extract.records({"title": "Only one", "url": "https://x.com/1", "content": "single"})[0]["title"] == "Only one"
    long_line = "这是一段没有标题的知识库片段，" * 12
    assert len(extract.records([{"text": long_line}])[0]["title"]) <= 81
    assert "excerpts are the evidence" in extract.render_search("q", found, "kb", citable=True)
    assert "open a page before relying" in extract.render_search("q", found, "kb")


def test_a_query_cannot_rewrite_the_operators_api_url():
    request = Request("search", query="a&admin=1 #x 中文", max_results=5)
    assert _render("https://api.internal/s?q={query}&n={max_results}", request, encode=True) == "https://api.internal/s?q=a%26admin%3D1%20%23x%20%E4%B8%AD%E6%96%87&n=5"
    page = Request("read", url="https://a.com/p?x=1")
    assert _render("https://reader.internal/{url}", page, encode=True) == "https://reader.internal/https://a.com/p?x=1"
    assert _render("https://reader.internal/get?u={url}", page, encode=True) == "https://reader.internal/get?u=https%3A%2F%2Fa.com%2Fp%3Fx%3D1"
    assert _render({"q": "{query}", "n": "{max_results}"}, request) == {"q": "a&admin=1 #x 中文", "n": 5}


# --- time is a budget that winds research down ------------------------------------


def test_research_keeps_time_back_for_the_report(settings):
    run = {"budget": {"max_elapsed_seconds": 1200}, "usage": {"elapsed_seconds": 700}, "drive_started_at": time.time() - 100}
    assert 95 <= research_seconds_left(run, settings) <= 100  # 1200 - 300 reserve - 700 - 100 running
    assert research_time_spent({**run, "usage": {"elapsed_seconds": 790}}, settings)
    assert research_seconds_left({"budget": {"max_elapsed_seconds": None}, "usage": {}}, settings) is None
    assert research_seconds_left(run, configured(settings, report_time_reserve_seconds=60)) > 300


async def settle(service, run_id):
    for _ in range(1000):
        if service.tasks.get(run_id) is None:
            return await service.store.get(run_id)
        await asyncio.sleep(0.01)
    raise AssertionError("workflow did not settle")


@pytest.mark.asyncio
async def test_running_out_of_time_still_ends_in_a_report(settings):
    from deepresearch.service import ResearchService

    service = ResearchService(settings)

    class OutOfTime(DemoRunner):
        supplements = []

        async def research(self, run, unit, dependencies):
            if unit.id.startswith("S"):
                self.supplements.append(unit.id)
            if unit.id == "R2":
                raise ResearchError("RESEARCH_TIME_SPENT", "研究时间预算已用尽，剩余时间留给报告写作", recoverable=False)
            result = await super().research(run, unit, dependencies)
            # The clock runs out while the first step works.
            await self.store.mutate(run["run_id"], lambda current: current["usage"].update(elapsed_seconds=current["budget"]["max_elapsed_seconds"]))
            return result

    runner = OutOfTime(settings, service.store)
    service.runner = runner
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="时间用尽也要有报告"), "time")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert run["unit_failures"] == {"R2": "RESEARCH_TIME_SPENT"}
        assert runner.supplements == []  # no time left to chase gaps
        assert any("RESEARCH_TIME_SPENT" in text for text in run["report"]["audit"]["limitations"])
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_a_deployment_chooses_which_gaps_are_worth_another_round(settings):
    from deepresearch.service import ResearchService

    class Curious(DemoRunner):
        supplements = []

        async def research(self, run, unit, dependencies):
            if unit.id.startswith("S"):
                self.supplements.append(unit.id)
            result = await super().research(run, unit, dependencies)
            return result.model_copy(update={"open_questions": ["还有一个可以继续查的问题"]})

    async def finished(tuned, key):
        service = ResearchService(tuned)
        runner = Curious(tuned, service.store)
        service.runner = runner
        Curious.supplements = []
        await service.start()
        try:
            run = await service.create("u", CreateResearch(query="开放问题是否值得补研"), key)
            run = await settle(service, run["run_id"])
            await service.decision(run["run_id"], 1, "approve")
            run = await settle(service, run["run_id"])
            assert run["status"] == "COMPLETED", run["error"]
            return run, list(runner.supplements)
        finally:
            await service.stop()

    _, chased = await finished(settings, "default")
    assert chased  # open questions start another round by default
    quiet, skipped = await finished(configured(settings, supplement_gap_codes=["coverage", "unsupported"], data_dir=settings.data_dir + "-quiet"), "quiet")
    assert skipped == [] and quiet["iteration"] == 0
    # The unanswered question is disclosed instead of researched.
    assert any("还有一个可以继续查的问题" in text for text in quiet["report"]["audit"]["limitations"])


def test_report_length_is_a_setting_not_a_constant(settings):
    from deepresearch.report_policy import section_target, summary_target

    full, half = section_target("standard"), section_target("standard", 0.5)
    assert (full["target_characters"], full["soft_maximum_characters"]) == (1200, 3000)
    assert (half["target_characters"], half["soft_maximum_characters"]) == (600, 1500)
    # A shape, because a character count alone was ignored: 0.45 shortened a real report by only 14%.
    assert "at most one compact table" in half["shape"] and half["shape"] != full["shape"]
    assert summary_target("brief", 0.2)["target_characters"] == 150  # never asks for nothing
    assert configured(settings, report_length_scale=0.4).report_length_scale == 0.4
    with pytest.raises(ValidationError):
        configured(settings, report_length_scale=5)


@pytest.mark.asyncio
async def test_a_section_far_past_its_ceiling_gets_one_shortening_repair(monkeypatch, settings):
    """Only where the deployment asked for shorter reports; the default leaves length as guidance."""
    from deepresearch.runner import DeerFlowRunner

    answers = ["**判断** 事实 [[E001]]。" + "补充说明。" * 400, "**判断** 事实 [[E001]]。"]
    requests = []

    async def native(settings, store, run, name, payload, tools, agent, context, **kwargs):
        requests.append(payload)
        return NativeExecution(answers[min(len(requests), len(answers)) - 1], "exec", [], [])

    class Store:
        events = []

        async def event(self, run_id, kind, data=None, key=None):
            self.events.append(kind)

    monkeypatch.setattr("deepresearch.native.execute_role", native)
    runner = DeerFlowRunner(settings, Store())
    text, audit = await runner._markdown({"run_id": "r"}, {"task": "write_section"}, {"E001"}, task_id="report-section-1", limit=500)
    assert text == "**判断** 事实 [[E001]]。" and audit["over_length"] == 1 and audit["repairs"] == 1
    assert "limit is 500" in requests[1]["validation_errors_to_fix"][0]
    # Without a limit the long answer is accepted as it is.
    requests.clear()
    text, audit = await runner._markdown({"run_id": "r"}, {"task": "write_section"}, {"E001"}, task_id="report-section-1")
    assert len(text) > 1500 and audit["repairs"] == 0 and len(requests) == 1
