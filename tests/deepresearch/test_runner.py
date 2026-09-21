"""The researcher sees original tool schemas and results, not a search adapter."""

import types
from contextlib import asynccontextmanager
from datetime import UTC

import pytest

from deepresearch.contracts import Finding, ResearchError
from deepresearch.observations import NativeExecution
from deepresearch.runner import DeerFlowRunner, ResearchAnalysis
from deepresearch.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_result", ["arbitrary plain text", {"unexpected": {"rows": [1, 2]}}, [{"type": "text", "text": "markdown result"}]])
async def test_native_tools_unchanged_and_execution_cached(settings, plan, tmp_path, monkeypatch, raw_result):
    store = Store(tmp_path / "runner.sqlite")
    await store.start()
    run = {"run_id": "r", "thread_id": "dr-r", "owner": "u", "source_names": [], "usage": {"tool_calls": 0, "model_tokens": 0}, "budget": {"max_tool_calls": 10, "max_model_tokens": 100000}}
    run["steering"] = [{"id": "m1", "text": "只看官方文档", "at": "now"}]
    await store.create(run, "k", "h")
    runner = DeerFlowRunner(settings, store)
    invoked = []

    class OriginalTool:
        args = {"term": {"type": "string"}, "options": {"type": "object"}}

        def __init__(self, source):
            self.name = source.tool

        async def ainvoke(self, args):
            assert args == {"term": "research", "options": {"count": 3}}
            invoked.append(self.name)
            return raw_result

    original_tools = [OriginalTool(source) for source in settings.sources]

    @asynccontextmanager
    async def source_tools(run, sources, budget=None):
        assert invoked == []  # no hardcoded search before the model runs
        yield {s.name: tool for s, tool in zip(sources, original_tools, strict=True)}

    async def agent_config(skill):
        return settings.skills[skill], types.SimpleNamespace(tools=None, disallowed_tools=[])

    async def native(settings, store, run, name, payload, tools, agent, context, **kwargs):
        assert tools == original_tools
        # Updates sent while research runs reach units that start afterwards.
        assert payload["user_updates"] == ["只看官方文档"]
        # The step knows what it may spend before it starts searching.
        assert payload["search_budget"] == {"max_searches_this_step": settings.max_searches_per_unit, "shared_tool_calls_left": 10}
        assert "read tool" in payload["instructions"] and "never be cited" in payload["instructions"]
        # A prompt cache reuses a prefix: what every step shares leads, the
        # step itself follows, and counters that change between steps end it.
        order = list(payload)
        assert order[0] == "instructions" and order[-2:] == ["search_budget", "shared_run_budget"]
        assert order.index("research_brief") < order.index("sources") < order.index("unit") < order.index("dependencies")
        messages, receipts = [], []
        for index, tool in enumerate(tools):
            response = await tool.ainvoke({"term": "research", "options": {"count": 3}})
            assert response is raw_result
            call_id = f"call-{index}"
            messages.extend(
                [
                    {"type": "ai", "tool_calls": [{"id": call_id, "name": tool.name, "args": {"term": "research"}}]},
                    {"type": "tool", "tool_call_id": call_id, "name": tool.name, "content": str(response), "status": "success"},
                ]
            )
            receipts.append({"id": f"r{index + 1}", "tool_call_id": call_id, "tool_name": tool.name, "status": "success"})
        return NativeExecution("Research notes [r1] [r2]", "native-exec", messages, receipts)

    async def convert(runner, run, name, payload, schema, answer, context, **kwargs):
        assert answer == "Research notes [r1] [r2]"
        assert list(payload)[0] == "instructions" and list(payload).index("unit") < list(payload).index("observed_calls")
        assert all("excerpt" not in call for call in payload["observed_calls"])
        refs = [e["raw_id"] for e in payload["observed_calls"]]
        return ResearchAnalysis(findings=[Finding(claim="claim", raw_evidence_refs=refs, confidence=0.8)], confidence=0.8)

    monkeypatch.setattr(runner, "_source_tools", source_tools)
    monkeypatch.setattr(runner, "_agent_config", agent_config)
    monkeypatch.setattr("deepresearch.native.execute_role", native)
    monkeypatch.setattr("deepresearch.structured.convert_answer", convert)
    result = await runner.research(run, plan.research_units[0], {})
    assert len(invoked) == len(settings.sources)
    assert {e.origin for e in result.raw_evidences} == {"internal", "external"}
    assert all(e.provenance == "tool_output" for e in result.raw_evidences)
    await runner.research(run, plan.research_units[0], {})
    assert len(invoked) == len(settings.sources)  # conversion retry reuses the whole completed execution

    async def fabricated(*args, **kwargs):
        return ResearchAnalysis(findings=[Finding(claim="claim", raw_evidence_refs=["fabricated"], confidence=0.8)], confidence=0.8)

    monkeypatch.setattr("deepresearch.structured.convert_answer", fabricated)
    with pytest.raises(ResearchError, match="unobserved"):
        await runner.research(run, plan.research_units[0], {})


@pytest.mark.asyncio
async def test_discovery_volume_never_fails_a_long_research_unit(settings, plan, tmp_path, monkeypatch):
    # A long unit can see hundreds of search-result links. They are discovery
    # only; the result keeps referenced, read and tool evidence within the cap.
    store = Store(tmp_path / "runner.sqlite")
    await store.start()
    run = {"run_id": "r", "thread_id": "dr-r", "owner": "u", "source_names": [], "usage": {"tool_calls": 0, "model_tokens": 0}, "budget": {"max_tool_calls": 10, "max_model_tokens": 100000}}
    await store.create(run, "k", "h")
    runner = DeerFlowRunner(settings, store)
    source = settings.sources[0]

    @asynccontextmanager
    async def source_tools(run, sources, budget=None):
        yield {}

    async def agent_config(skill):
        return settings.skills[skill], types.SimpleNamespace(tools=None, disallowed_tools=[])

    async def native(settings, store, run, name, payload, tools, agent, context, **kwargs):
        messages, receipts = [], []
        for call in range(7):  # 7 records, each mentioning 100 distinct links
            links = "\n".join(f"- [Result {call}-{index}](https://example{call}-{index}.com/page)" for index in range(100))
            messages += [
                {"type": "ai", "tool_calls": [{"id": f"call-{call}", "name": source.tool, "args": {"term": "research"}}]},
                {"type": "tool", "tool_call_id": f"call-{call}", "name": source.tool, "content": f"Record {call} is active.\n" + links, "status": "success"},
            ]
            receipts.append({"id": f"r{call}", "tool_call_id": f"call-{call}", "tool_name": source.tool, "status": "success"})
        return NativeExecution("notes", "native-exec", messages, receipts, stop_reason="max_turns")

    async def convert(runner, run, name, payload, schema, answer, context, **kwargs):
        record = next(call["raw_id"] for call in payload["observed_calls"] if call.get("raw_id", "").startswith("raw_"))
        limitations = [f"limitation {index}" for index in range(30)]
        return ResearchAnalysis(findings=[Finding(claim="Record 42 is active", raw_evidence_refs=[record], confidence=0.8)], limitations=limitations, confidence=0.8)

    monkeypatch.setattr(runner, "_source_tools", source_tools)
    monkeypatch.setattr(runner, "_agent_config", agent_config)
    monkeypatch.setattr("deepresearch.native.execute_role", native)
    monkeypatch.setattr("deepresearch.structured.convert_answer", convert)
    result = await runner.research(run, plan.research_units[0], {})
    kept = {e.raw_id: e for e in result.raw_evidences}
    assert len(result.raw_evidences) == 500
    assert result.findings[0].raw_evidence_refs[0] in kept
    assert sum(e.provenance == "tool_output" for e in result.raw_evidences) == 7
    assert sum(e.provenance == "observed_source" for e in result.raw_evidences) == 493
    assert len(result.limitations) == 30 and "max_turns" in result.limitations[-1]
    events = await store.events("r", 0)
    trimmed = next(e["data"] for e in events if e["type"] == "research.evidence.trimmed")
    assert trimmed == {"unit_id": plan.research_units[0].id, "dropped": 207, "kept": 500}


def test_result_contract_errors_are_not_reported_as_fabricated_references():
    from pydantic import ValidationError

    from deepresearch.contracts import ResearchResult
    from deepresearch.runner import result_error

    with pytest.raises(ValidationError) as oversized:
        ResearchResult(unit_id="u", confidence=0.5, limitations=["x"] * 31)
    error = result_error(oversized.value)
    assert error.code == "RESULT_CONTRACT" and "limitations" in str(error)
    with pytest.raises(ValidationError) as unknown:
        ResearchResult(unit_id="u", confidence=0.5, findings=[Finding(claim="c", raw_evidence_refs=["missing"], confidence=0.5)])
    assert result_error(unknown.value).code == "EVIDENCE_REFERENCE"


@pytest.mark.asyncio
async def test_writer_outlines_then_writes_sections_from_citable_evidence_only(settings, plan, tmp_path, monkeypatch):
    from deepresearch import report as documents
    from deepresearch.contracts import OutlineSection, ReportOutline

    store = Store(tmp_path / "writer.sqlite")
    await store.start()
    supplement = {"id": "S1-abc", "skill": "technical-route", "objective": "supplement", "parent_gap_id": "R1-coverage", "depends_on": ["R1"]}
    run = {"run_id": "r", "owner": "u", "query": "比较数据库", "cycle": 0, "units": [unit.model_dump(mode="json") for unit in plan.research_units] + [supplement], "steering": [{"id": "m", "text": "加上运维成本", "at": "now"}]}
    await store.create(run, "k", "h")
    runner = DeerFlowRunner(settings, store)

    def page(url, provenance="fetched_document"):
        return {"url": url, "canonical_url": url, "source_uri": None, "title": "Guide", "origin": "external", "source_name": "web", "snippet": "Quoted text", "provenance": provenance, "document_hash": url, "published_at": None}

    pool = {"E001": page("https://a.example/doc"), "E002": page("https://search.example/result", "observed_source"), "E003": page("https://b.example/doc")}
    findings = [
        {"unit_id": "R1", "claim": "A", "evidence_ids": ["E001", "E002"], "confidence": 0.8, "high_risk": True},
        {"unit_id": "S1-abc", "claim": "B", "evidence_ids": ["E003"], "confidence": 0.7, "high_risk": False},
    ]
    results = [{"unit_id": "R1", "summary": "单元摘要", "assumptions_needed": ["团队规模未知"]}]
    outlines, drafts = [], []

    async def outline(_run, _skill, payload, _schema, *_, validator=None, repair=None, task_id=None):
        outlines.append((task_id, payload))
        value = ReportOutline(title="数据库选型研究", key_conclusions=["结论"], sections=[OutlineSection(heading="一、并发写入", purpose="p", unit_ids=["R1"])], limitations=["公开基准有限"])
        validator(value)
        with pytest.raises(ValueError, match="missing: R1"):
            validator(ReportOutline(title="t", key_conclusions=["k"], sections=[OutlineSection(heading="h", purpose="p", unit_ids=[])]))
        # The summary, scope and references are assembled separately.
        duplicated = ReportOutline(title="t", key_conclusions=["k"], sections=[OutlineSection(heading="一、执行摘要与行动清单", purpose="p", unit_ids=["R1"]), OutlineSection(heading="二、并发写入", purpose="p", unit_ids=[])])
        with pytest.raises(ValueError, match="generated separately"):
            validator(duplicated)
        repaired = repair(duplicated)
        validator(repaired)
        assert [(section.heading, section.unit_ids) for section in repaired.sections] == [("并发写入", ["R1"])]
        return value

    async def markdown(_run, payload, allowed, *, task_id, heading=None, whole_document=False, limit=None):
        drafts.append((task_id, payload, set(allowed)))
        if payload["task"] == "write_section":
            # Sections written from the same steps share their evidence as a prefix;
            # the length budget ends the task with the instructions that enforce it.
            assert list(payload)[-5:] == ["findings", "evidence", "section", "length", "instructions"]
            return "**判断**。事实。[[E001]] 补充。[[E003]]", {"repairs": 0}
        if payload["task"] == "write_summary":
            return "核心结论。[[E001]]", {"repairs": 0}
        return "# 修订后的报告\n\n## 执行摘要\n\n改写。[[E001]]", {"repairs": 1}

    monkeypatch.setattr(runner, "_json", outline)
    monkeypatch.setattr(runner, "_markdown", markdown)
    draft = await runner.write_report(run, plan, results, findings, pool, ["raw limitation"])
    task, payload = outlines[0]
    assert task == "report-outline"
    assert payload["user_updates"] == ["加上运维成本"] and payload["assumptions"] == ["团队规模未知"]
    assert payload["raw_limitations"] == ["raw limitation"] and payload["unit_summaries"] == {"R1": "单元摘要"}
    assert payload["findings"][0]["single_source"] is True
    task, section, allowed = drafts[0]
    assert task == "report-section-1" and section["section"]["heading"] == "并发写入"
    # Search result links are discovery only; the supplement's finding belongs to R1.
    assert set(section["evidence"]) == {"E001", "E003"} == allowed
    assert drafts[1][0] == "report-summary" and drafts[1][2] == {"E001", "E003"}
    assert draft["title"] == "数据库选型研究" and draft["limitations"] == ["公开基准有限"]
    assert [item["text"] for item in documents.toc(draft["document"])] == ["执行摘要", "并发写入", "研究范围与局限"]
    assert "团队规模未知" in draft["document"]
    # A retry reuses the outline and every completed section.
    await runner.write_report(run, plan, results, findings, pool, ["raw limitation"])
    assert len(outlines) == 1 and len(drafts) == 2
    revised = await runner.revise_report(run, plan, draft, "缩短第一章", findings, pool)
    task, request, allowed = drafts[-1]
    assert task == "report-revision" and request["previous_report"] == draft["document"] and allowed == {"E001", "E003"}
    assert request["user_request"] == "缩短第一章"
    # Findings and evidence lead a revision; the report, then the request, end it:
    # a second revision request reuses the first one's prompt up to its own words.
    order = list(request)
    assert order.index("findings") < order.index("evidence") < order.index("previous_report") < order.index("user_request") < order.index("instructions")
    assert revised["title"] == "修订后的报告" and revised["limitations"] == ["公开基准有限"]


def test_opaque_calls_do_not_require_external_date_schema(settings, plan):
    from datetime import datetime

    from deepresearch.contracts import ResearchResult
    from deepresearch.evidence import merge_results
    from deepresearch.observations import research_observations
    from deepresearch.validators import research_gaps

    plan.research_units[0].source_strategy.not_before = datetime(2026, 1, 1, tzinfo=UTC)
    messages = [{"type": "tool", "tool_call_id": f"c{i}", "name": source.tool, "status": "success", "content": "Unstructured dated research material"} for i, source in enumerate(settings.sources)]
    evidence, _ = research_observations(NativeExecution("notes", "x", messages), settings.sources)
    result = ResearchResult(unit_id="R1", raw_evidences=evidence, confidence=0.5, findings=[Finding(claim="A claim", raw_evidence_refs=[e.raw_id for e in evidence], confidence=0.5, high_risk=True)])
    pool, findings, _ = merge_results([result])
    units = [unit.model_dump() for unit in plan.research_units]
    assert research_gaps(plan, units, findings, pool) == []
    result.open_questions = ["Publication date could not be established"]
    gaps = research_gaps(plan, units, findings, pool, [result.model_dump()])
    assert gaps[0]["code"] == "open-questions"


@pytest.mark.asyncio
async def test_source_selection_reuses_host_cache_and_checks_server(settings, tmp_path, monkeypatch):
    import sys

    from deepresearch.config import SourceSpec

    # An older file binding a host MCP server (not one of DeepResearch's own servers).
    legacy = SourceSpec(name="internal-kb", server="your-internal-mcp", tool="your-internal-mcp_search", origin="internal")
    assert legacy.kind == "mcp"
    tool = types.SimpleNamespace(name=legacy.tool)
    monkeypatch.setitem(sys.modules, "deerflow.mcp.cache", types.SimpleNamespace(get_cached_mcp_tools=lambda: [tool]))
    monkeypatch.setitem(sys.modules, "deerflow.tools.mcp_metadata", types.SimpleNamespace(get_mcp_source=lambda value: {"server_name": legacy.server}))
    runner = DeerFlowRunner(settings, Store(tmp_path / "unused.sqlite"))
    async with runner._source_tools({"run_id": "r"}, [legacy]) as selected:
        assert selected[legacy.name] is tool
    wrong = legacy.model_copy(update={"server": "another-server"})
    with pytest.raises(ResearchError, match="unavailable"):
        async with runner._source_tools({"run_id": "r"}, [wrong]):
            pytest.fail("A tool from another server must never be selected")


def test_native_file_receipts_are_citable_without_claiming_mcp_origin(settings):
    from deepresearch.observations import research_observations

    execution = NativeExecution(
        "file finding",
        "x",
        [
            {
                "type": "tool",
                "name": "read_file",
                "tool_call_id": "file-call",
                "status": "success",
                "content": "local evidence",
            }
        ],
    )
    evidence, catalog = research_observations(execution, settings.sources)
    assert len(evidence) == 1
    assert evidence[0].origin == "runtime"
    assert evidence[0].source_uri.startswith("tool-result://")
    assert catalog[0]["tool_call_id"] == "file-call"


def test_a_step_stopped_by_the_engine_explains_itself_in_the_report_language():
    """A capped step is a disclosed limitation a reader can understand.

    Budget shares make the native token cap common, so the engine's English
    stop code must not land in a Chinese report as-is.
    """
    from deepresearch.runner import stop_limitation

    zh = stop_limitation("token_capped", "zh")
    assert "研究额度" in zh and "token_capped" not in zh
    assert "research budget" in stop_limitation("token_capped", "en")
    assert "something_new" in stop_limitation("something_new", "zh")


@pytest.mark.asyncio
async def test_dependencies_the_owner_set_in_an_edited_plan_survive_normalisation(settings, tmp_path, monkeypatch):
    # Found in a real run: the planner is told to avoid dependencies, so the model that
    # normalises an edited plan returned it without the depends_on the owner had added.
    from deepresearch.contracts import ResearchPlan, ResearchUnit

    store = Store(tmp_path / "runner.sqlite")
    await store.start()
    runner = DeerFlowRunner(settings, store)
    skill = next(iter(settings.researchers()))
    run = {"run_id": "r", "query": "q", "constraints": [], "source_names": [], "budget": {"max_units": 8}}

    def units(needs):
        return [ResearchUnit(id=uid, skill=skill, objective=f"step {uid}", depends_on=deps) for uid, deps in needs.items()]

    edited = ResearchPlan(goal="compare", research_units=units({"A": [], "B": [], "C": ["A"], "D": ["B"], "E": ["D"]})).model_dump(mode="json")

    async def normalised(run, name, payload, schema, **kwargs):
        # The model keeps A, B, C and E, drops D and every dependency.
        return ResearchPlan(goal="compare", research_units=units({"A": [], "B": [], "C": [], "E": []}))

    monkeypatch.setattr(runner, "_json", normalised)
    plan = await runner.plan(run, edited)
    # What the owner declared is back; a dependency on the removed step went with it.
    assert {unit.id: unit.depends_on for unit in plan.research_units} == {"A": [], "B": [], "C": ["A"], "E": []}
    # A revision in words is the model's to plan: nothing is forced onto it.
    revised = await runner.plan(run, {"plan": edited, "revision": "去掉依赖"})
    assert all(not unit.depends_on for unit in revised.research_units)
