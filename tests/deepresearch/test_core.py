from io import BytesIO

import pytest
from pydantic import ValidationError

from deepresearch.contracts import ResearchPlan, ResearchUnit, StructuredReport, safe_http_url
from deepresearch.evidence import canonical_url, merge_results, ordered_sources
from deepresearch.render import bind_citations, citation_metadata, docx_report, html_report, markdown
from deepresearch.validators import research_gaps, supplemental_units, validate_report


def make_report(ids):
    s = {"text": "可验证结论", "evidence_ids": ids, "segment_type": "fact"}
    return StructuredReport.model_validate({"title": "报告", "executive_summary": [s], "sections": [{"heading": "技术分析", "unit_ids": ["R1"], "segments": [s]}], "conclusion": [s]})


def test_plan_dag_and_unique_ids(plan):
    with pytest.raises(ValidationError):
        ResearchPlan(goal="valid goal", research_units=[ResearchUnit(id="R1", skill="technical-route", objective="a good objective", depends_on=["R1"])])
    with pytest.raises(ValidationError):
        ResearchPlan(goal="valid goal", research_units=plan.research_units * 2)
    with pytest.raises(ValidationError):
        ResearchPlan(goal="valid goal", research_units=[ResearchUnit(id="R1", skill="technical-route", objective="a good objective", depends_on=["missing"])])


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "https://user:pass@example.org", "https://example.org/\nsecret", "https://example.org:notaport/path"])
def test_url_deny(url):
    with pytest.raises(ValueError):
        safe_http_url(url)


def test_canonical_url_preserves_business_parameters():
    assert canonical_url("https://EXAMPLE.com:443/page?id=1&utm_source=abc&lang=zh#x") == "https://example.com/page?id=1&lang=zh"
    assert canonical_url("http://example.com:8080/") == "http://example.com:8080/"
    assert canonical_url("http://example.com:0/") == "http://example.com:0/"


def test_merge_replay_lineage_and_distinct_origins(result):
    pool, findings, lineage = merge_results([result])
    replay, repeated, _ = merge_results([result], pool)
    assert pool == replay and findings == repeated
    assert set(pool) == {"E001", "E002"}
    assert set(lineage.values()) == set(pool)
    second = result.model_copy(deep=True)
    second.unit_id = "R2"
    merged, _, _ = merge_results([result, second], pool)
    assert len(merged) == 2
    assert all(e["unit_ids"] == ["R1", "R2"] for e in merged.values())


def test_source_priority_injected_file_fallback(settings, plan, tmp_path):
    priority = tmp_path / "priority.yaml"
    priority.write_text("- external-web\n", encoding="utf-8")
    settings.source_priority_file = str(priority)
    a, provenance = ordered_sources(settings, plan.research_units[0].source_strategy, ["internal-kb"])
    assert a[0].name == "internal-kb" and provenance == "injected"
    b, provenance = ordered_sources(settings, plan.research_units[0].source_strategy)
    assert b[0].name == "external-web" and provenance == "file"
    settings.source_priority_file = None
    _, provenance = ordered_sources(settings, plan.research_units[0].source_strategy)
    assert provenance == "fallback"
    assert {s.origin for s in b} == {"internal", "external"}


def test_reference_binding_first_appearance_and_render(result, plan):
    pool, _, _ = merge_results([result])
    report = make_report(["E002", "E001", "E002"])
    mapping = bind_citations(report, pool)
    assert mapping == {"E002": 1, "E001": 2}
    assert not validate_report(report, plan, pool)
    one = markdown(report, mapping, pool)
    assert one == markdown(report, mapping, pool)
    assert one.count("[1](#ref-1)") == 3
    assert "id=1" in one and "utm_source=x" in one  # Export the original locator, not a rewritten signed URL.
    assert all("utm_source" not in (e["canonical_url"] or "") for e in pool.values())
    with pytest.raises(ValueError):
        bind_citations(make_report(["E999"]), pool)


def test_html_escapes_model_and_source_text(result):
    pool, _, _ = merge_results([result])
    report = make_report(["E001"])
    report.executive_summary[0].text = "<script>alert(1)</script>"
    pool["E001"]["title"] = '<img src=x onerror="alert(1)">'
    value = html_report(report, {"E001": 1}, pool)
    assert "<script>" not in value and "<img " not in value


def test_final_validator_missing_evidence_and_manual_citations(result, plan):
    pool, _, _ = merge_results([result])
    report = make_report([])
    report.sections[0].unit_ids = ["wrong"]
    report.executive_summary[0].text = "claim[1] https://example.com"
    assert len(validate_report(report, plan, pool)) == 3


def test_dual_source_and_high_risk_gap(result, plan):
    pool, findings, _ = merge_results([result])
    assert research_gaps(plan, [u.model_dump() for u in plan.research_units], findings, pool) == []
    only = [dict(findings[0], evidence_ids=["E001"], high_risk=True)]
    gaps = research_gaps(plan, [u.model_dump() for u in plan.research_units], only, pool)
    assert any(g["code"].startswith("missing-") for g in gaps)
    assert any(g["code"] == "independence" for g in gaps)
    supplemental = supplemental_units(plan, gaps, 1, 1)
    assert len(supplemental) == 1 and supplemental[0]["parent_gap_id"]
    assert supplemental[0]["depends_on"] == ["R1"]


def test_docx_uses_same_ast(result):
    from docx import Document

    pool, _, _ = merge_results([result])
    report = make_report(["E001"])
    value = {"report": report.model_dump(), "citation_map": {"E001": 1}, "citations": citation_metadata({"E001": 1}, pool), "demo": True, "limitations": []}
    data = docx_report(value)
    doc = Document(BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "可验证结论[1]" in text and "演示模式" in text and "参考资料" in text
