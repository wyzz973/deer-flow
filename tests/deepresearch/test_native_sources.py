"""Native sources reuse exact host tools without requiring MCP discovery."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from deepresearch.config import Settings, SourceSpec
from deepresearch.contracts import ResearchError
from deepresearch.runner import DeerFlowRunner


def test_native_bindings_are_explicit_and_do_not_need_an_mcp_server(settings):
    source = SourceSpec(name="web", kind="native", tool="web_search", origin="external")
    configured = Settings.model_validate({**settings.model_dump(), "runner": "deerflow", "sources": [source], "require_dual_source": False, "source_fallback": []})
    assert configured.sources[0].server is None
    # Without a host binding a source is DeepResearch-owned and needs providers.
    with pytest.raises(ValidationError, match="at least one provider"):
        SourceSpec(name="web", tool="search", origin="external")
    with pytest.raises(ValidationError, match="server name"):
        SourceSpec(name="web", kind="mcp", tool="search", origin="external")
    with pytest.raises(ValidationError, match="do not select"):
        SourceSpec(name="web", kind="native", server="unknown", tool="web_search", origin="external")


@pytest.mark.asyncio
async def test_native_selection_preserves_tool_identity_and_does_not_discover_mcp(settings, monkeypatch):
    tool = SimpleNamespace(name="web_search", args_schema=object(), metadata={"original": True})
    source = SourceSpec(name="web", kind="native", tool="web_search", origin="external")

    def available(**kwargs):
        assert kwargs["include_mcp"] is False
        assert kwargs["include_upload_tool"] is False
        return [tool]

    def unexpected_mcp():
        raise AssertionError("Native-only research must not discover MCP tools")

    monkeypatch.setattr("deerflow.config.get_app_config", lambda: object())
    monkeypatch.setattr("deerflow.tools.get_available_tools", available)
    monkeypatch.setattr("deerflow.mcp.cache.get_cached_mcp_tools", unexpected_mcp)
    runner = DeerFlowRunner(settings, None)
    async with runner._source_tools({}, [source]) as selected:
        assert selected == {"web": tool}
        assert selected["web"] is tool
    settings.native_tools = ["web_fetch"]
    with pytest.raises(ResearchError, match="ceiling"):
        async with runner._source_tools({}, [source]):
            pytest.fail("Native source bypassed the tool ceiling")


@pytest.mark.asyncio
async def test_mcp_sources_still_require_exact_server_and_keep_original_tool(settings, monkeypatch):
    wrong_server = SimpleNamespace(name="search", server="wrong")
    original = SimpleNamespace(name="search", server="configured")
    monkeypatch.setattr("deerflow.mcp.cache.get_cached_mcp_tools", lambda: [wrong_server, original])
    monkeypatch.setattr("deerflow.tools.mcp_metadata.get_mcp_source", lambda tool: {"server_name": tool.server})
    source = SourceSpec(name="web", server="configured", tool="search", origin="external")
    async with DeerFlowRunner(settings, None)._source_tools({}, [source]) as selected:
        assert selected["web"] is original


def test_configured_native_evidence_does_not_claim_to_be_an_mcp_call():
    from deepresearch.observations import NativeExecution, research_observations

    source = SourceSpec(name="web", kind="native", tool="web_fetch", origin="external")
    execution = NativeExecution(answer="notes", execution_id="execution", messages=[{"type": "tool", "name": "web_fetch", "tool_call_id": "call", "content": "Original native result"}])
    evidence, _ = research_observations(execution, [source])
    assert len(evidence) == 1
    assert evidence[0].source_uri.startswith("tool-result://")
    assert evidence[0].origin == "external"
