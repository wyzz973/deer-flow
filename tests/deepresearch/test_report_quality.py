"""Source scope, document-level citations and compact report rendering."""

from io import BytesIO

import pytest
from docx import Document

from deepresearch.config import SourceSpec
from deepresearch.contracts import ComparisonTable, ResearchPlan, SourcePolicy, StructuredReport
from deepresearch.observations import NativeExecution, research_observations
from deepresearch.output import parse_contract
from deepresearch.render import bind_citations, citation_metadata, docx_report, html_report, markdown
from deepresearch.report_policy import report_schema, source_allowed
from deepresearch.sources import fetched_source
from deepresearch.validators import validate_report


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


def report():
    def segment(text, refs):
        return {"text": text, "evidence_ids": refs, "segment_type": "fact"}

    return StructuredReport.model_validate(
        {
            "title": "Decision report",
            "executive_summary": [segment("Recommendation basis", ["E001", "E002"])],
            "comparison_table": {"headers": ["A", "B"], "rows": [{"label": "Concurrency", "cells": [segment("One writer", ["E001"]), segment("Multiple writers", ["E003"])]}]},
            "sections": [{"heading": "Tradeoffs", "unit_ids": ["R1"], "segments": [segment("Relevant difference", ["E002"])]}],
            "conclusion": [segment("Conditions that change the choice", ["E003"])],
        }
    )


def test_native_fetch_metadata_registers_the_read_page_not_incidental_links():
    artifact = {"schema": "deerflow.web_page.v1", "url": "https://docs.example.org/guide", "title": "Official guide", "document_hash": "snapshot", "excerpt": "Original text"}
    execution = NativeExecution(
        "notes", "execution", [{"type": "tool", "name": "web_fetch", "tool_call_id": "c", "content": "Source: https://docs.example.org/guide\nOriginal text; incidental https://other.example.org/", "artifact": artifact}]
    )
    source = SourceSpec(name="web", kind="native", tool="web_fetch", origin="external")
    evidence, _ = research_observations(execution, [source])
    read = [e for e in evidence if e.provenance == "fetched_document"]
    assert len(read) == 1 and read[0].url == artifact["url"]
    assert read[0].title == "Official guide" and read[0].document_hash == "snapshot"
    assert any(e.provenance == "observed_source" and e.url == "https://other.example.org/" for e in evidence)
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


def test_citation_groups_keep_all_excerpt_ids_and_table_cells():
    value = report()
    pool = {"E001": page("E001"), "E002": page("E002", chunk="Second excerpt"), "E003": page("E003", "https://other.example.org/guide")}
    mapping = bind_citations(value, pool)
    assert mapping == {"E001": 1, "E002": 1, "E003": 2}
    citations = citation_metadata(mapping, pool)
    assert len(citations) == 2
    assert citations[0]["evidence_ids"] == ["E001", "E002"]
    assert citations[0]["excerpts"][1]["text"] == "Second excerpt"
    md = markdown(value, mapping, pool)
    assert "| 维度 | A | B |" in md and "[1](#ref-1)[1]" not in md
    assert "<table>" in html_report(value, mapping, pool)
    exported = docx_report({"report": value.model_dump(), "citations": citations, "citation_map": mapping, "demo": False, "limitations": []})
    doc = Document(BytesIO(exported))
    assert doc.tables[0].rows[1].cells[1].text == "One writer[1]"
    changed = {**pool, "E002": {**pool["E002"], "document_hash": "new-version"}}
    assert bind_citations(value, changed)["E002"] != bind_citations(value, changed)["E001"]


def test_final_gate_checks_table_references_scope_and_brief_length(plan):
    value = report()
    plan = ResearchPlan.model_validate({**plan.model_dump(), "report_style": "brief", "source_policy": {"allowed_domains": ["example.org"], "require_original": True}})
    pool = {"E001": page("E001"), "E002": page("E002"), "E003": page("E003", "https://unrelated.test/")}
    assert any("来源范围" in error for error in validate_report(value, plan, pool))
    value.executive_summary[0].text = "x" * 6100
    assert any("6000" in error for error in validate_report(value, plan, pool))
    with pytest.raises(ValueError, match="header count"):
        ComparisonTable.model_validate({"headers": ["A", "B", "C"], "rows": [{"label": "x", "cells": [value.conclusion[0].model_dump()] * 2}]})


def test_schema_repair_reports_the_actual_table_field_without_echoing_values():
    import json

    value = report().model_dump()
    value["comparison_table"]["headers"] = ["Dimension", "A", "B"]
    value["executive_summary"][0]["text"] = "PRIVATE-CONTENT-NOT-FEEDBACK"
    with pytest.raises(ValueError) as caught:
        parse_contract(json.dumps(value), StructuredReport)
    assert "comparison_table" in str(caught.value)
    assert "only alternative names" in str(caught.value)
    assert "PRIVATE-CONTENT" not in str(caught.value)


def test_technical_placeholder_is_not_a_reference_error_and_is_escaped(plan):
    value = report()
    value.executive_summary[0].text = "The <name>_docsize table; <script>literal example</script>"
    pool = {"E001": page("E001"), "E002": page("E002"), "E003": page("E003")}
    assert not validate_report(value, plan, pool)
    exported = markdown(value, bind_citations(value, pool), pool)
    assert "<script>" not in exported and "&lt;name&gt;" in exported


def test_brief_generation_has_local_limits_without_breaking_historical_reports(plan):
    plan.report_style = "brief"
    schema = report_schema(plan)
    value = report().model_dump()
    assert schema.model_validate(value).comparison_table is not None
    value["executive_summary"][0]["text"] = "x" * 281
    with pytest.raises(ValueError, match="280"):
        schema.model_validate(value)
    # A historical report is still valid under the public storage/export AST.
    assert StructuredReport.model_validate(value)
    value = report().model_dump()
    value["comparison_table"]["rows"][0]["cells"][0]["text"] = "x" * 121
    with pytest.raises(ValueError, match="120"):
        schema.model_validate(value)
    plan.report_style = "detailed"
    assert report_schema(plan) is StructuredReport
