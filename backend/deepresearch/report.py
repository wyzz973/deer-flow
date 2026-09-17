"""Markdown research reports with verifiable evidence markers.

Writers produce ordinary Markdown and reference evidence only with markers such
as ``[[E012]]`` or ``[[E012, E031]]``. Everything after that is deterministic:
marker validation, removal of statements whose evidence cannot be verified,
citation numbering, the table of contents and Markdown/HTML/DOCX export. A model
never chooses a citation number or a URL, and fenced code (including Mermaid
diagrams) is never rewritten.
"""

from __future__ import annotations

import html
import re
from io import BytesIO
from urllib.parse import quote, urlsplit

from .output import visible_text
from .render import citation_metadata

MARKER = re.compile(r"(?:\[\[|【|\[)\s*(E\d{3,}(?:\s*[,，;；、]\s*E\d{3,})*)\s*(?:\]\]|】|\])(?!\()")
MARKER_ID = re.compile(r"E\d{3,}")
NUMERIC = re.compile(r"\[\^?\d{1,3}\](?![(:])")
LINK = re.compile(r"\[([^\]\n]*)\]\(\s*<?(?:https?://|www\.)[^)\s]*>?\s*\)")
BARE_URL = re.compile(r"<?\bhttps?://[^\s<>)\]\"'，。；]+>?")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
LIST_PREFIX = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+|\s*>\s*)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# Outline headings arrive numbered ("一、", "1. ", "第二章"). The reader shows an
# outline, and numbering would clash with the unnumbered generated parts.
HEADING_NUMBER = re.compile(r"^\s*(?:第[一二三四五六七八九十百零〇\d]+[章节部分篇]\s*[、.．:：]?\s*|[（(][一二三四五六七八九十\d]+[）)]\s*|[一二三四五六七八九十百零〇]+[、.．]\s*|\d+(?:\.\d+)*(?:[、．)]|\.(?=\s))\s*|[IVX]+\.\s+)")
# Parts assembled around the sections; an outline must not plan them again.
RESERVED_HEADING = re.compile(
    r"^(?:(?:执行摘要|内容摘要|摘要|参考(?:来源|文献|资料))(?:$|[与和及、:：（(\s])|研究(?:范围|局限|限制)(?:$|[与和及、:：])"
    r"|(?:executive summary|summary|tl;?dr|references|sources|bibliography|limitations|scope and limitations)$|executive summary\b)",
    re.I,
)
# A writer occasionally narrates before the requested Markdown. Only a whole
# first line about writing is dropped; a real opening sentence is kept.
PREAMBLE = re.compile(
    r"^(?:(?:writing|here(?: is|'s)|below is|now writing|i(?:'ll| will) (?:now )?write|let me write)\b[^\n]{0,120}\b(?:section|body|report|draft|markdown|summary)\b[^\n]{0,40}"
    r"|(?:好的[，,。!！]?\s*)?(?:下面|以下|现在|接下来)?我?(?:将|会|来)?(?:开始)?(?:撰写|编写|输出)(?:本|该|这一)?(?:节|章节|部分|正文|报告|摘要)[^\n]{0,40}"
    r"|(?:好的[，,。!！]?\s*)?(?:以下|下面)(?:是|为)(?:修订后的|改写后的|更新后的)?(?:本节|该节|本章|报告|正文|章节|摘要)(?:正文|内容)?[：:。.]?)$",
    re.I,
)

LABELS = {
    "zh": {
        "summary": "执行摘要",
        "scope": "研究范围与局限",
        "assumptions": "前提假设",
        "limitations": "局限",
        "references": "参考资料",
        "demo": "演示模式：以下证据和结论为合成测试数据，不可用于业务决策。",
    },
    "en": {
        "summary": "Executive summary",
        "scope": "Scope and limitations",
        "assumptions": "Assumptions",
        "limitations": "Limitations",
        "references": "References",
        "demo": "Demo mode: synthetic evidence and conclusions; not for business decisions.",
    },
}


def language(text):
    return "zh" if re.search(r"[\u4e00-\u9fff]", text or "") else "en"


def _segments(document):
    """Split into (is_code, lines). Fenced blocks stay verbatim."""
    segments, buffer, fence = [], [], None
    for line in (document or "").split("\n"):
        if fence is None:
            opening = FENCE.match(line)
            if opening:
                if buffer:
                    segments.append((False, buffer))
                buffer, fence = [line], opening.group(1)
                continue
            buffer.append(line)
        else:
            buffer.append(line)
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                segments.append((True, buffer))
                buffer, fence = [], None
    if buffer:
        segments.append((fence is not None, buffer))
    return segments


def _map_lines(document, transform):
    """Apply ``transform`` to prose lines with inline code protected.

    ``transform`` may return None to remove a line.
    """
    output = []
    for code, lines in _segments(document):
        if code:
            output.extend(lines)
            continue
        for line in lines:
            saved = []

            def keep(match):
                saved.append(match.group(0))
                return f"\x00{len(saved) - 1}\x00"

            changed = transform(INLINE_CODE.sub(keep, line))
            if changed is not None:
                output.append(re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], changed))
    return "\n".join(output)


def marker_ids(document):
    """Evidence IDs in first-appearance order, excluding code."""
    ids = []

    def collect(line):
        for match in MARKER.finditer(line):
            ids.extend(MARKER_ID.findall(match.group(1)))
        return line

    _map_lines(document, collect)
    return ids


def _replace_markers(line, render):
    """Merge adjacent markers into one group before rendering them."""
    output, last, group, start, end = [], 0, [], 0, 0
    for match in MARKER.finditer(line):
        ids = MARKER_ID.findall(match.group(1))
        if group and not line[end : match.start()].strip():
            group += ids
            end = match.end()
            continue
        if group:
            output += [line[last:start], render(group)]
            last = end
        group, start, end = ids, match.start(), match.end()
    if group:
        output += [line[last:start], render(group)]
        last = end
    output.append(line[last:])
    return "".join(output)


def problems(text, eligible):
    """Precise, value-free feedback for a bounded writer repair."""
    unknown, numeric, links = set(), 0, 0

    def scan(line):
        nonlocal numeric, links
        for match in MARKER.finditer(line):
            unknown.update(eid for eid in MARKER_ID.findall(match.group(1)) if eid not in eligible)
        prose = MARKER.sub("", line)
        numeric += len(NUMERIC.findall(prose))
        links += len(LINK.findall(prose)) + len(BARE_URL.findall(LINK.sub("", prose)))
        return line

    _map_lines(text, scan)
    errors = []
    if unknown:
        errors.append("Unknown or non-citable evidence IDs: " + ", ".join(sorted(unknown)[:30]) + ". Use only IDs listed in `evidence`; omit a statement that no listed evidence supports.")
    if numeric:
        errors.append(f"{numeric} numeric citation(s) such as [1]. Cite only with [[E###]] markers.")
    if links:
        errors.append(f"{links} URL or link(s). Do not write URLs or Markdown links; sources are attached from evidence markers.")
    return errors


LEADING_MARKERS = re.compile(r"^\s*(?:" + MARKER.pattern + r"\s*)+")


def _sentences(text):
    """Split prose into statements; a marker after the full stop belongs to
    the sentence it follows, not to the next one."""
    parts = re.split(r"(?<=[。！？!?；;])|(?<=\.)(?=\s+[A-Z\u4e00-\u9fff*\[【])", text)
    statements = []
    for part in filter(None, parts):
        lead = LEADING_MARKERS.match(part)
        if lead and statements:
            statements[-1] += lead.group(0)
            part = part[lead.end() :]
        if part:
            statements.append(part)
    return statements


def sanitize(text, eligible):
    """Remove what cannot be verified instead of guessing a replacement.

    Links and numeric citations are stripped. A sentence, bullet or table cell
    that relies on an unknown or non-citable evidence ID is removed (a table
    cell becomes a dash), so no statement keeps a citation it does not have.
    """
    audit = {"dropped_statements": 0, "stripped_links": 0, "stripped_numeric_citations": 0}

    def invalid(fragment):
        return any(eid not in eligible for match in MARKER.finditer(fragment) for eid in MARKER_ID.findall(match.group(1)))

    def clean(line):
        line, count = LINK.subn(lambda m: m.group(1), line)
        audit["stripped_links"] += count
        line, count = BARE_URL.subn("", line)
        audit["stripped_links"] += count
        prose = MARKER.sub(lambda m: "\x01" * len(m.group(0)), line)
        for match in reversed(list(NUMERIC.finditer(prose))):
            line = line[: match.start()] + line[match.end() :]
            audit["stripped_numeric_citations"] += 1
        if not invalid(line):
            return line
        if line.lstrip().startswith("|"):
            cells = re.split(r"(?<!\\)\|", line)
            for index, cell in enumerate(cells):
                if invalid(cell):
                    audit["dropped_statements"] += 1
                    cells[index] = " — "
            return "|".join(cells)
        match = LIST_PREFIX.match(line)
        prefix = match.group(0) if match else ""
        sentences = _sentences(line[len(prefix) :])
        kept = [sentence for sentence in sentences if not invalid(sentence)]
        audit["dropped_statements"] += len(sentences) - len(kept)
        body = "".join(kept).strip()
        return prefix + body if body else None

    return _map_lines(text, clean), audit


# Pipeline vocabulary. A prose paragraph using it describes how the text was
# produced ("未新增证据 ID"), which is bookkeeping, never reader content.
PROCESS_NOTE = re.compile(r"证据\s*ID|证据编号|证据标记|evidence\s+(?:IDs?|markers?)|section[_ ]drafts|章节草稿", re.I)
BLOCK_START = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\||>|#)")


def _drop_process_notes(text):
    output = []
    for code, lines in _segments(text):
        if code:
            output.extend(lines)
            continue
        paragraph = []
        for line in [*lines, ""]:
            if line.strip():
                paragraph.append(line)
                continue
            if paragraph and not (not BLOCK_START.match(paragraph[0]) and PROCESS_NOTE.search(" ".join(paragraph))):
                output.extend(paragraph)
            paragraph = []
            output.append(line)
        output.pop()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def clean_answer(text, heading=None, *, whole_document=False):
    """Normalize a writer's Markdown without touching its research statements.

    A narrated first line and prose notes about the writing pipeline are
    dropped. A section body loses a repeated heading and has its top-level
    headings demoted; a whole revised document keeps its own heading structure.
    """
    text = visible_text(text or "").strip()
    first, _, rest = text.partition("\n")
    if "[[" not in first and PREAMBLE.match(first.strip()):
        text = rest.strip()
    wrapped = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.S)
    if wrapped:
        text = wrapped.group(1).strip()
    text = _drop_process_notes(text)
    if whole_document:
        return text
    lines = text.split("\n")
    normalized = " ".join((heading or "").split()).casefold()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and (match := HEADING.match(lines[0])) and (len(match.group(1)) == 1 or " ".join(match.group(2).split()).casefold() == normalized):
        lines.pop(0)

    def demote(line):
        match = HEADING.match(line)
        if match and len(match.group(1)) < 3:
            return "### " + match.group(2)
        return line

    return _map_lines("\n".join(lines).strip(), demote).strip()


def plain_heading(heading):
    stripped = HEADING_NUMBER.sub("", heading or "", count=1).strip()
    return stripped or (heading or "").strip()


def reserved_heading(heading):
    """Whether a planned section duplicates the summary, scope or references."""
    return bool(RESERVED_HEADING.match(plain_heading(heading)))


def assemble(title, summary, sections, assumptions=(), limitations=(), lang="zh"):
    labels = LABELS[lang]
    parts = [f"# {title.strip()}", "", f"## {labels['summary']}", "", summary.strip(), ""]
    for heading, body in sections:
        if body.strip():
            parts += [f"## {heading.strip()}", "", body.strip(), ""]
    assumptions = [item.strip() for item in dict.fromkeys(assumptions) if item and item.strip()]
    limitations = [item.strip() for item in dict.fromkeys(limitations) if item and item.strip()]
    if assumptions or limitations:
        parts += [f"## {labels['scope']}", ""]
        if assumptions:
            parts += [f"**{labels['assumptions']}**", "", *[f"- {item}" for item in assumptions], ""]
        if limitations:
            parts += [f"**{labels['limitations']}**", "", *[f"- {item}" for item in limitations], ""]
    return "\n".join(parts).strip() + "\n"


def title_of(document):
    for code, lines in _segments(document):
        if code:
            continue
        for line in lines:
            match = HEADING.match(line)
            if match and len(match.group(1)) == 1:
                return MARKER.sub("", match.group(2)).strip()
    return ""


def toc(document):
    items = []
    for code, lines in _segments(document):
        if code:
            continue
        for line in lines:
            match = HEADING.match(line)
            if match and len(match.group(1)) in {2, 3}:
                items.append({"level": len(match.group(1)), "text": MARKER.sub("", match.group(2)).strip()})
    return items


def _page_identity(evidence, eid):
    """One display number per page, however it was read.

    Reads through different tools or excerpt windows give different snapshot
    hashes; each excerpt keeps its own. Scheme, "www." and a trailing slash do
    not change the page, a query does. Records without a URL stay per record.
    """
    url = evidence.get("canonical_url") or evidence.get("url")
    if url:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower().removeprefix("www.")
            port = f":{parts.port}" if parts.port not in (None, 80, 443) else ""
        except ValueError:
            host = None
        if host:
            return (evidence.get("origin"), host + port, parts.path.rstrip("/") or "/", parts.query)
    locator = evidence.get("source_uri") or eid
    return (evidence.get("origin"), evidence.get("source_name"), locator, evidence.get("document_hash"))


def bind(document, pool):
    """Number cited pages by first appearance; excerpts of one page share it."""
    mapping, numbers = {}, {}
    for eid in marker_ids(document):
        if eid not in pool:
            raise ValueError("Unknown evidence id")
        if eid not in mapping:
            mapping[eid] = numbers.setdefault(_page_identity(pool[eid], eid), len(numbers) + 1)
    return mapping


def _numbers(ids, mapping):
    ordered = {}
    for eid in ids:
        if eid in mapping:
            ordered.setdefault(mapping[eid], eid)
    return ordered


def validate(document, pool, eligible):
    errors = problems(document, eligible)
    if eligible and not marker_ids(document):
        errors.append("The report cites no evidence although citable evidence exists.")
    if not title_of(document):
        errors.append("The report has no title.")
    return errors


def _literal_dollars(line):
    """Prices such as "$19 ... $39" must not become inline math in renderers."""
    return re.sub(r"(?<!\\)\$", r"\\$", line)


def display_markdown(document, mapping):
    """Client rendering: citations are in-document links the UI turns into chips."""
    return _map_lines(document, lambda line: _literal_dollars(_replace_markers(line, lambda ids: "".join(f"[{number}](#citation-{eid})" for number, eid in _numbers(ids, mapping).items()))))


EXCERPT_HEADER = re.compile(r"^(?:Source|Title|Excerpt|Literal matches|No literal match found)[^\n]*\n", re.M)


SYNOPSIS = re.compile(r"\A\[Full [^\]]* output saved to [^\]]*\]\n.*?(?:Raw sample[^\n]*\n|\Z)", re.S)


def excerpt(snippet, limit=2000):
    """Readable excerpt: drop tool envelopes (a host synopsis of an externalized
    result and the native fetch header lines) and keep the page text."""
    text = SYNOPSIS.sub("", snippet or "")
    text = EXCERPT_HEADER.sub("", text)
    text = re.sub(r"\n\[(?:More content|End of extracted page)[^\]]*\]\s*$", "", text).strip()
    return text[:limit]


def _display_title(item, pool):
    """The first real page title in a citation group, else a readable locator."""
    for eid in item["evidence_ids"]:
        evidence = pool[eid]
        title = " ".join((evidence.get("title") or "").split())
        locators = {(evidence.get(key) or "").rstrip("/") for key in ("url", "canonical_url")}
        if title and title.casefold() not in {"untitled", "untitled document", "no title"} and title.rstrip("/") not in locators:
            return title
    locator = item.get("url") or item.get("canonical_url")
    if locator:
        return re.sub(r"^https?://(?:www\.)?", "", locator).rstrip("/")
    return item.get("title") or item.get("source_uri") or item["evidence_id"]


def references(mapping, pool):
    values = citation_metadata(mapping, pool)
    for item in values:
        item["title"] = _display_title(item, pool)
    return values


def citations(mapping, pool):
    values = references(mapping, pool)
    for item in values:
        item["domain"] = (urlsplit(item.get("url") or item.get("canonical_url") or "").hostname or "").removeprefix("www.")
        item["snippet"] = excerpt(item.get("snippet"), 1200)
        for part in item["excerpts"]:
            part["text"] = excerpt(pool[part["evidence_id"]].get("snippet"))
    return values


def _reference_lines(mapping, pool, lang):
    lines = [f"## {LABELS[lang]['references']}", ""]
    for ref in references(mapping, pool):
        target = ref.get("url") or ref.get("canonical_url")
        title = ref["title"].replace("[", "(").replace("]", ")")
        locator = f"[{title}](<{quote(target, safe=':/?=&%#@+;,~-._')}>)" if target else f"{title} — {ref.get('source_uri') or ''}"
        date = f" · {str(ref['published_at'])[:10]}" if ref.get("published_at") else ""
        lines += [f'<a id="ref-{ref["number"]}"></a>', f"{ref['number']}. {locator}{date}", ""]
    return lines


def export_markdown(document, mapping, pool, lang="zh", demo=False):
    body = _map_lines(document, lambda line: _literal_dollars(_replace_markers(line, lambda ids: "".join(f"[{number}](#ref-{number})" for number in _numbers(ids, mapping)))))
    lines = body.rstrip().split("\n")
    if demo:
        lines[1:1] = ["", f"> {LABELS[lang]['demo']}"]
    return "\n".join([*lines, "", *_reference_lines(mapping, pool, lang)]).rstrip() + "\n"


def _markdown_parser():
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])


def html_document(document, mapping, pool, lang="zh", demo=False):
    body = _map_lines(document, lambda line: _replace_markers(line, lambda ids: "\u27e6" + ",".join(str(number) for number in _numbers(ids, mapping)) + "\u27e7"))
    rendered = _markdown_parser().render(body)
    rendered = re.sub(r"\u27e6([\d,]+)\u27e7", lambda m: "".join(f'<sup><a href="#ref-{n}">[{n}]</a></sup>' for n in m.group(1).split(",")), rendered)
    esc = html.escape
    items = []
    for ref in references(mapping, pool):
        label = esc(ref["title"])
        target = ref.get("url") or ref.get("canonical_url")
        label = f'<a rel="noreferrer noopener" href="{esc(target, quote=True)}">{label}</a>' if target else label + " — " + esc(ref.get("source_uri") or "")
        items.append(f'<li id="ref-{ref["number"]}">{label}</li>')
    style = (
        "body{font:16px/1.7 system-ui,sans-serif;max-width:820px;margin:40px auto;padding:0 16px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px 8px;vertical-align:top}"
        "pre{white-space:pre-wrap;background:#f5f5f5;padding:12px}"
    )
    notice = f"<aside>{esc(LABELS[lang]['demo'])}</aside>" if demo else ""
    return (
        f'<!doctype html><html lang="{lang}"><meta charset="utf-8"><title>{esc(title_of(document))}</title><style>{style}</style><body><article>'
        + notice
        + rendered
        + f"<h2>{esc(LABELS[lang]['references'])}</h2><ol>"
        + "".join(items)
        + "</ol></article></body></html>"
    )


def docx_document(value):
    """Word export of the same verified Markdown document."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    lang = value.get("language") or language(value.get("document", ""))
    mapping = value["citation_map"]
    body = _map_lines(value["document"], lambda line: _replace_markers(line, lambda ids: "".join(f"[{number}]" for number in _numbers(ids, mapping))))
    tokens = _markdown_parser().parse(body)
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    def runs(paragraph, inline):
        bold = italic = False
        for child in inline.children or []:
            if child.type == "strong_open":
                bold = True
            elif child.type == "strong_close":
                bold = False
            elif child.type == "em_open":
                italic = True
            elif child.type == "em_close":
                italic = False
            elif child.type in {"text", "code_inline"}:
                run = paragraph.add_run(child.content)
                run.bold, run.italic = bold, italic
                if child.type == "code_inline":
                    run.font.name = "Consolas"
            elif child.type in {"softbreak", "hardbreak"}:
                paragraph.add_run(" " if child.type == "softbreak" else "\n")

    def plain(content):
        return re.sub(r"\*\*|__|`", "", content)

    lists, table, row, index = [], None, None, 0
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind == "heading_open":
            level = int(token.tag[1])
            doc.add_heading(plain(tokens[index + 1].content), 0 if level == 1 else min(level - 1, 3))
            index += 2
        elif kind in {"bullet_list_open", "ordered_list_open"}:
            lists.append("List Number" if kind == "ordered_list_open" else "List Bullet")
        elif kind in {"bullet_list_close", "ordered_list_close"}:
            lists.pop()
        elif kind == "table_open":
            table = []
        elif kind == "tr_open":
            row = []
        elif kind in {"th_open", "td_open"}:
            row.append(plain(tokens[index + 1].content) if tokens[index + 1].type == "inline" else "")
        elif kind == "tr_close":
            table.append(row)
        elif kind == "table_close":
            columns = max((len(r) for r in table), default=0)
            if columns:
                grid = doc.add_table(rows=0, cols=columns)
                grid.style = "Table Grid"
                for values in table:
                    cells = grid.add_row().cells
                    for cell, text in zip(cells, values + [""] * (columns - len(values)), strict=True):
                        cell.text = text
            table = None
        elif kind == "inline" and table is None and tokens[index - 1].type == "paragraph_open":
            style = lists[-1] if lists else None
            paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            runs(paragraph, token)
        elif kind in {"fence", "code_block"}:
            paragraph = doc.add_paragraph()
            run = paragraph.add_run(token.content.rstrip())
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        index += 1
    if value.get("demo"):
        doc.add_paragraph(LABELS[lang]["demo"])
    doc.add_heading(LABELS[lang]["references"], 1)
    for ref in value["citations"]:
        doc.add_paragraph(f"[{ref['number']}] {ref['title']}\n{ref.get('url') or ref.get('canonical_url') or ref.get('source_uri') or ''}")
    doc.core_properties.author = "DeepResearch"
    doc.core_properties.title = title_of(value["document"])[:255]
    out = BytesIO()
    doc.save(out)
    return out.getvalue()
