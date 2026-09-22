"""Directly exposed MCP tools (``kind: mcp``) answer in whatever shape their server has.

A research that searched and read only through such tools ended with NO_EVIDENCE:
the page an MCP fetch tool opened was kept as an anonymous tool output, and the
converter, which matches the researcher's notes to pages by address, was shown an
entry without an address or a title. Evidence is derived after execution, from the
call's own arguments and its result, so the model still sees the tool unchanged.
"""

import json
import types
from contextlib import asynccontextmanager

import pytest

from deepresearch.config import SourceSpec
from deepresearch.contracts import Finding, ResearchUnit
from deepresearch.evidence import merge_results
from deepresearch.observations import NativeExecution, research_observations
from deepresearch.report import _page_identity
from deepresearch.report_policy import citable
from deepresearch.runner import DeerFlowRunner, ResearchAnalysis
from deepresearch.store import Store

SEARCH = SourceSpec(name="intranet-search", kind="mcp", server="web", tool="web_search", role="search", origin="external")
READ = SourceSpec(name="intranet-fetch", kind="mcp", server="web", tool="web_fetch", role="read", origin="external")
KNOWLEDGE = SourceSpec(name="kb", kind="mcp", server="kb", tool="kb_search", role="data", origin="internal")
ROLES = {source.name: source.role for source in (SEARCH, READ, KNOWLEDGE)}
PAGE = "https://qdrant.tech/blog/qdrant-1.12/"
BODY = "# Qdrant 1.12 发布说明\n\nQdrant 1.12 新增磁盘上的 payload 索引，详见 https://qdrant.tech/blog/ 与 https://github.com/qdrant/qdrant/releases 。" + "正文。" * 40
# ensure_ascii on purpose: many servers escape non-ASCII text.
ENVELOPE = json.dumps({"code": 0, "data": {"title": "Qdrant 1.12 发布说明", "content": BODY}})


def observe(tool, arguments, content, *sources, artifact=None, cite_search_results=False):
    messages = [
        {"type": "ai", "tool_calls": [{"id": "c1", "name": tool, "args": arguments}]},
        {"type": "tool", "name": tool, "tool_call_id": "c1", "content": content, "artifact": artifact, "status": "success"},
    ]
    evidence, catalog = research_observations(NativeExecution("notes", "exec", messages), list(sources), cite_search_results=cite_search_results)
    superseded = {item["raw_id"] for item in catalog if item.get("superseded")}
    eligible = {item.raw_id for item in evidence if item.raw_id not in superseded and citable(item, ROLES, cite_search_results)}
    return evidence, [item for item in catalog if item["raw_id"] in eligible]


def test_a_page_opened_through_an_mcp_tool_is_cited_as_that_page():
    evidence, offered = observe("web_fetch", {"url": PAGE}, BODY, SEARCH, READ)
    (page,) = offered
    # The converter finds the page the notes cite by its address and title.
    assert (page["url"], page["title"], page["provenance"]) == (PAGE, "Qdrant 1.12 发布说明", "fetched_document")
    document = next(item for item in evidence if item.raw_id == page["raw_id"])
    assert document.url == PAGE and document.source_name == "intranet-fetch" and document.snippet.startswith("# Qdrant 1.12")
    # Links the page mentions were seen, not read.
    assert {item.url for item in evidence if item.provenance == "observed_source"} == {"https://qdrant.tech/blog/", "https://github.com/qdrant/qdrant/releases"}


@pytest.mark.parametrize(
    ("arguments", "content", "artifact"),
    [
        # A JSON envelope with escaped text, the address under another argument name.
        ({"link": PAGE, "format": "markdown"}, ENVELOPE, None),
        # Structured content of a tool that returns an object.
        ({"uri": PAGE}, "ok", {"structured_content": {"result": {"name": "Qdrant 1.12 发布说明", "markdown": BODY, "sourceURL": "https://qdrant.tech/blog/qdrant-1.12"}}}),
        # What an MCP adapter makes of a tool returning a JSON string: the string again, wrapped as structured content (seen in a real run).
        ({"url": PAGE}, [{"type": "text", "text": ENVELOPE}], {"structured_content": {"result": ENVELOPE}}),
        # Content blocks, and only part of the page.
        ({"url": PAGE, "max_length": 300}, [{"type": "text", "text": "Title: Qdrant 1.12 发布说明\n\n" + BODY[:300]}], None),
    ],
)
def test_the_page_is_found_in_any_answer_shape(arguments, content, artifact):
    evidence, offered = observe("web_fetch", arguments, content, READ, artifact=artifact)
    (page,) = offered
    assert (page["url"], page["title"]) == (PAGE, "Qdrant 1.12 发布说明")
    document = next(item for item in evidence if item.raw_id == page["raw_id"])
    # What the writer quotes is the page's text, not its JSON wrapping.
    assert "payload 索引" in document.snippet and "\\u" not in document.snippet and '"code"' not in document.snippet


def test_an_answer_too_short_to_be_a_page_is_not_registered_as_one():
    _, offered = observe("web_fetch", {"url": PAGE}, "403 Forbidden", READ)
    assert [item.get("provenance") for item in offered] == [None]


def test_a_document_addressed_by_an_id_is_one_reference_however_often_it_is_read():
    reader = SourceSpec(name="kb-read", kind="mcp", server="kb", tool="get_document", role="read", origin="internal")
    text = json.dumps({"data": {"doc_name": "向量检索平台运维手册", "content": "平台在 2026 年 3 月完成从 Milvus 到 Qdrant 的迁移。" * 5}}, ensure_ascii=False)
    evidence, offered = observe("get_document", {"doc_id": "KB-42"}, text, reader)
    (document,) = offered
    assert (document["title"], document.get("url"), document["provenance"]) == ("向量检索平台运维手册", None, "fetched_document")
    found = next(item for item in evidence if item.raw_id == document["raw_id"])
    assert found.source_uri == "mcp://kb-read/KB-42" and citable(found, {"kb-read": "read"})
    again, _ = observe("get_document", {"doc_id": "KB-42"}, text, reader)
    first, second = (next(item for item in batch if item.provenance == "fetched_document").model_dump(mode="json") for batch in (evidence, again))
    assert _page_identity(first, "E001") == _page_identity(second, "E002")


def test_several_pages_opened_by_one_call_are_cited_one_by_one():
    other = "https://qdrant.tech/documentation/guides/quantization/"
    quantization = {"url": other, "title": "Quantization", "raw_content": "Scalar quantization reduces memory use by a factor of four. " * 4}
    content = json.dumps({"results": [{"url": PAGE, "title": "Qdrant 1.12", "raw_content": BODY}, quantization], "failed_results": []})
    evidence, offered = observe("web_fetch", {"urls": [PAGE, other]}, content, READ)
    assert {(item["url"], item["title"], item["provenance"]) for item in offered} == {(PAGE, "Qdrant 1.12", "fetched_document"), (other, "Quantization", "fetched_document")}
    assert all("quantization" in item.snippet.lower() for item in evidence if item.url == other and item.provenance == "fetched_document")


def test_results_of_an_mcp_search_are_separate_evidence_where_results_are_the_evidence():
    # No tool opens pages: whatever the search returns is what can be cited.
    hits = [
        {"headline": "Qdrant 配额与 SLA", "desc": "共享集群的可用性目标为 99.9%，单租户上限 5 亿向量。", "link": "https://wiki.corp.example/qdrant-sla"},
        {"headline": "扩容流程", "desc": "扩容需要提前两周提交容量评审。", "link": "https://wiki.corp.example/qdrant-scale"},
    ]
    content = json.dumps({"code": 0, "data": {"hits": hits}})
    evidence, offered = observe("web_search", {"query": "qdrant sla"}, content, SEARCH, cite_search_results=True)
    records = [item for item in offered if item.get("record")]
    assert [(item["title"], item["url"]) for item in records] == [("Qdrant 配额与 SLA", "https://wiki.corp.example/qdrant-sla"), ("扩容流程", "https://wiki.corp.example/qdrant-scale")]
    assert next(item for item in evidence if item.raw_id == records[0]["raw_id"]).snippet == "共享集群的可用性目标为 99.9%，单租户上限 5 亿向量。"
    # The adapter's wrapping of a string answer changes nothing.
    _, wrapped = observe("web_search", {"query": "qdrant sla"}, content, SEARCH, artifact={"structured_content": {"result": content}}, cite_search_results=True)
    assert [item["title"] for item in wrapped if item.get("record")] == ["Qdrant 配额与 SLA", "扩容流程"]
    # Records that carry the whole answer are cited alone, like a research source's own results ...
    assert not any(item for item in offered if not item.get("record"))
    # ... while a shape read only in part keeps the whole answer citable, under what was asked.
    partly = json.dumps({"data": {"hits": hits, "notice": "另有 3 条结果因权限不足未展示；可联系平台组申请知识库只读权限后重新检索，或改用工单系统查询历史记录。" * 2}})
    _, partial = observe("web_search", {"query": "qdrant sla"}, partly, SEARCH, cite_search_results=True)
    assert len([item for item in partial if item.get("record")]) == 2 and any(item.get("title") == "intranet-search: qdrant sla" for item in partial if not item.get("record"))
    # With a tool that opens pages, results are discovery only, as before.
    _, discovery = observe("web_search", {"query": "qdrant sla"}, content, SEARCH, READ)
    assert discovery == []


def test_passages_of_an_mcp_knowledge_tool_without_titles_or_links_are_cited_one_by_one():
    chunks = [{"content": "压测结论：Qdrant 在 1 亿向量、过滤检索场景下 P99 为 38 毫秒。", "score": 0.9, "doc_id": "D-1"}, {"content": "成本评审：三副本部署的月成本约为 Milvus 方案的 70%。", "score": 0.8, "doc_id": "D-2"}]
    content = json.dumps({"chunks": chunks}, ensure_ascii=False)
    evidence, offered = observe("kb_search", {"q": "压测"}, content, KNOWLEDGE)
    records = [item for item in offered if item.get("record")]
    assert [item["title"] for item in records] == ["压测结论：Qdrant 在 1 亿向量、过滤检索场景下 P99 为 38 毫秒。", "成本评审：三副本部署的月成本约为 Milvus 方案的 70%。"]
    assert all(next(found for found in evidence if found.raw_id == item["raw_id"]).origin == "internal" for item in records)
    # Text with no record structure is still one citable answer.
    _, plain = observe("kb_search", {"q": "压测"}, "压测结论见平台周报，P99 为 38 毫秒。", KNOWLEDGE)
    assert [item["title"] for item in plain] == ["kb: 压测"]


def test_a_quote_is_checked_against_the_page_text_not_its_json_wrapping():
    from deepresearch.contracts import SourceAnnotation
    from deepresearch.observations import ground_source_annotations

    evidence, offered = observe("web_fetch", {"url": PAGE}, json.dumps({"data": {"title": "Qdrant 1.12", "content": BODY}}), READ)
    document = next(item for item in evidence if item.raw_id == offered[0]["raw_id"])
    ground_source_annotations(evidence, [SourceAnnotation(raw_id=document.raw_id, title="", quote="Qdrant 1.12 新增磁盘上的 payload 索引")])
    assert document.snippet == "Qdrant 1.12 新增磁盘上的 payload 索引"


@pytest.mark.asyncio
async def test_a_research_that_only_has_mcp_search_and_fetch_tools_keeps_its_findings(settings, tmp_path, monkeypatch):
    store = Store(tmp_path / "runner.sqlite")
    await store.start()
    run = {"run_id": "r", "thread_id": "dr-r", "owner": "u", "source_names": [], "usage": {"tool_calls": 0, "model_tokens": 0}, "budget": {"max_tool_calls": 10, "max_model_tokens": 100000}}
    await store.create(run, "k", "h")
    settings.sources = [SEARCH, READ]
    settings.source_fallback = [SEARCH.name, READ.name]
    runner = DeerFlowRunner(settings, store)

    @asynccontextmanager
    async def source_tools(run, sources, budget=None):
        yield {source.name: types.SimpleNamespace(name=source.tool) for source in sources}

    async def agent_config(skill):
        return settings.skills[skill], types.SimpleNamespace(tools=None, disallowed_tools=[])

    async def native(settings, store, run, name, payload, tools, agent, context, **kwargs):
        messages = [
            {"type": "ai", "tool_calls": [{"id": "c1", "name": "web_search", "args": {"query": "qdrant 1.12"}}]},
            {"type": "tool", "tool_call_id": "c1", "name": "web_search", "content": json.dumps({"results": [{"title": "Qdrant 1.12", "link": PAGE, "desc": "release notes"}]}), "status": "success"},
            {"type": "ai", "tool_calls": [{"id": "c2", "name": "web_fetch", "args": {"url": PAGE}}]},
            {"type": "tool", "tool_call_id": "c2", "name": "web_fetch", "content": BODY, "status": "success"},
        ]
        return NativeExecution(f"Qdrant 1.12 新增磁盘上的 payload 索引（来源：{PAGE}）", "native-exec", messages, [])

    async def convert(runner, run, name, payload, schema, answer, context, validator=None, repair=None, **kwargs):
        # What the conversion model can do: find the page its notes cite among the calls it is shown.
        cited = [call["raw_id"] for call in payload["observed_calls"] if call.get("url") and call["url"] in answer]
        analysis = ResearchAnalysis(findings=[Finding(claim="Qdrant 1.12 新增磁盘上的 payload 索引", raw_evidence_refs=cited, confidence=0.8)] if cited else [], confidence=0.8)
        validator(analysis)
        return analysis

    monkeypatch.setattr(runner, "_source_tools", source_tools)
    monkeypatch.setattr(runner, "_agent_config", agent_config)
    monkeypatch.setattr("deepresearch.native.execute_role", native)
    monkeypatch.setattr("deepresearch.structured.convert_answer", convert)
    unit = ResearchUnit(id="R1", skill="technical-route", objective="Qdrant 1.12 的变化", source_strategy={"required_origins": ["external"]})
    result = await runner.research(run, unit, {})
    (finding,) = result.findings
    (cited,) = [item for item in result.raw_evidences if item.raw_id in finding.raw_evidence_refs]
    assert (cited.url, cited.provenance, cited.title) == (PAGE, "fetched_document", "Qdrant 1.12 发布说明")
    # The report can cite it: the pool keeps its address.
    pool, findings, _ = merge_results([result])
    assert pool[findings[0]["evidence_ids"][0]]["url"] == PAGE


def test_a_page_says_when_it_was_published_and_the_date_is_kept():
    """A read page's own date is evidence metadata, like a search record's.

    A reader judging whether a claim is still true needs the page's date, and
    the reference list renders it. Only the server states it, so it is read
    where the server puts it — beside the text or under ``metadata`` — and an
    unusable value costs the date, never the page.
    """
    envelope = json.dumps({"data": {"title": "Qdrant 1.12 发布说明", "content": BODY, "metadata": {"publishedDate": "Oct 8, 2026"}}})
    evidence, offered = observe("web_fetch", {"url": PAGE}, envelope, READ)
    (page,) = offered
    document = next(item for item in evidence if item.raw_id == page["raw_id"])
    assert str(document.published_at)[:10] == "2026-10-08"
    # A date nobody can parse leaves the page citable and simply undated.
    unusable = json.dumps({"data": {"title": "Qdrant 1.12 发布说明", "content": BODY, "published_at": "上周"}})
    evidence, offered = observe("web_fetch", {"url": PAGE}, unusable, READ)
    assert len(offered) == 1
    assert next(item for item in evidence if item.raw_id == offered[0]["raw_id"]).published_at is None


def test_the_catalogue_offered_to_conversion_stays_json():
    """The catalogue is a payload, not an object graph.

    Conversion sends it to the model with ``json.dumps``, so a value that only
    Python understands fails the whole step: a real run died with TypeError in
    the converter the first time a page carried a parsed date.
    """
    envelope = json.dumps({"data": {"title": "Qdrant 1.12 发布说明", "content": BODY, "published_at": "2026-10-08"}})
    _, catalog = research_observations(
        NativeExecution(
            "notes",
            "exec",
            [
                {"type": "ai", "tool_calls": [{"id": "c1", "name": "web_fetch", "args": {"url": PAGE}}]},
                {"type": "tool", "name": "web_fetch", "tool_call_id": "c1", "content": envelope, "status": "success"},
            ],
        ),
        [READ],
    )
    assert json.dumps({"task": {}, "answer": "notes", "observed_calls": catalog}, ensure_ascii=False)
    assert "2026-10-08" in json.dumps(catalog)
