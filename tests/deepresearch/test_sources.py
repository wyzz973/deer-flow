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


def test_a_supplied_publication_date_survives_into_evidence_and_the_reference_list():
    """A source that says when its page was published is the only date we have.

    The writer's evidence catalogue and the reference list were both built to
    show it, but the record loop nulled it out, so across 34,053 recorded
    evidence rows not one carried a date and a reader could not tell a 2019 page
    from last week's.
    """
    import json

    from deepresearch.config import SourceSpec
    from deepresearch.evidence import merge_results, valid_result
    from deepresearch.observations import NativeExecution, research_observations
    from deepresearch.report import bind, references

    source = SourceSpec(name="kb", kind="mcp", server="kb", tool="kb_search", role="data", origin="internal")
    answer = {
        "results": [
            {"title": "平台 SLA", "url": "https://wiki.example/sla", "snippet": "可用性 99.9%。", "published_at": "2026-07-10"},
            # A date nobody can parse must cost the date, never the evidence.
            {"title": "接入指引", "url": "https://wiki.example/on", "snippet": "配额 3000 万向量。", "published_at": "3 天前"},
        ]
    }
    messages = [
        {"type": "ai", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "配额"}}]},
        {"type": "tool", "name": "kb_search", "tool_call_id": "c1", "status": "success", "content": json.dumps(answer, ensure_ascii=False)},
    ]
    evidence, _ = research_observations(NativeExecution("notes", "exec", messages), [source])
    dated = {item.url: item.published_at for item in evidence if item.url}
    assert str(dated["https://wiki.example/sla"])[:10] == "2026-07-10"
    assert dated["https://wiki.example/on"] is None and len(dated) == 2

    result = valid_result({"unit_id": "R1", "findings": [], "raw_evidences": [item.model_dump(mode="json") for item in evidence], "confidence": 0.5})[0]
    pool, _, _ = merge_results([result])
    cited = next(eid for eid, item in pool.items() if item["url"] == "https://wiki.example/sla")
    mapping = bind(f"结论[[{cited}]]", pool)
    assert str(references(mapping, pool)[0]["published_at"])[:10] == "2026-07-10"


def test_dates_are_read_in_the_shapes_search_providers_actually_send():
    from deepresearch.extract import published

    for value, expected in (
        ("2026-07-10", "2026-07-10"),
        ("2026-07-10T08:30:00Z", "2026-07-10"),
        ("2026-04-21T00:00:00", "2026-04-21"),  # Brave page_age
        ("Apr 21, 2026", "2026-04-21"),  # Serper date
        ("21 Apr 2026", "2026-04-21"),
        ("July 10, 2026", "2026-07-10"),
        ("2026/07/10", "2026-07-10"),
        ("2026年7月10日", "2026-07-10"),
        (1752105600, "2025-07-10"),  # epoch seconds
    ):
        assert str(published(value))[:10] == expected, value
    # Never guess, and never raise: an unusable value is simply no date.
    for value in ("", None, "3 天前", "2 days ago", "recently", "not a date", {}, [], "0000-00-00", "2199-01-01", "1970-01-01"):
        assert published(value) is None, value


def test_a_web_page_states_its_date_the_way_the_open_web_does():
    """HTML pages and the readers in front of them put the date in known places.

    A page read with `direct` arrives as HTML: its date is in the metadata every
    CMS writes, not in the prose. Jina Reader hands back its own field name.
    Reading both is what makes dates show up for open-web research, not only for
    a self-hosted server that was told what to send.
    """
    from deepresearch import extract

    html = '<html><head><title>Qdrant 1.12</title><meta property="article:published_time" content="2026-05-04T09:00:00Z"></head><body>正文。</body></html>'
    assert extract.page_date(html) == "2026-05-04T09:00:00Z"
    assert extract.page_date('<meta name="date" content="2026-05-04">') == "2026-05-04"
    assert extract.page_date('<time datetime="2026-05-04">5 月 4 日</time>') == "2026-05-04"
    # itemprop and JSON-LD are the other two shapes worth reading.
    assert extract.page_date('<meta itemprop="datePublished" content="2026-05-04"/>') == "2026-05-04"
    assert extract.page_date('<script type="application/ld+json">{"@type":"Article","datePublished":"2026-05-04"}</script>') == "2026-05-04"
    # Nothing to read is not a failure, and prose is never a date.
    assert extract.page_date("<html><body>更新于上周。</body></html>") is None
    assert extract.page_date("") is None
    # Jina Reader's own field name, in the envelope shape it returns.
    jina = {"code": 200, "data": {"title": "SLA", "url": "https://x.example/sla", "content": "正文。", "publishedTime": "2026-05-04T09:00:00Z"}}
    assert extract.document(jina)["published_at"] == "2026-05-04T09:00:00Z"


def test_a_source_that_states_its_own_logo_has_it_read_as_an_icon_not_as_a_link():
    """A logo is decoration, and a source may only speak for its own site.

    Before this it was scanned as an ordinary link: a picture became a "seen
    source" row and its long URL pushed the records' share of the answer below
    the threshold, so the whole answer turned into an extra reference with no
    link. Now it is read as the site's icon, counts as part of the record, and
    an icon claimed for somebody else's host is ignored.
    """
    import json

    from deepresearch import extract
    from deepresearch.config import SourceSpec
    from deepresearch.observations import NativeExecution, research_observations

    logo = "https://wiki.corp.example/static/logo/platform-256.png"
    answer = {
        "results": [
            {"title": "平台 SLA", "url": "https://wiki.corp.example/sla", "summary": "可用性 99.9%。", "publish_date": "2026-07-10", "logo_url": logo, "site_name": "平台 Wiki"},
            # An icon on another host is not this source's to declare.
            {"title": "接入指引", "url": "https://wiki.corp.example/on", "summary": "配额 3000 万向量。", "logo_url": "https://cdn.other.example/logo.png"},
        ]
    }
    records = extract.records(answer, limit=50, text_limit=4000, from_text=False)
    assert [record.get("icon_url") for record in records] == [logo, None]
    # The icon belongs to the record, so it no longer counts against its share.
    without = [{key: value for key, value in record.items() if key != "icon_url"} for record in records]
    assert extract.coverage(answer, records) > extract.coverage(answer, without)
    # A payload of the recommended shape plus its own logo stays above the
    # threshold, so the whole answer is not cited next to the records.
    clean = {"results": [{key: value for key, value in item.items() if key != "site_name"} for item in answer["results"][:1]]}
    assert extract.coverage(clean, extract.records(clean, limit=50, text_limit=4000, from_text=False)) >= 0.8

    source = SourceSpec(name="kb", kind="mcp", server="kb", tool="kb_search", role="data", origin="internal")
    messages = [
        {"type": "ai", "tool_calls": [{"id": "c1", "name": "kb_search", "args": {"query": "SLA"}}]},
        {"type": "tool", "name": "kb_search", "tool_call_id": "c1", "status": "success", "content": json.dumps(answer, ensure_ascii=False)},
    ]
    evidence, catalog = research_observations(NativeExecution("notes", "exec", messages), [source])
    assert not [item for item in evidence if item.url == logo], "a logo is not a source that was seen"
    assert not [item for item in evidence if (item.snippet or "").find(logo) >= 0 and item.provenance == "observed_source"]


def test_an_icon_is_read_from_whatever_shape_the_answer_has_and_only_for_its_own_site():
    """The run records its sources from the tool body alone, not from records.

    So the icon has to be found the same way: by looking for an object that
    names both an address and an icon on the same host, in any envelope.
    """
    from deepresearch import extract

    answer = {
        "data": {
            "hits": [
                {"headline": "SLA", "link": "https://wiki.corp.example/sla", "logo_url": "https://wiki.corp.example/logo.png"},
                {"headline": "别人家的", "link": "https://other.example/a", "icon": "https://cdn.attacker.example/track.png"},
            ]
        }
    }
    assert extract.declared_icons(answer) == {"wiki.corp.example": "https://wiki.corp.example/logo.png"}
    # The same answer as the escaped JSON string a server actually sends.
    import json

    assert extract.declared_icons(json.dumps(answer, ensure_ascii=True)) == {"wiki.corp.example": "https://wiki.corp.example/logo.png"}
    assert extract.declared_icons("plain text, no icons") == {}


def test_icons_survive_the_wrappers_an_mcp_adapter_puts_around_a_server_answer():
    """Shapes taken from a real run, where the icon was found nowhere.

    The adapter hands back the server's JSON as a *string* inside
    ``structured_content``, and content blocks carry it as text. Walking only
    the outer object found no address and no icon, so the logo stayed an
    ordinary link: recorded as a site that was seen, and never shown.
    """
    import json

    from deepresearch import extract

    server = json.dumps(
        {
            "code": 0,
            "data": {
                "hits": [
                    {"headline": "向量检索接入指引", "link": "https://wiki.corp.example/wiki-vector-onboarding", "desc": "新业务接入…", "published_at": "2026-07-10", "logo_url": "https://wiki.corp.example/static/logo/platform-team.png"},
                ]
            },
        },
        ensure_ascii=True,
    )
    expected = {"wiki.corp.example": "https://wiki.corp.example/static/logo/platform-team.png"}
    assert extract.declared_icons({"structured_content": {"result": server}}) == expected
    assert extract.declared_icons([{"type": "text", "text": server}]) == expected
    assert extract.declared_icons(json.dumps({"artifact": {"body": {"structured_content": {"result": server}}}})) == expected
