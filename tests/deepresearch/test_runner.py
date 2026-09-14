import asyncio
import sys
import types
from contextlib import asynccontextmanager

import pytest

from deepresearch.contracts import Finding, ResearchError
from deepresearch.runner import DeerFlowRunner, ResearchAnalysis
from deepresearch.store import Store


@pytest.mark.asyncio
async def test_real_adapter_parallel_sources_trusted_metadata_and_success_cache(settings, plan, tmp_path, monkeypatch):
    # Mock transport/LLM only. The production research() implementation executes unchanged.
    class Wrapped:
        @classmethod
        def from_function(cls, **kwargs): return kwargs
    monkeypatch.setitem(sys.modules, "langchain_core.tools", types.SimpleNamespace(StructuredTool=Wrapped))
    store = Store(tmp_path / "runner.sqlite")
    await store.start()
    run = {"run_id": "r", "owner": "u", "source_names": [], "usage": {"tool_calls": 0, "model_tokens": 0}, "budget": {"max_tool_calls": 10, "max_model_tokens": 100000}}
    await store.create(run, "k", "h")
    runner = DeerFlowRunner(settings, store)
    calls, entered = [], asyncio.Event()
    class Tool:
        def __init__(self, source): self.source = source
        async def ainvoke(self, args):
            calls.append(self.source.origin)
            if len(calls) == 2: entered.set()
            await asyncio.wait_for(entered.wait(), 1)
            return {"results": [{"title": "Source", "url": "https://example.com" if self.source.origin == "external" else None, "document_id": "doc1", "snippet": "registered evidence", "origin": "fabricated"}]}
    @asynccontextmanager
    async def source_tools(run, sources): yield {s.name: Tool(s) for s in sources}
    async def agent_config(skill): return settings.skills[skill], types.SimpleNamespace(tools=None, disallowed_tools=[])
    async def model(run, name, payload, schema, tools=None, agent=None):
        refs = [e["raw_id"] for e in payload["registered_raw_evidences"]]
        return ResearchAnalysis(findings=[Finding(claim="claim", raw_evidence_refs=refs, confidence=.8)], confidence=.8)
    monkeypatch.setattr(runner, "_source_tools", source_tools)
    monkeypatch.setattr(runner, "_agent_config", agent_config)
    monkeypatch.setattr(runner, "_json", model)
    result = await runner.research(run, plan.research_units[0], {})
    assert set(calls) == {"internal", "external"}
    assert {e.origin for e in result.raw_evidences} == {"internal", "external"}
    assert len(result.findings[0].raw_evidence_refs) == 2
    await runner.research(run, plan.research_units[0], {})
    assert len(calls) == 2  # remote success cached, even if model/unit checkpoint failed later
    assert (await store.get("r"))["usage"]["tool_calls"] == 2
    async def bad_model(*args, **kwargs): return ResearchAnalysis(findings=[Finding(claim="claim", raw_evidence_refs=["fabricated"], confidence=.8)], confidence=.8)
    monkeypatch.setattr(runner, "_json", bad_model)
    with pytest.raises(ResearchError, match="未由工具注册"):
        await runner.research(run, plan.research_units[0], {})
