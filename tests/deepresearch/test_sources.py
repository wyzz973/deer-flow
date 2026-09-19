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


def test_an_observed_link_never_produces_evidence_the_pool_rejects():
    """A page can link to a signed URL thousands of characters long.

    That link becomes the observed source's title when the page gives no label,
    so the title must stay inside the evidence contract instead of failing the
    whole unit later, in evidence_merge.
    """
    from deepresearch.contracts import RawEvidence

    signed = "https://example.com/image.png?jwt=" + "e" * 1800
    found = observed_sources(f"see {signed} for the chart")
    assert len(found) == 1 and found[0]["url"] == signed
    assert len(found[0]["title"]) <= 1000
    evidence = RawEvidence(
        raw_id="doc_observed",
        title=found[0]["title"],
        url=found[0]["url"],
        origin="external",
        source_name="external-web",
        publisher="web",
        snippet=found[0]["excerpt"],
        provenance="observed_source",
    )
    assert evidence.title.startswith("https://example.com/image.png")


def test_observed_evidence_from_a_real_page_always_survives_the_merge_contract():
    """The shape that failed a live run: a page linking to a signed image URL.

    Everything research_observations returns must pass the same validation
    evidence_merge performs, otherwise one link ends a completed unit.
    """
    from deepresearch.config import SourceSpec
    from deepresearch.contracts import ResearchResult
    from deepresearch.observations import NativeExecution, research_observations

    signed = "https://private-user-images.githubusercontent.com/25103655/406084851-8f05.png?jwt=" + "e" * 1500
    source = SourceSpec(name="public-web-read", tool="web_fetch", role="read", origin="external", providers=[{"id": "direct", "type": "direct"}])
    execution = NativeExecution(
        answer="notes",
        execution_id="exec",
        messages=[
            {"type": "ai", "tool_calls": [{"id": "f1", "name": "web_fetch", "args": {"url": "https://github.com/vllm-project/vllm"}}]},
            {"type": "tool", "name": "web_fetch", "tool_call_id": "f1", "content": f"vLLM benchmarks\n\n![chart]({signed})\n\nThroughput doubled."},
        ],
    )
    evidences, _catalog = research_observations(execution, [source])
    assert any(item.provenance == "observed_source" for item in evidences)
    result = ResearchResult.model_validate(
        {
            "unit_id": "u1",
            "confidence": 0.5,
            "raw_evidences": [item.model_dump(mode="json") for item in evidences],
            "findings": [],
        }
    )
    assert all(len(item.title) <= 1000 for item in result.raw_evidences)
