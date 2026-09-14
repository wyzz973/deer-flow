"""Citation binding and rendering are pure functions; source metadata is read-only."""

from __future__ import annotations

import html
import re
from urllib.parse import quote

from .contracts import StructuredReport


def bind_citations(report: StructuredReport, pool):
    mapping = {}
    for segment in report.segments():
        for eid in segment.evidence_ids:
            if eid not in pool:
                raise ValueError("Unknown evidence id")
            if eid not in mapping:
                mapping[eid] = len(mapping) + 1
    return mapping


def plain(text):
    return re.sub(r"([\\`*_{}\[\]()#+.!>|~-])", r"\\\1", text).replace("\n", " ")


def citation_metadata(mapping, pool):
    return [{"number": number, **pool[eid]} for eid, number in mapping.items()]


def markdown(report, mapping, pool, limitations=(), demo=False):
    def segment(s):
        return plain(s.text) + "".join(f"[{mapping[e]}](#ref-{mapping[e]})" for e in dict.fromkeys(s.evidence_ids))

    lines = ["# " + plain(report.title), ""]
    if demo:
        lines += ["> 演示模式：以下证据和结论为合成测试数据，不可用于业务决策。", ""]
    if limitations:
        lines += ["## 研究限制", "", *[plain(x) for x in limitations], ""]
    lines += ["## 执行摘要", "", *[segment(s) + "\n" for s in report.executive_summary]]
    for section in report.sections:
        lines += ["## " + plain(section.heading), "", *[segment(s) + "\n" for s in section.segments]]
    lines += ["## 结论", "", *[segment(s) + "\n" for s in report.conclusion], "## 参考资料", ""]
    for ref in citation_metadata(mapping, pool):
        target = ref.get("url") or ref["canonical_url"]
        title = plain(ref["title"])
        locator = f"[{title}](<{quote(target, safe=':/?=&%#@+;,~-._')}>)" if target else title + " — " + plain(ref["source_uri"] or "")
        lines += [f'<a id="ref-{ref["number"]}"></a>', f"{ref['number']}. {locator} · {ref['origin']} · {ref['source_level']}", ""]
    return "\n".join(lines)


def html_report(report, mapping, pool, limitations=(), demo=False):
    esc = html.escape

    def segment(s):
        return "<p>" + esc(s.text) + "".join(f'<a href="#ref-{mapping[e]}">[{mapping[e]}]</a>' for e in dict.fromkeys(s.evidence_ids)) + "</p>"

    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8"><title>' + esc(report.title) + "</title><body><article>", "<h1>" + esc(report.title) + "</h1>"]
    if demo:
        parts += ["<aside>演示模式：合成测试数据，不可用于业务决策。</aside>"]
    parts += ["<aside>" + esc(x) + "</aside>" for x in limitations]
    parts += ["<h2>执行摘要</h2>", *map(segment, report.executive_summary)]
    for section in report.sections:
        parts += ["<h2>" + esc(section.heading) + "</h2>", *map(segment, section.segments)]
    parts += ["<h2>结论</h2>", *map(segment, report.conclusion), "<h2>参考资料</h2><ol>"]
    for ref in citation_metadata(mapping, pool):
        label = esc(ref["title"])
        target = ref.get("url") or ref["canonical_url"]
        if target:
            label = '<a rel="noreferrer noopener" href="' + esc(target, quote=True) + '">' + label + "</a>"
        else:
            label += " — " + esc(ref["source_uri"] or "")
        parts += [f'<li id="ref-{ref["number"]}">{label}</li>']
    return "".join(parts) + "</ol></article></body></html>"


def docx_report(value):
    """Export the same verified AST; no Markdown reparsing or model formatting."""
    from io import BytesIO

    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    report = StructuredReport.model_validate(value["report"])
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.15
    normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    doc.add_heading(report.title, 0)
    if value["demo"]:
        doc.add_paragraph("演示模式：合成测试数据，不可用于业务决策。")
    if value["limitations"]:
        doc.add_heading("研究限制", 1)
        for text in value["limitations"]:
            doc.add_paragraph(text)
    mapping = value["citation_map"]

    def segments(items):
        for item in items:
            doc.add_paragraph(item.text + "".join(f"[{mapping[e]}]" for e in dict.fromkeys(item.evidence_ids)))

    doc.add_heading("执行摘要", 1)
    segments(report.executive_summary)
    for section in report.sections:
        doc.add_heading(section.heading, 1)
        segments(section.segments)
    doc.add_heading("结论", 1)
    segments(report.conclusion)
    doc.add_heading("参考资料", 1)
    for ref in value["citations"]:
        doc.add_paragraph(f"[{ref['number']}] {ref['title']}\n{ref.get('url') or ref['canonical_url'] or ref['source_uri']}")
    # Stable core metadata avoids exposing local usernames.
    doc.core_properties.author = "DeepResearch"
    doc.core_properties.title = report.title
    out = BytesIO()
    doc.save(out)
    return out.getvalue()
