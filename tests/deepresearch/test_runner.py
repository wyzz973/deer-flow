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
    async def source_tools(run, sources):
        assert invoked == []  # no hardcoded search before the model runs
        yield {s.name: tool for s, tool in zip(sources, original_tools, strict=True)}

    async def agent_config(skill):
        return settings.skills[skill], types.SimpleNamespace(tools=None, disallowed_tools=[])

    async def native(settings, store, run, name, payload, tools, agent, context):
        assert tools == original_tools
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

    async def convert(runner, run, name, payload, schema, answer, context):
        assert answer == "Research notes [r1] [r2]"
        refs = [e["raw_id"] for e in payload["observed_calls"]]
        return ResearchAnalysis(findings=[Finding(claim="claim", raw_evidence_refs=refs, confidence=0.8)], confidence=0.8)

    monkeypatch.setattr(runner, "_source_tools", source_tools)
    monkeypatch.setattr(runner, "_agent_config", agent_config)
    monkeypatch.setattr("deepresearch.native.execute_role", native)
    monkeypatch.setattr("deepresearch.structured.convert_answer", convert)
    result = await runner.research(run, plan.research_units[0], {})
    assert len(invoked) == 2
    assert {e.origin for e in result.raw_evidences} == {"internal", "external"}
    assert all(e.provenance == "tool_output" for e in result.raw_evidences)
    await runner.research(run, plan.research_units[0], {})
    assert len(invoked) == 2  # conversion retry reuses the whole completed execution

    async def fabricated(*args):
        return ResearchAnalysis(findings=[Finding(claim="claim", raw_evidence_refs=["fabricated"], confidence=0.8)], confidence=0.8)

    monkeypatch.setattr("deepresearch.structured.convert_answer", fabricated)
    with pytest.raises(ResearchError, match="unobserved"):
        await runner.research(run, plan.research_units[0], {})


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

    tool = types.SimpleNamespace(name=settings.sources[0].tool)
    monkeypatch.setitem(sys.modules, "deerflow.mcp.cache", types.SimpleNamespace(get_cached_mcp_tools=lambda: [tool]))
    monkeypatch.setitem(sys.modules, "deerflow.tools.mcp_metadata", types.SimpleNamespace(get_mcp_source=lambda value: {"server_name": settings.sources[0].server}))
    runner = DeerFlowRunner(settings, Store(tmp_path / "unused.sqlite"))
    async with runner._source_tools({}, [settings.sources[0]]) as selected:
        assert selected[settings.sources[0].name] is tool
    wrong = settings.sources[0].model_copy(update={"server": "another-server"})
    with pytest.raises(ResearchError, match="unavailable"):
        async with runner._source_tools({}, [wrong]):
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
