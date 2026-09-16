"""Citation binding and rendering are pure functions; source metadata is read-only."""

from __future__ import annotations

import html
import re
from urllib.parse import quote

from .contracts import StructuredReport


def bind_citations(report: StructuredReport, pool):
    mapping = {}
    sources = {}
    for segment in report.segments():
        for eid in segment.evidence_ids:
            if eid not in pool:
                raise ValueError("Unknown evidence id")
            if eid not in mapping:
                evidence = pool[eid]
                locator = evidence.get("canonical_url") or evidence.get("url") or evidence.get("source_uri") or eid
                identity = (evidence.get("origin"), evidence.get("source_name"), locator, evidence.get("document_hash"))
                mapping[eid] = sources.setdefault(identity, len(sources) + 1)
    return mapping


def plain(text):
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!>|~-])", r"\\\1", text).replace("\n", " ")


def citation_metadata(mapping, pool):
    groups = {}
    for eid, number in mapping.items():
        if number not in groups:
            groups[number] = {"number": number, **pool[eid], "evidence_ids": [], "excerpts": []}
        groups[number]["evidence_ids"].append(eid)
        groups[number]["excerpts"].append({"evidence_id": eid, "text": pool[eid]["snippet"][:2000]})
    return list(groups.values())


def markdown(report, mapping, pool, limitations=(), demo=False):
    def segment(s):
        return plain(s.text) + "".join(f"[{number}](#ref-{number})" for number in dict.fromkeys(mapping[e] for e in s.evidence_ids))

    lines = ["# " + plain(report.title), ""]
    if demo:
        lines += ["> 演示模式：以下证据和结论为合成测试数据，不可用于业务决策。", ""]
    lines += ["## 执行摘要", "", *[segment(s) + "\n" for s in report.executive_summary]]
    if report.comparison_table:
        table = report.comparison_table
        lines += ["## 快速对比", "", "| 维度 | " + " | ".join(plain(h).replace("|", "\\|") for h in table.headers) + " |", "| " + " | ".join("---" for _ in range(len(table.headers) + 1)) + " |"]
        lines += ["| " + plain(row.label).replace("|", "\\|") + " | " + " | ".join(segment(cell).replace("|", "\\|") for cell in row.cells) + " |" for row in table.rows]
        lines.append("")
    for section in report.sections:
        lines += ["## " + plain(section.heading), "", *[segment(s) + "\n" for s in section.segments]]
    lines += ["## 结论", "", *[segment(s) + "\n" for s in report.conclusion]]
    if limitations:
        lines += ["## 研究限制", "", *[plain(x) for x in limitations], ""]
    lines += ["## 参考资料", ""]
    for ref in citation_metadata(mapping, pool):
        target = ref.get("url") or ref["canonical_url"]
        title = plain(ref["title"])
        locator = f"[{title}](<{quote(target, safe=':/?=&%#@+;,~-._')}>)" if target else title + " — " + plain(ref["source_uri"] or "")
        lines += [f'<a id="ref-{ref["number"]}"></a>', f"{ref['number']}. {locator} · {ref['origin']} · {ref['source_level']}", ""]
    return "\n".join(lines)


def html_report(report, mapping, pool, limitations=(), demo=False):
    esc = html.escape

    def segment(s):
        return "<p>" + esc(s.text) + "".join(f'<a href="#ref-{number}">[{number}]</a>' for number in dict.fromkeys(mapping[e] for e in s.evidence_ids)) + "</p>"

    parts = ['<!doctype html><html lang="zh"><meta charset="utf-8"><title>' + esc(report.title) + "</title><body><article>", "<h1>" + esc(report.title) + "</h1>"]
    if demo:
        parts += ["<aside>演示模式：合成测试数据，不可用于业务决策。</aside>"]
    parts += ["<h2>执行摘要</h2>", *map(segment, report.executive_summary)]
    if report.comparison_table:
        table = report.comparison_table
        parts += ["<h2>快速对比</h2><table><thead><tr><th>维度</th>", *["<th>" + esc(h) + "</th>" for h in table.headers], "</tr></thead><tbody>"]
        for row in table.rows:
            parts += ["<tr><th>" + esc(row.label) + "</th>", *["<td>" + segment(cell) + "</td>" for cell in row.cells], "</tr>"]
        parts.append("</tbody></table>")
    for section in report.sections:
        parts += ["<h2>" + esc(section.heading) + "</h2>", *map(segment, section.segments)]
    parts += ["<h2>结论</h2>", *map(segment, report.conclusion)]
    if limitations:
        parts += ["<h2>研究限制</h2>", *["<aside>" + esc(x) + "</aside>" for x in limitations]]
    parts += ["<h2>参考资料</h2><ol>"]
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
    mapping = value["citation_map"]

    def segment_text(item):
        return item.text + "".join(f"[{number}]" for number in dict.fromkeys(mapping[e] for e in item.evidence_ids))

    def segments(items):
        for item in items:
            doc.add_paragraph(segment_text(item))

    doc.add_heading("执行摘要", 1)
    segments(report.executive_summary)
    if report.comparison_table:
        source = report.comparison_table
        doc.add_heading("快速对比", 1)
        table = doc.add_table(rows=1, cols=len(source.headers) + 1)
        table.style = "Table Grid"
        for cell, label in zip(table.rows[0].cells, ["维度", *source.headers], strict=True):
            cell.text = label
        for row in source.rows:
            for cell, text in zip(table.add_row().cells, [row.label, *[segment_text(item) for item in row.cells]], strict=True):
                cell.text = text
    for section in report.sections:
        doc.add_heading(section.heading, 1)
        segments(section.segments)
    doc.add_heading("结论", 1)
    segments(report.conclusion)
    if value["limitations"]:
        doc.add_heading("研究限制", 1)
        for text in value["limitations"]:
            doc.add_paragraph(text)
    doc.add_heading("参考资料", 1)
    for ref in value["citations"]:
        doc.add_paragraph(f"[{ref['number']}] {ref['title']}\n{ref.get('url') or ref['canonical_url'] or ref['source_uri']}")
    # Stable core metadata avoids exposing local usernames.
    doc.core_properties.author = "DeepResearch"
    doc.core_properties.title = report.title
    out = BytesIO()
    doc.save(out)
    return out.getvalue()
