from io import BytesIO

import pytest
from pydantic import ValidationError

from deepresearch import report as documents
from deepresearch.contracts import ResearchPlan, ResearchUnit, StructuredReport, safe_http_url
from deepresearch.evidence import canonical_url, merge_results, ordered_sources
from deepresearch.render import citation_metadata, docx_report
from deepresearch.validators import research_gaps, single_source, supplemental_units


def make_document(ids, extra=""):
    marker = "[[" + ", ".join(ids) + "]]" if ids else ""
    summary = f"**可验证结论** {marker}"
    return documents.assemble("报告", summary, [("技术分析", f"可验证结论。{marker}{extra}")], [], [], "zh")


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


def test_reference_binding_first_appearance_and_render(result):
    pool, _, _ = merge_results([result])
    document = make_document(["E002", "E001"])
    mapping = documents.bind(document, pool)
    assert mapping == {"E002": 1, "E001": 2}
    assert not documents.validate(document, pool, set(pool))
    exported = documents.export_markdown(document, mapping, pool)
    assert exported == documents.export_markdown(document, mapping, pool)
    assert exported.count("[1](#ref-1)[2](#ref-2)") == 2
    assert "id=1" in exported and "utm_source=x" in exported  # Export the original locator, not a rewritten signed URL.
    assert all("utm_source" not in (e["canonical_url"] or "") for e in pool.values())
    display = documents.display_markdown(document, mapping)
    assert "[1](#citation-E002)[2](#citation-E001)" in display and "[[" not in display
    with pytest.raises(ValueError):
        documents.bind(make_document(["E999"]), pool)


def test_adjacent_markers_merge_and_code_is_never_rewritten(result):
    pool, _, _ = merge_results([result])
    body = "结论。[[E001]] [[E001, E002]]\n\n```mermaid\nflowchart LR\n  A[[E999]] --> B\n```\n\n`list[[E999]]` 是类型示例。"
    document = documents.assemble("报告", "摘要 [[E001]]", [("分析", body)], lang="zh")
    assert documents.marker_ids(document) == ["E001", "E001", "E001", "E002"]
    mapping = documents.bind(document, pool)
    display = documents.display_markdown(document, mapping)
    assert "结论。[1](#citation-E001)[2](#citation-E002)" in display
    assert "A[[E999]] --> B" in display and "`list[[E999]]`" in display
    assert not documents.problems(document, set(pool))


def test_html_escapes_model_and_source_text(result):
    pool, _, _ = merge_results([result])
    document = make_document(["E001"], extra="\n\n<script>alert(1)</script>")
    pool["E001"]["title"] = '<img src=x onerror="alert(1)">'
    value = documents.html_document(document, {"E001": 1}, pool)
    assert "<script>" not in value and "<img " not in value
    assert '<sup><a href="#ref-1">[1]</a></sup>' in value


def test_writer_problems_are_precise_and_sanitizing_never_invents_citations(result):
    pool, _, _ = merge_results([result])
    draft = "- 有据结论。[[E001]]\n- 无据结论。[[E999]] 另一句仍有据。[[E002]]\n| 维度 | A |\n| --- | --- |\n| 并发 | 伪造 [[E998]] |\n见 [官网](https://example.org/doc) 与 https://example.org/raw，编号[3]。"
    errors = documents.problems(draft, set(pool))
    assert any("E998, E999" in error for error in errors)
    assert any("numeric citation" in error for error in errors)
    assert any("2 URL" in error for error in errors)
    cleaned, audit = documents.sanitize(draft, set(pool))
    assert "E999" not in cleaned and "E998" not in cleaned
    assert "- 有据结论。[[E001]]" in cleaned and "另一句仍有据。[[E002]]" in cleaned
    assert "| 并发 | — |" in cleaned
    assert "https://" not in cleaned and "官网" in cleaned and "[3]" not in cleaned
    assert audit == {"dropped_statements": 2, "stripped_links": 2, "stripped_numeric_citations": 1}
    assert not documents.problems(cleaned, set(pool))


def test_final_gate_requires_citations_and_a_title(result):
    pool, _, _ = merge_results([result])
    assert any("cites no evidence" in error for error in documents.validate(make_document([]), pool, set(pool)))
    assert not documents.validate(make_document([]), pool, set())
    assert any("no title" in error for error in documents.validate("## 只有章节 [[E001]]", pool, set(pool)))


def test_section_answers_are_normalized_without_touching_statements():
    answer = "```markdown\n## 并发写入\n\n## 小节\n正文。[[E001]]\n```"
    assert documents.clean_answer(answer, "并发写入") == "### 小节\n正文。[[E001]]"
    assert documents.clean_answer("# 报告\n\n## 章节", whole_document=True) == "# 报告\n\n## 章节"
    document = documents.assemble("标题", "摘要", [("第一章", "### 小节\n正文")], ["百人规模"], ["公开资料有限"], "zh")
    assert documents.toc(document) == [{"level": 2, "text": "执行摘要"}, {"level": 2, "text": "第一章"}, {"level": 3, "text": "小节"}, {"level": 2, "text": "研究范围与局限"}]
    assert "- 百人规模" in document and "- 公开资料有限" in document
    assert documents.title_of(document) == "标题"


def test_writer_preambles_are_dropped_but_real_openings_are_kept():
    body = "**判断**。事实。[[E001]]"
    assert documents.clean_answer("Writing the assigned section body now.\n\n" + body, "章节") == body
    assert documents.clean_answer("好的，下面开始撰写本节正文。\n\n" + body, "章节") == body
    table = "以下是三种路径的对比：\n\n| 路径 | 成本 |\n|---|---|\n| A | 低 [[E001]] |"
    assert documents.clean_answer(table, "章节") == table
    assert documents.clean_answer("Here is the revised report.\n\n# 报告\n\n## 章节", whole_document=True) == "# 报告\n\n## 章节"


def test_notes_about_how_the_text_was_produced_are_removed():
    body = "**结论**。事实。[[E001]]\n\n*说明：本摘要为对各章节草稿的综合，未引入草稿之外的事实，也未新增证据 ID。*"
    assert documents.clean_answer(body, "执行摘要") == "**结论**。事实。[[E001]]"
    kept = "- 研究对象：证据 ID 在取证系统中的作用 [[E001]]\n\n| 项 | 说明 |\n|---|---|\n| A | evidence ID [[E001]] |"
    assert documents.clean_answer(kept, "章节") == kept
    code = '```mermaid\nflowchart LR\n  A["证据 ID"] --> B\n```'
    assert documents.clean_answer(code, "章节") == code


def test_outline_headings_lose_numbering_and_generated_parts_are_recognized():
    assert documents.plain_heading("一、前沿模型代际盘点") == "前沿模型代际盘点"
    assert documents.plain_heading("3. Pricing and packaging") == "Pricing and packaging"
    assert documents.plain_heading("第二章 市场格局") == "市场格局"
    assert documents.plain_heading("（四）风险与治理") == "风险与治理"
    for heading in ["2026 年路线图", "3.5 Sonnet 与 GPT-5 对比", "CI/CD 成熟度"]:
        assert documents.plain_heading(heading) == heading
    for heading in ["执行摘要与读者行动清单", "一、执行摘要", "Executive Summary", "摘要", "参考来源", "研究范围与局限", "四、研究限制与未解决的证据缺口", "References"]:
        assert documents.reserved_heading(heading), heading
    for heading in ["结论与行动建议", "风险与局限", "Sources of competitive advantage", "摘要生成能力对比"]:
        assert not documents.reserved_heading(heading), heading


def test_citable_gaps_ignore_private_context_and_hedge_single_sites(result, plan):
    pool, findings, _ = merge_results([result])
    units = [u.model_dump() for u in plan.research_units]
    assert research_gaps(plan, units, findings, pool) == []
    only = [dict(findings[0], evidence_ids=["E001"], high_risk=True)]
    gaps = research_gaps(plan, units, only, pool)
    assert any(g["code"].startswith("missing-") for g in gaps)
    assert not any(g["code"] == "independence" for g in gaps)
    assert single_source(only[0], pool, set(pool))
    assert not single_source(dict(findings[0], high_risk=True), pool, set(pool))
    # Non-citable evidence does not count as coverage.
    assert any(g["code"] == "coverage" for g in research_gaps(plan, units, findings, pool, citable_ids=set()))
    context = result.model_dump()
    context["assumptions_needed"] = ["团队规模未知"]
    assert research_gaps(plan, units, findings, pool, [context]) == []
    supplemental = supplemental_units(plan, gaps, 1, 1)
    assert len(supplemental) == 1 and supplemental[0]["parent_gap_id"]
    assert supplemental[0]["depends_on"] == ["R1"]


def test_supplements_spend_budget_on_missing_coverage_first():
    plan = ResearchPlan(goal="valid goal", research_units=[ResearchUnit(id=uid, skill="technical-route", objective="a good objective") for uid in ("R1", "R2")])
    gaps = [{"gap_id": "R1-open-questions", "unit_id": "R1", "code": "open-questions", "description": "q"}, {"gap_id": "R2-coverage", "unit_id": "R2", "code": "coverage", "description": "c"}]
    selected = supplemental_units(plan, gaps, 1, 1)
    assert [unit["depends_on"] for unit in selected] == [["R2"]]
    assert supplemental_units(plan, gaps, 1, 0) == []


def test_docx_export_uses_the_verified_markdown_document(result):
    from docx import Document

    pool, _, _ = merge_results([result])
    body = "正文结论。[[E001]]\n\n| 维度 | 方案 A |\n| --- | --- |\n| 并发 | 单写者 [[E002]] |\n\n- **要点** 说明"
    document = documents.assemble("报告", "摘要 [[E001]]", [("技术分析", body)], lang="zh")
    mapping = documents.bind(document, pool)
    value = {"format": "markdown-v2", "document": document, "citation_map": mapping, "citations": documents.citations(mapping, pool), "demo": True}
    doc = Document(BytesIO(documents.docx_document(value)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "正文结论。[1]" in text and "演示模式" in text and "参考资料" in text
    assert doc.tables[0].rows[1].cells[1].text == "单写者 [2]"
    assert "要点 说明" in text


def test_historical_ast_reports_still_export_to_word(result):
    from docx import Document

    pool, _, _ = merge_results([result])
    segment = {"text": "可验证结论", "evidence_ids": ["E001"], "segment_type": "fact"}
    report = StructuredReport.model_validate({"title": "报告", "executive_summary": [segment], "sections": [{"heading": "技术分析", "unit_ids": ["R1"], "segments": [segment]}], "conclusion": [segment]})
    value = {"report": report.model_dump(), "citation_map": {"E001": 1}, "citations": citation_metadata({"E001": 1}, pool), "demo": True, "limitations": []}
    doc = Document(BytesIO(docx_report(value)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "可验证结论[1]" in text and "演示模式" in text and "参考资料" in text
