"""Source scope, citable evidence tiers, page-level citations and report export."""

from io import BytesIO

import pytest
from docx import Document

from deepresearch import report as documents
from deepresearch.config import SourceSpec
from deepresearch.contracts import ComparisonTable, SourcePolicy, StructuredReport
from deepresearch.observations import NativeExecution, research_observations
from deepresearch.output import parse_contract
from deepresearch.report_policy import citable, eligible_evidence, source_allowed, source_roles
from deepresearch.sources import fetched_source


def page(eid, url="https://docs.example.org/guide", chunk="First excerpt"):
    return {
        "evidence_id": eid,
        "url": url,
        "canonical_url": url,
        "source_uri": None,
        "source_name": "web",
        "origin": "external",
        "title": "Official guide",
        "snippet": chunk,
        "provenance": "fetched_document",
        "document_hash": "snapshot",
        "source_level": "L2",
    }


def test_native_fetch_metadata_registers_the_read_page_not_incidental_links():
    artifact = {"schema": "deerflow.web_page.v1", "url": "https://docs.example.org/guide", "title": "Official guide", "document_hash": "snapshot", "excerpt": "Original text"}
    execution = NativeExecution(
        "notes", "execution", [{"type": "tool", "name": "web_fetch", "tool_call_id": "c", "content": "Source: https://docs.example.org/guide\nOriginal text; incidental https://other.example.org/", "artifact": artifact}]
    )
    source = SourceSpec(name="web", kind="native", tool="web_fetch", origin="external", role="read")
    evidence, catalog = research_observations(execution, [source])
    read = [e for e in evidence if e.provenance == "fetched_document"]
    assert len(read) == 1 and read[0].url == artifact["url"]
    assert read[0].title == "Official guide" and read[0].document_hash == "snapshot"
    assert any(e.provenance == "observed_source" and e.url == "https://other.example.org/" for e in evidence)
    # The anonymous tool envelope is superseded by the page it registered.
    assert [item["superseded"] for item in catalog if item["raw_id"].startswith("raw_")] == [True]
    assert fetched_source({"arbitrary_mcp_shape": True}) is None


def test_source_scope_rejects_lookalikes_discovery_and_excluded_forums():
    policy = SourcePolicy(allowed_domains=["example.org"], require_original=True, excluded_url_prefixes=["https://example.org/forum"])
    assert source_allowed(page("E001"), policy)
    assert not source_allowed(page("E001", "https://example.org.evil.test/guide"), policy)
    assert not source_allowed(page("E001", "https://www.example.org/forum/topic"), policy)
    assert not source_allowed({**page("E001"), "provenance": "observed_source"}, policy)
    assert source_allowed({"provenance": "tool_output"}, {})
    with pytest.raises(ValueError):
        SourcePolicy(allowed_domains=["https://example.org"])


def test_search_results_are_discovery_while_read_pages_and_data_records_are_citable(settings):
    settings.sources = [
        SourceSpec(name="web-search", kind="native", tool="web_search", origin="external", role="search"),
        SourceSpec(name="web-read", kind="native", tool="web_fetch", origin="external", role="read"),
        SourceSpec(name="kb", server="kb", tool="kb_search", origin="internal", role="data"),
    ]
    roles = source_roles(settings)
    search_body = {"provenance": "tool_output", "source_name": "web-search"}
    assert not citable(search_body, roles)
    assert not citable({"provenance": "observed_source", "source_name": "kb"}, roles)
    assert citable({"provenance": "fetched_document", "source_name": "web-read"}, roles)
    assert citable({"provenance": "tool_output", "source_name": "kb"}, roles)
    # Undeclared runtime tools (file reads, shell, raw browser text) are working material.
    assert not citable({"provenance": "tool_output", "source_name": "native-runtime-tool"}, roles)
    assert citable(search_body, roles, cite_search_results=True)
    assert citable({"provenance": "observed_source", "source_name": "kb"}, roles, cite_search_results=True)
    pool = {"E001": page("E001"), "E002": {**page("E002"), "provenance": "observed_source"}, "E003": {**search_body, "url": None}}
    assert eligible_evidence(pool, {}, settings) == {"E001"}
    assert eligible_evidence(pool, {"allowed_domains": ["unrelated.test"]}, settings) == set()


def test_citation_groups_keep_all_excerpt_ids_and_table_cells():
    pool = {
        "E001": page("E001", chunk="Source: https://docs.example.org/guide\nTitle: Official guide\nExcerpt: characters 0-20 of 20.\n\nFirst excerpt\n\n[End of extracted page.]"),
        "E002": page("E002", chunk="Second excerpt"),
        "E003": page("E003", "https://other.example.org/guide"),
    }
    body = "| 维度 | A | B |\n| --- | --- | --- |\n| Concurrency | One writer [[E001]] | Multiple writers [[E003]] |\n\nRelevant difference. [[E002]]"
    document = documents.assemble("Decision report", "Recommendation basis [[E001, E002]]", [("Tradeoffs", body)], lang="en")
    mapping = documents.bind(document, pool)
    assert mapping == {"E001": 1, "E002": 1, "E003": 2}
    values = documents.citations(mapping, pool)
    assert len(values) == 2 and values[0]["domain"] == "docs.example.org"
    assert values[0]["evidence_ids"] == ["E001", "E002"]
    assert values[0]["excerpts"][0]["text"] == "First excerpt"
    assert values[0]["excerpts"][1]["text"] == "Second excerpt"
    display = documents.display_markdown(document, mapping)
    assert "Recommendation basis [1](#citation-E001)" in display and "[1](#citation-E001)[1]" not in display
    exported = documents.export_markdown(document, mapping, pool, "en")
    assert "| Concurrency | One writer [1](#ref-1) | Multiple writers [2](#ref-2) |" in exported and "## References" in exported
    assert "<table>" in documents.html_document(document, mapping, pool, "en")
    doc = Document(BytesIO(documents.docx_document({"document": document, "citations": values, "citation_map": mapping, "demo": False})))
    assert doc.tables[0].rows[1].cells[1].text == "One writer [1]"
    # One page keeps one number across reads; each excerpt keeps its own snapshot.
    changed = {**pool, "E002": {**pool["E002"], "document_hash": "new-version"}}
    regrouped = documents.bind(document, changed)
    assert regrouped["E002"] == regrouped["E001"]
    assert [part["document_hash"] for part in documents.citations(regrouped, changed)[0]["excerpts"]] == ["snapshot", "new-version"]


def test_url_variants_of_one_page_share_a_number_and_titles_are_readable():
    pool = {
        "E001": {**page("E001", "https://docs.example.org/guide"), "title": "Untitled"},
        "E002": {**page("E002", "https://www.docs.example.org/guide/"), "title": "Official guide", "document_hash": "other-read"},
        "E003": {**page("E003", "https://docs.example.org/guide?lang=zh"), "title": "https://docs.example.org/guide?lang=zh"},
        "E004": {**page("E004", "https://raw.example.org/docs/setup.md"), "title": "Untitled"},
        "E005": {**page("E005", None), "source_uri": "mcp-result://exec/raw_1", "source_name": "kb", "title": "kb / r1"},
        "E006": {**page("E006", None), "source_uri": "mcp-result://exec/raw_2", "source_name": "kb", "title": "kb / r2"},
    }
    document = documents.assemble("Report", "A [[E001]] B [[E002]] C [[E003]] D [[E004]] E [[E005]] F [[E006]]", [("Details", "G [[E002]]")], lang="en")
    mapping = documents.bind(document, pool)
    assert mapping == {"E001": 1, "E002": 1, "E003": 2, "E004": 3, "E005": 4, "E006": 5}
    values = documents.citations(mapping, pool)
    assert [value["title"] for value in values] == ["Official guide", "docs.example.org/guide?lang=zh", "raw.example.org/docs/setup.md", "kb / r1", "kb / r2"]
    exported = documents.export_markdown(document, mapping, pool, "en")
    assert "Untitled" not in exported and "[Official guide]" in exported
    assert "Untitled" not in documents.html_document(document, mapping, pool, "en")


def test_schema_repair_reports_the_actual_table_field_without_echoing_values():
    import json

    def segment(text, refs):
        return {"text": text, "evidence_ids": refs, "segment_type": "fact"}

    value = {
        "title": "Decision report",
        "executive_summary": [segment("PRIVATE-CONTENT-NOT-FEEDBACK", ["E001"])],
        "comparison_table": {"headers": ["Dimension", "A", "B"], "rows": [{"label": "Concurrency", "cells": [segment("One writer", ["E001"]), segment("Multiple writers", ["E003"])]}]},
        "sections": [{"heading": "Tradeoffs", "unit_ids": ["R1"], "segments": [segment("Relevant difference", ["E002"])]}],
        "conclusion": [segment("Conditions that change the choice", ["E003"])],
    }
    with pytest.raises(ValueError) as caught:
        parse_contract(json.dumps(value), StructuredReport)
    assert "comparison_table" in str(caught.value)
    assert "only alternative names" in str(caught.value)
    assert "PRIVATE-CONTENT" not in str(caught.value)
    with pytest.raises(ValueError, match="header count"):
        ComparisonTable.model_validate({"headers": ["A", "B", "C"], "rows": [{"label": "x", "cells": [segment("y", [])] * 2}]})


def test_technical_placeholder_is_not_a_reference_error_and_is_escaped():
    pool = {"E001": page("E001")}
    document = documents.assemble("Report", "The <name>_docsize table; <script>literal example</script> [[E001]]", [("Details", "Use `a[1]` indexing. [[E001]]")], lang="en")
    assert not documents.validate(document, pool, set(pool))
    mapping = documents.bind(document, pool)
    html = documents.html_document(document, mapping, pool, "en")
    assert "<script>" not in html and "&lt;name&gt;" in html
    assert "<code>a[1]</code>" in html


def test_an_externalized_page_read_back_from_its_file_is_cited_as_that_page():
    from deepresearch import report as documents

    artifact = {"schema": "deerflow.web_page.v1", "url": "https://docs.gitlab.com/duo", "title": "GitLab Duo", "document_hash": "page-hash", "excerpt": "head"}
    pointer = (
        "[Full web_fetch output saved to /mnt/user-data/outputs/.tool-results/web_fetch-45ed6114851c.log (15352 chars, ~3838 tokens).]\n[Preview kind: text.]\n\n"
        "Raw sample (head + tail):\nSource: https://docs.gitlab.com/duo\nTitle: GitLab Duo\n\n# GitLab Duo\n\nHead text"
    )
    full = "Source: https://docs.gitlab.com/duo\nTitle: GitLab Duo\nExcerpt: characters 0-15000 of 15352.\n\n# GitLab Duo\n\nDuo Agent Platform is generally available for self-managed instances."
    transforms = {"deerflow_tool_transforms": [{"kind": "externalized", "by": "ToolOutputBudgetMiddleware", "version": "1"}]}
    execution = NativeExecution(
        "notes",
        "execution",
        [
            {"type": "ai", "tool_calls": [{"id": "fetch", "name": "web_fetch", "args": {"url": "https://docs.gitlab.com/duo"}}]},
            {"type": "tool", "name": "web_fetch", "tool_call_id": "fetch", "content": pointer, "artifact": artifact, "additional_kwargs": transforms},
            {"type": "ai", "tool_calls": [{"id": "read", "name": "read_file", "args": {"path": "/mnt/user-data/outputs/.tool-results/web_fetch-45ed6114851c.log"}}]},
            {"type": "tool", "name": "read_file", "tool_call_id": "read", "content": full},
            {"type": "ai", "tool_calls": [{"id": "skill", "name": "read_file", "args": {"path": "/mnt/skills/custom/technical-route/SKILL.md"}}]},
            {"type": "tool", "name": "read_file", "tool_call_id": "skill", "content": "Skill methodology"},
        ],
    )
    source = SourceSpec(name="web-read", kind="native", tool="web_fetch", origin="external", role="read")
    evidence, catalog = research_observations(execution, [source])
    pages = [e for e in evidence if e.provenance == "fetched_document"]
    assert {e.url for e in pages} == {"https://docs.gitlab.com/duo"} and len(pages) == 2
    continuation = next(e for e in pages if "generally available" in e.snippet)
    assert continuation.document_hash == "page-hash" and continuation.source_name == "web-read"
    # The file-read envelope stays auditable but is superseded by the page.
    read_envelope = next(item for item in catalog if item["tool_call_id"] == "read" and item["raw_id"].startswith("raw_"))
    assert read_envelope["superseded"] is True
    # An unrelated file read remains an ordinary runtime record.
    skill = next(e for e in evidence if "Skill methodology" in e.snippet)
    assert skill.provenance == "tool_output" and skill.origin == "runtime"
    # Readers see page text, not the host synopsis envelope.
    assert documents.excerpt(pointer).startswith("# GitLab Duo")
    assert documents.excerpt(full).startswith("# GitLab Duo")


def test_browser_text_is_a_page_read_only_when_the_opened_url_is_certain():
    def turn(call_id, name, args):
        return {"type": "ai", "tool_calls": [{"id": call_id, "name": name, "args": args}]}

    def result(call_id, name, content):
        return {"type": "tool", "name": name, "tool_call_id": call_id, "content": content}

    execution = NativeExecution(
        "notes",
        "execution",
        [
            turn("nav", "browser_navigate", {"url": "https://about.gitlab.com/pricing/"}),
            result("nav", "browser_navigate", "Navigated to https://about.gitlab.com/pricing/."),
            turn("text", "browser_get_text", {}),
            result("text", "browser_get_text", "Premium $29 per user/month"),
            turn("click", "browser_click", {"ref": 4}),
            result("click", "browser_click", "Clicked"),
            turn("after-click", "browser_get_text", {}),
            result("after-click", "browser_get_text", "Some other page"),
            turn("nav2", "browser_navigate", {"url": "javascript:alert(1)"}),
            result("nav2", "browser_navigate", "Error"),
            turn("bad", "browser_get_text", {}),
            result("bad", "browser_get_text", "Unknown page"),
        ],
    )
    evidence, catalog = research_observations(execution, [])
    pages = [e for e in evidence if e.provenance == "fetched_document"]
    assert [(e.url, e.origin) for e in pages] == [("https://about.gitlab.com/pricing/", "external")]
    assert pages[0].snippet == "Premium $29 per user/month"
    # After a click the page is uncertain; its text stays an uncitable runtime record.
    later = [e for e in evidence if "Some other page" in e.snippet or "Unknown page" in e.snippet]
    assert later and all(e.provenance == "tool_output" and e.origin == "runtime" for e in later)
    assert any(item.get("superseded") for item in catalog if item["tool_call_id"] == "text")
