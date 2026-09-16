"""Sources are observed beside native tool execution, never response adapters."""

import pytest

from deepresearch.sources import observed_sources
from deepresearch.store import Store


def test_arbitrary_payload_links_are_grouped_without_a_result_schema():
    payload = {"unrelated": ["See [SQLite](https://www.sqlite.org/whentouse.html)", {"whatever": "https://www.postgresql.org/docs/"}]}
    sources = observed_sources(payload, connector="search", origin="external")
    assert {s["domain"] for s in sources} == {"sqlite.org", "postgresql.org"}
    assert all(s["status"] == "discovered" for s in sources)
    assert sources[0]["title"] == "SQLite"


def test_unknown_text_and_unsafe_links_do_not_become_fake_sources():
    assert observed_sources("An answer without URLs") == []
    assert observed_sources("https://user:password@example.org/doc") == []
    assert observed_sources("https://example.org/doc?token=[redacted]") == []


@pytest.mark.asyncio
async def test_calls_and_sources_have_different_counts_and_durable_links(tmp_path):
    store = Store(tmp_path / "sources.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "key", "hash")
    sources = observed_sources("https://sqlite.org/ https://postgresql.org/", connector="web")
    await store.record_call("r", "c1", {"tool_name": "search", "status": "success"}, sources)
    await store.record_call("r", "c2", {"tool_name": "read", "status": "success"}, sources[:1])
    assert len(await store.calls("r")) == 2
    stored = await store.sources("r")
    assert len(stored) == 2
    assert stored[0]["call_ids"] == ["c1", "c2"]
    await store.record_call("r", "c1", {"status": "success"}, sources)
    assert len(await store.calls("r")) == 2
    assert (await store.sources("r"))[0]["call_ids"] == ["c1", "c2"]
