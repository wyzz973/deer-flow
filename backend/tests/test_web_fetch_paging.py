"""The native fetch tool keeps page identity and makes truncation actionable."""

from types import SimpleNamespace

import pytest

from deerflow.community.jina_ai.jina_client import JinaClient
from deerflow.community.jina_ai.tools import web_fetch_tool


@pytest.mark.anyio
async def test_fetch_pages_and_section_lookup_keep_native_artifact(monkeypatch):
    text = "Overview\n" + "a" * 18000 + "\nTokenizers\n" + "Full original section. " * 100

    async def crawl(*args, **kwargs):
        return "<html>page</html>"

    monkeypatch.setattr(JinaClient, "crawl", crawl)
    monkeypatch.setattr("deerflow.community.jina_ai.tools.get_app_config", lambda: SimpleNamespace(get_tool_config=lambda _: None))
    monkeypatch.setattr("deerflow.community.jina_ai.tools.readability_extractor.extract_article", lambda _: SimpleNamespace(title="Page title", html_content="page", to_markdown=lambda: text))

    async def invoke(**kwargs):
        return await web_fetch_tool.ainvoke({"type": "tool_call", "id": "call", "name": "web_fetch", "args": {"url": "https://example.org/", **kwargs}})

    first = await invoke()
    assert first.artifact["next_start_index"] == 16000
    assert "start_index=16000" in first.content
    second = await invoke(start_index=16000)
    assert "Full original section" in second.content
    assert second.artifact["document_hash"] == first.artifact["document_hash"]
    located = await invoke(query="Tokenizers", max_length=2000)
    assert located.artifact["start_index"] > 16000
    assert "Tokenizers" in located.content and "Literal matches" in located.content


@pytest.mark.anyio
async def test_failed_fetch_has_native_error_status_and_no_read_artifact(monkeypatch):
    async def crawl(*args, **kwargs):
        return "Error: upstream unavailable"

    monkeypatch.setattr(JinaClient, "crawl", crawl)
    monkeypatch.setattr("deerflow.community.jina_ai.tools.get_app_config", lambda: SimpleNamespace(get_tool_config=lambda _: None))
    result = await web_fetch_tool.ainvoke({"type": "tool_call", "id": "call", "name": "web_fetch", "args": {"url": "https://example.org/"}})
    assert result.status == "error" and result.artifact is None
