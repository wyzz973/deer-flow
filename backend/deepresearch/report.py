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
import struct
from io import BytesIO
from urllib.parse import quote, urlsplit

from .output import visible_text
from .render import citation_metadata

MARKER = re.compile(r"(?:\[\[|【|\[)\s*(E\d{3,}(?:\s*[,，;；、]\s*E\d{3,})*)\s*(?:\]\]|】|\])(?!\()")
MARKER_ID = re.compile(r"E\d{3,}")
NUMERIC = re.compile(r"\[\^?\d{1,3}\](?![(:])")
# Anything written like a marker that MARKER does not accept: [[E12]], [[e001]],
# [[E001-E002]], [[E001 E002]], 【E1】, （E001）. normalize_markers rewrites the ones
# that only differ in separators or case; what is left cannot be verified.
# The parenthesized form is limited to three-digit IDs: "(E2650)" is a CPU, not a citation.
LOOSE_MARKER = re.compile(r"\[\[[^\[\]\n]{0,80}\]\]|【\s*[Ee]\d+[^】\n]{0,40}】|[（(]\s*[Ee]\d{3}(?!\d)(?:\s*[,，、;；和与及&\-–~至到\s]\s*[Ee]\d{3}(?!\d))*\s*[）)]")
# A writer may only link inside the document. Every other target is removed:
# "//host/x.png" is as remote as "https://host/x.png", and an image or a link
# the model chose is an exfiltration channel for injected instructions. The
# label is bounded so a line of stray brackets cannot make matching quadratic.
LINK = re.compile(r"!?\[([^\[\]\n]{0,300})\]\(\s*<?(?!#)[^)\s]*>?(?:\s+\"[^\"\n]{0,200}\")?\s*\)")
REFERENCE_DEFINITION = re.compile(r"^\s{0,3}\[[^\]\n]{1,200}\]:\s*\S+.*$")
BARE_URL = re.compile(r"<?\b(?:https?|ftp)://[^\s<>)\]\"'，。；]+>?|<?\bmailto:[^\s<>)\]\"'，。；]+>?")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
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
        "contents": "目录",
        "figure": "图",
        "appendix": "附录：未渲染的图表源码",
        "unrendered": "本次导出未包含图形，源码见附录",
    },
    "en": {
        "summary": "Executive summary",
        "scope": "Scope and limitations",
        "assumptions": "Assumptions",
        "limitations": "Limitations",
        "references": "References",
        "demo": "Demo mode: synthetic evidence and conclusions; not for business decisions.",
        "contents": "Contents",
        "figure": "Figure",
        "appendix": "Appendix: diagram sources that were not rendered",
        "unrendered": "no picture in this export; the source is in the appendix",
    },
}


ASKS_ENGLISH = re.compile(r"(?i)\b(?:answer|reply|respond|write|report)\s+in\s+english\b|用英[文语]|英文(?:回答|撰写|输出|报告|写)")
ASKS_CHINESE = re.compile(r"(?i)\bin\s+chinese\b|用中文|中文(?:回答|撰写|输出|报告|写)")


def language(text):
    """The reader's language for report labels: "zh" or "en".

    An explicit wish wins. Otherwise Chinese needs to carry the sentence: one
    Chinese name inside an English question is still an English question, and
    kana marks Japanese, which has no label set of its own and gets English labels.
    """
    text = text or ""
    if ASKS_ENGLISH.search(text):
        return "en"
    if ASKS_CHINESE.search(text):
        return "zh"
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    if not han or len(re.findall(r"[\u3040-\u30ff]", text)) * 5 > han:
        return "en"
    return "zh" if han >= len(re.findall(r"[A-Za-z]{2,}", text)) else "en"


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
    unknown, numeric, links, malformed = set(), 0, 0, []

    def scan(line):
        nonlocal numeric, links
        for match in MARKER.finditer(line):
            unknown.update(eid for eid in MARKER_ID.findall(match.group(1)) if eid not in eligible)
        prose = MARKER.sub("", line)
        malformed.extend(match.group(0)[:40] for match in LOOSE_MARKER.finditer(prose))
        prose = LOOSE_MARKER.sub("", prose)
        numeric += len(NUMERIC.findall(prose))
        links += len(LINK.findall(prose)) + len(BARE_URL.findall(LINK.sub("", prose))) + bool(REFERENCE_DEFINITION.match(prose))
        return line

    _map_lines(text, scan)
    errors = []
    if malformed:
        errors.append(f"{len(malformed)} malformed evidence marker(s) such as {malformed[0]}. Write markers exactly as [[E012]] or [[E012, E031]], with IDs copied from `evidence`.")
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
    # A clause after a semicolon shares the sentence's citation, so removing
    # only the cited half would leave the other half as an uncited fact.
    parts = re.split(r"(?<=[。！？!?])|(?<=\.)(?<!e\.g\.)(?<!i\.e\.)(?<!\bvs\.)(?<!etc\.)(?<!\bNo\.)(?<!\bInc\.)(?<!\bLtd\.)(?<!\bCo\.)(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bFig\.)(?=\s+[A-Z\u4e00-\u9fff*\[【])", text)
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
        if any(eid not in eligible for match in MARKER.finditer(fragment) for eid in MARKER_ID.findall(match.group(1))):
            return True
        # Something shaped like a citation that names no verifiable ID.
        return bool(LOOSE_MARKER.search(MARKER.sub("", fragment)))

    def clean(line):
        if REFERENCE_DEFINITION.match(line):
            audit["stripped_links"] += 1
            return None
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
        if body.count("**") % 2:
            # The removed sentence held one half of a bold span.
            body = body.replace("**", "", 1).lstrip()
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
            joined = " ".join(paragraph)
            # A cited paragraph is reader content even when its subject is
            # evidence numbering (forensics, legal discovery); only uncited
            # bookkeeping about how the text was produced goes.
            if paragraph and not (not BLOCK_START.match(paragraph[0]) and PROCESS_NOTE.search(joined) and not MARKER.search(joined)):
                output.extend(paragraph)
            paragraph = []
            output.append(line)
        output.pop()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def close_fences(text):
    """(text, repaired): end a code fence the writer left open.

    An answer cut off at the output cap inside ```mermaid would otherwise turn
    every later section of the assembled report into code: headings, markers
    and the scope section all disappeared and the report still validated. A
    cut-off diagram cannot render, so it is dropped; other code is closed.
    """
    segments = _segments(text)
    if not segments or not segments[-1][0]:
        return text, False
    lines = segments[-1][1]
    opening = FENCE.match(lines[0])
    closed = len(lines) > 1 and lines[-1].strip() and set(lines[-1].strip()) == {opening.group(1)[0]} and len(lines[-1].strip()) >= len(opening.group(1))
    if closed:
        return text, False
    kept = [line for code, body in segments[:-1] for line in body]
    if not re.match(r"^\s{0,3}[`~]{3,}\s*mermaid\b", lines[0], re.I):
        kept += [*lines, opening.group(1)]
    return "\n".join(kept).rstrip(), True


def normalize_markers(text):
    """Rewrite markers that differ from the contract only in separators or case.

    ``[[e001]]``, ``[[E001 E002]]``, ``[[E001-E002]]``, ``【E001、E002】`` and
    ``（E001）`` name real IDs; the IDs are still validated afterwards. A range is
    never expanded and nothing is guessed: a marker without a three-digit ID
    stays as it is and is reported as malformed.
    """

    def canonical(match):
        if MARKER.fullmatch(match.group(0)):
            return match.group(0)
        ids = [eid.upper() for eid in re.findall(r"[Ee]\d{3,}", match.group(0))]
        inner = re.sub(r"[Ee]\d+|[\s,，、;；和与及&\-–~至到\[\]【】（）()]", "", match.group(0))
        return "[[" + ", ".join(dict.fromkeys(ids)) + "]]" if ids and not inner else match.group(0)

    return _map_lines(text, lambda line: LOOSE_MARKER.sub(canonical, line))


def plain_text(value, limit=None):
    """A model-written label without links, URLs or citations of any kind.

    Titles, key conclusions, assumptions and limitations are assembled into the
    report as they are. A caveat such as "could not open https://..." or a
    stray "[1]" used to fail final validation and rewrite the whole report.
    """
    text = CONTROL.sub("", " ".join(str(value or "").split()))
    text = LINK.sub(lambda match: match.group(1), text)
    text = BARE_URL.sub("", text)
    text = LOOSE_MARKER.sub("", MARKER.sub("", text))
    text = NUMERIC.sub("", text)
    text = re.sub(r"\s+([，。；：、,.;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()
    return text[:limit] if limit else text


def clean_answer(text, heading=None, *, whole_document=False):
    """Normalize a writer's Markdown without touching its research statements.

    A narrated first line and prose notes about the writing pipeline are
    dropped. A section body loses a repeated heading and has its top-level
    headings demoted; a whole revised document keeps its own heading structure.
    """
    text = CONTROL.sub("", visible_text(text or "")).strip()
    first, _, rest = text.partition("\n")
    if "[[" not in first and PREAMBLE.match(first.strip()):
        text = rest.strip()
    wrapped = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*?)\n```", text, re.S)
    if wrapped:
        text = wrapped.group(1).strip()
    text = normalize_markers(close_fences(text)[0])
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
            # A single-page application routes in its fragment ("#/doc/5"): those are different pages.
            route = parts.fragment if parts.fragment.startswith(("/", "!")) else ""
            return (evidence.get("origin"), host + port, parts.path.rstrip("/") or "/", parts.query, route)
    if evidence.get("document_hash"):
        # The same knowledge-base passage returned by several queries is one reference.
        return (evidence.get("origin"), evidence.get("source_name"), evidence["document_hash"])
    return (evidence.get("origin"), evidence.get("source_name"), evidence.get("source_uri") or eid)


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


# The native fetch header is a block at the top; the same words inside the page are its text.
EXCERPT_HEADER = re.compile(r"\A(?:(?:Source|Title|Excerpt|Literal matches|No literal match found)[^\n]*\n)+")


SYNOPSIS = re.compile(r"\A\[Full [^\]]* output saved to [^\]]*\]\n.*?(?:Raw sample[^\n]*\n|\Z)", re.S)


MARKDOWN_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
MARKDOWN_LINK = re.compile(r"\[([^\]\n]*)\]\([^)\n]*\)")


def _prose(line):
    """How much of a line is running text rather than navigation or markup."""
    return len(re.sub(r"[\s#>*_`|\-=•·]+", "", line))


def excerpt(snippet, limit=2000):
    """Readable excerpt: drop tool envelopes (a host synopsis of an externalized
    result and the native fetch header lines) and keep the page text.

    A fetched page starts with its navigation: logo links, menus, cookie
    notices. Readers saw that as the "quote" of a citation. Link and image
    syntax is reduced to its text, and the excerpt starts at the first line
    that reads like a sentence; a page without one is kept from its start.
    """
    text = SYNOPSIS.sub("", snippet or "")
    text = EXCERPT_HEADER.sub("", text)
    text = re.sub(r"\n\[(?:More content|End of extracted page|End of document)[^\]]*\]\s*$", "", text).strip()
    text = MARKDOWN_LINK.sub(lambda match: match.group(1), MARKDOWN_IMAGE.sub("", text))
    lines = [line.rstrip() for line in text.split("\n")]
    start = next((index for index, line in enumerate(lines) if _prose(line) >= 60 and len(line.split()) + len(re.findall(r"[\u4e00-\u9fff]", line)) >= 12), 0)
    # Keep the heading right above the first sentence: it says what the passage is about.
    above = next((index for index in range(start - 1, max(-1, start - 4), -1) if lines[index].strip()), None)
    if above is not None and lines[above].lstrip().startswith("#"):
        start = above
    body = "\n".join(lines[start:]).strip()
    return re.sub(r"\n{3,}", "\n\n", body)[:limit]


def evidence_basis(evidence, roles=None):
    """What a citation rests on: an opened original, a returned record or a search excerpt.

    ``roles`` maps source names to their operator-declared role.
    """
    provenance = evidence.get("provenance", "document")
    if provenance in {"document", "fetched_document"}:
        return "page"
    return "search excerpt" if provenance == "observed_source" or (roles or {}).get(evidence.get("source_name")) == "search" else "record"


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


BASIS_NOTE = {"zh": "检索摘录，未读取原文", "en": "search excerpt; the original was not opened"}


def references(mapping, pool, roles=None):
    values = citation_metadata(mapping, pool)
    for item in values:
        item["title"] = _display_title(item, pool)
        # A reader must be able to tell an opened original from the excerpt a
        # search tool showed: the strongest basis in the group counts.
        kinds = {evidence_basis(pool[eid], roles) for eid in item["evidence_ids"]}
        item["basis"] = next(kind for kind in ("page", "record", "search excerpt") if kind in kinds)
    return values


def _locator(ref):
    """What stands in for a link when a record has none: where it came from, never an internal URI."""
    return ref.get("source_name") or ref.get("publisher") or ""


def citations(mapping, pool, roles=None):
    values = references(mapping, pool, roles)
    for item in values:
        item["domain"] = (urlsplit(item.get("url") or item.get("canonical_url") or "").hostname or "").removeprefix("www.")
        item["snippet"] = excerpt(item.get("snippet"), 1200)
        for part in item["excerpts"]:
            part["text"] = excerpt(pool[part["evidence_id"]].get("snippet"))
    return values


def _reference_lines(mapping, pool, lang, roles=None):
    lines = [f"## {LABELS[lang]['references']}", ""]
    for ref in references(mapping, pool, roles):
        target = ref.get("url") or ref.get("canonical_url")
        title = ref["title"].replace("[", "(").replace("]", ")")
        locator = f"[{title}](<{quote(target, safe=':/?=&%#@+;,~-._')}>)" if target else f"{title} — {_locator(ref)}".rstrip(" —")
        date = f" · {str(ref['published_at'])[:10]}" if ref.get("published_at") else ""
        note = f"（{BASIS_NOTE[lang]}）" if ref["basis"] == "search excerpt" and lang == "zh" else f" ({BASIS_NOTE[lang]})" if ref["basis"] == "search excerpt" else ""
        lines += [f'<a id="ref-{ref["number"]}"></a>', f"{ref['number']}. {locator}{date}{note}", ""]
    return lines


def export_markdown(document, mapping, pool, lang="zh", demo=False, roles=None):
    body = _map_lines(document, lambda line: _literal_dollars(_replace_markers(line, lambda ids: "".join(f"[{number}](#ref-{number})" for number in _numbers(ids, mapping)))))
    lines = body.rstrip().split("\n")
    if demo:
        lines[1:1] = ["", f"> {LABELS[lang]['demo']}"]
    return "\n".join([*lines, "", *_reference_lines(mapping, pool, lang, roles)]).rstrip() + "\n"


def _markdown_parser():
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])


def html_document(document, mapping, pool, lang="zh", demo=False, roles=None):
    body = _map_lines(document, lambda line: _replace_markers(line, lambda ids: "\u27e6" + ",".join(str(number) for number in _numbers(ids, mapping)) + "\u27e7"))
    rendered = _markdown_parser().render(body)
    rendered = re.sub(r"\u27e6([\d,]+)\u27e7", lambda m: "".join(f'<sup><a href="#ref-{n}">[{n}]</a></sup>' for n in m.group(1).split(",")), rendered)
    esc = html.escape
    items = []
    for ref in references(mapping, pool, roles):
        label = esc(ref["title"])
        target = ref.get("url") or ref.get("canonical_url")
        label = f'<a rel="noreferrer noopener" href="{esc(target, quote=True)}">{label}</a>' if target else (label + " — " + esc(_locator(ref))).rstrip(" —")
        if ref["basis"] == "search excerpt":
            label += f" <small>({esc(BASIS_NOTE[lang])})</small>"
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


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# A diagram becomes a picture in a document people publish, so only real PNG
# bytes within a bound are embedded, and a report holds a bounded number.
MAX_DIAGRAM_BYTES = 4 * 1024 * 1024
MAX_DIAGRAMS = 60
TEXT_WIDTH_INCHES = 6.5
# Letter height less the margins, less room for the caption under the picture.
TEXT_HEIGHT_INCHES = 8.4
# Below this share of the column a fitted diagram is a ribbon of unreadable
# labels, and dropping it into the text strands most of the page before it.
NARROW_DIAGRAM = 0.55
CODE_FILL = "F5F5F5"
CITATION_GROUP = re.compile(r"⟦([\d,]+)⟧")


def diagram_key(source: str) -> str:
    """The name a rendered diagram is filed under: its own source text.

    The browser renders the Mermaid blocks of the document it is showing and
    sends the pictures back for export. Keying by the source rather than by
    position means a picture can only land under the diagram it was drawn
    from, and needs nothing from the browser: Web Crypto, which a hash would
    need, is unavailable on the plain-HTTP intranet origins this has to run on.
    Whitespace is collapsed because the two sides reach the same source through
    different parsers, and a diagram that differs only in spacing draws the same.
    """
    return " ".join(source.split())


def _png_pixels(data):
    """``(width, height)`` for real PNG bytes within the size bound, else None."""
    if not isinstance(data, bytes) or len(data) <= 24 or len(data) > MAX_DIAGRAM_BYTES:
        return None
    if not data.startswith(PNG_MAGIC) or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if 0 < width <= 20000 and 0 < height <= 20000 else None


def docx_document(value, diagrams=None):
    """Word export of the same verified Markdown document.

    ``diagrams`` maps :func:`diagram_key` to the PNG a browser rendered for that
    Mermaid block. A diagram with no usable picture keeps its caption and moves
    its source to an appendix rather than printing code in the middle of the
    prose. Citations become superscript links to the reference list, and every
    reference carries its site, the date the source states and whether the
    original was opened, exactly as the Markdown and HTML exports do.
    """
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    lang = value.get("language") or language(value.get("document", ""))
    labels = LABELS[lang]
    mapping = value["citation_map"]
    pictures = {key: data for key, data in (diagrams or {}).items() if _png_pixels(data)}
    body = _map_lines(value["document"], lambda line: _replace_markers(line, lambda ids: "⟦" + ",".join(str(number) for number in _numbers(ids, mapping)) + "⟧"))
    tokens = _markdown_parser().parse(body)

    doc = Document()
    for section in doc.sections:
        section.left_margin = section.right_margin = Inches(1.0)
        section.top_margin = section.bottom_margin = Inches(1.0)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.2
    # Latin and Chinese are chosen separately; without the East Asian face a
    # Chinese heading falls back to whatever Word picks for Calibri.
    for name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        try:
            style = doc.styles[name]
        except KeyError:
            continue
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Microsoft YaHei")

    bookmarks = iter(range(1, 1_000_000))

    def bookmark(paragraph, name):
        ident = str(next(bookmarks))
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), ident)
        start.set(qn("w:name"), name)
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), ident)
        paragraph._p.insert(0, start)
        paragraph._p.append(end)

    def link(paragraph, text, *, url=None, anchor=None, superscript=False):
        run = paragraph.add_run(text)
        run.font.color.rgb = RGBColor(0x0B, 0x57, 0xD0)
        run.font.underline = not superscript
        if superscript:
            run.font.superscript = True
        wrapper = OxmlElement("w:hyperlink")
        if url:
            wrapper.set(qn("r:id"), paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True))
        if anchor:
            wrapper.set(qn("w:anchor"), anchor)
        run._r.addprevious(wrapper)
        wrapper.append(run._r)
        return run

    def rules(grid):
        """Three rules and no boxes: the shape a printed report uses.

        A full grid with a shaded header is loud on paper, and where Word has
        to break the table the boxes make the seam the loudest thing on the page.
        """
        borders = OxmlElement("w:tblBorders")
        for edge, width in (("top", 8), ("bottom", 8), ("insideH", 0), ("insideV", 0), ("left", 0), ("right", 0)):
            line = OxmlElement(f"w:{edge}")
            line.set(qn("w:val"), "single" if width else "none")
            line.set(qn("w:sz"), str(width))
            line.set(qn("w:color"), "000000")
            borders.append(line)
        grid._tbl.tblPr.append(borders)

    def rule_under(row):
        """The line that separates the header from the body."""
        for cell in row.cells:
            borders = OxmlElement("w:tcBorders")
            line = OxmlElement("w:bottom")
            line.set(qn("w:val"), "single")
            line.set(qn("w:sz"), "6")
            line.set(qn("w:color"), "000000")
            borders.append(line)
            cell._tc.get_or_add_tcPr().append(borders)

    def shade(element, fill):
        shading = OxmlElement("w:shd")
        shading.set(qn("w:val"), "clear")
        shading.set(qn("w:color"), "auto")
        shading.set(qn("w:fill"), fill)
        element.append(shading)

    def citations_in(paragraph, text, bold, italic, mono):
        """Write text, turning each citation group into superscript links."""
        position = 0
        for match in CITATION_GROUP.finditer(text):
            if match.start() > position:
                run = paragraph.add_run(text[position : match.start()])
                run.bold, run.italic = bold, italic
                if mono:
                    run.font.name = "Consolas"
            for number in match.group(1).split(","):
                link(paragraph, f"[{number}]", anchor=f"ref-{number}", superscript=True)
            position = match.end()
        if position < len(text):
            run = paragraph.add_run(text[position:])
            run.bold, run.italic = bold, italic
            if mono:
                run.font.name = "Consolas"

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
                citations_in(paragraph, child.content, bold, italic, child.type == "code_inline")
            elif child.type in {"softbreak", "hardbreak"}:
                paragraph.add_run(" " if child.type == "softbreak" else "\n")

    def code_block(content, *, size=9):
        paragraph = doc.add_paragraph()
        shade(paragraph._p.get_or_add_pPr(), CODE_FILL)
        paragraph.paragraph_format.left_indent = Pt(12)
        paragraph.paragraph_format.space_before = Pt(6)
        run = paragraph.add_run(content.rstrip())
        run.font.name = "Consolas"
        run.font.size = Pt(size)
        return paragraph

    def caption(text):
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = paragraph.add_run(text)
        run.italic = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0x5F, 0x63, 0x68)
        return paragraph

    def picture(data):
        width, height = _png_pixels(data)
        # A top-down flowchart is far taller than it is wide. Fitting only the
        # column leaves a picture several pages long, which Word clips at the
        # first page break, so the page height bounds it too.
        inches = min(TEXT_WIDTH_INCHES, max(width / 96, 0.02), TEXT_HEIGHT_INCHES * width / height)
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        # A diagram the page can only hold as a narrow ribbon would otherwise be
        # dropped into the prose, pushing itself to the next page and leaving
        # most of the previous one blank. Starting its own page costs the same
        # space and reads as a figure rather than as a gap.
        if inches < TEXT_WIDTH_INCHES * NARROW_DIAGRAM:
            paragraph.paragraph_format.page_break_before = True
        # The caption follows in its own paragraph; it must not land alone.
        paragraph.paragraph_format.keep_with_next = True
        paragraph.add_run().add_picture(BytesIO(data), width=Inches(inches))

    # Headings are bookmarked before the body is written so the contents list
    # can link to them; only levels 2 and 3 appear, as in the Markdown outline.
    outline, anchors, order = [], {}, iter(range(1, 1_000_000))
    for position, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag in {"h2", "h3"}:
            anchor = f"heading-{next(order)}"
            anchors[position] = anchor
            outline.append((int(token.tag[1]), visible_text(tokens[position + 1].content).strip(), anchor))

    doc.add_heading(title_of(value.get("document", "")) or value.get("title", ""), 0)
    stats = value.get("stats") or {}
    subtitle = " · ".join(part for part in [str(stats.get("citations") or len(value.get("citations") or [])) + (" 条引用" if lang == "zh" else " references")] if part)
    if subtitle:
        caption(subtitle)
    if value.get("demo"):
        notice = doc.add_paragraph()
        shade(notice._p.get_or_add_pPr(), "FFF4E5")
        run = notice.add_run(labels["demo"])
        run.bold = True
    if outline:
        doc.add_heading(labels["contents"], 1)
        for level, text, anchor in outline:
            entry = doc.add_paragraph()
            entry.paragraph_format.left_indent = Pt(0 if level == 2 else 18)
            entry.paragraph_format.space_after = Pt(2)
            link(entry, text, anchor=anchor)

    unrendered, figures = [], iter(range(1, 1_000_000))
    lists, table, row, index = [], None, None, 0
    while index < len(tokens):
        token = tokens[index]
        kind = token.type
        if kind == "heading_open":
            level = int(token.tag[1])
            heading = doc.add_heading(visible_text(tokens[index + 1].content).strip(), 0 if level == 1 else min(level - 1, 3))
            if index in anchors:
                bookmark(heading, anchors[index])
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
            row.append(tokens[index + 1] if tokens[index + 1].type == "inline" else None)
        elif kind == "tr_close":
            table.append(row)
        elif kind == "table_close":
            columns = max((len(entry) for entry in table), default=0)
            if columns:
                grid = doc.add_table(rows=0, cols=columns)
                grid.style = "Table Grid"
                grid.alignment = WD_TABLE_ALIGNMENT.CENTER
                # Word's autofit gives one column most of the width and squeezes
                # the rest into a vertical ribbon of single characters.
                grid.autofit = False
                layout = OxmlElement("w:tblLayout")
                layout.set(qn("w:type"), "fixed")
                grid._tbl.tblPr.append(layout)
                rules(grid)
                share = Inches(TEXT_WIDTH_INCHES / columns)
                for number, values in enumerate(table):
                    row = grid.add_row()
                    # A row Word is allowed to split leaves one line of a cell
                    # stranded on the next page under the repeated header.
                    keep = OxmlElement("w:cantSplit")
                    row._tr.get_or_add_trPr().append(keep)
                    for cell, inline in zip(row.cells, values + [None] * (columns - len(values)), strict=True):
                        cell.width = share
                        paragraph = cell.paragraphs[0]
                        if inline is not None:
                            runs(paragraph, inline)
                        if number == 0:
                            for run in paragraph.runs:
                                run.bold = True
                    if number == 0:
                        repeat = OxmlElement("w:tblHeader")
                        repeat.set(qn("w:val"), "true")
                        row._tr.get_or_add_trPr().append(repeat)
                        rule_under(row)
            table = None
        elif kind == "inline" and table is None and tokens[index - 1].type == "paragraph_open":
            style = lists[-1] if lists else None
            paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
            runs(paragraph, token)
        elif kind in {"fence", "code_block"}:
            info = (token.info or "").strip().split(" ")[0].lower()
            if info == "mermaid":
                source = token.content.strip()
                number = next(figures)
                data = pictures.get(diagram_key(source)) if number <= MAX_DIAGRAMS else None
                if data:
                    picture(data)
                    caption(f"{labels['figure']} {number}")
                else:
                    caption(f"{labels['figure']} {number}（{labels['unrendered']}）" if lang == "zh" else f"{labels['figure']} {number} ({labels['unrendered']})")
                    unrendered.append((number, source))
            else:
                code_block(token.content)
        index += 1

    doc.add_heading(labels["references"], 1)
    for ref in value.get("citations") or []:
        entry = doc.add_paragraph()
        entry.paragraph_format.space_after = Pt(4)
        bookmark(entry, f"ref-{ref['number']}")
        entry.add_run(f"{ref['number']}. ")
        target = ref.get("url") or ref.get("canonical_url")
        title = ref.get("title") or target or ""
        if target:
            link(entry, title, url=target)
        else:
            locator = _locator(ref)
            entry.add_run(f"{title} — {locator}".rstrip(" —"))
        trailing = [part for part in (ref.get("domain"), str(ref.get("published_at"))[:10] if ref.get("published_at") else None) if part]
        if trailing:
            note = entry.add_run(" · " + " · ".join(trailing))
            note.font.color.rgb = RGBColor(0x5F, 0x63, 0x68)
            note.font.size = Pt(9)
        if ref.get("basis") == "search excerpt":
            mark = entry.add_run(f"（{BASIS_NOTE[lang]}）" if lang == "zh" else f" ({BASIS_NOTE[lang]})")
            mark.font.color.rgb = RGBColor(0x5F, 0x63, 0x68)
            mark.font.size = Pt(9)

    if unrendered:
        doc.add_heading(labels["appendix"], 1)
        for number, source in unrendered:
            caption(f"{labels['figure']} {number}")
            code_block(source)

    footer = doc.sections[0].footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run()
    for kind, content in (("begin", None), (None, " PAGE "), ("end", None)):
        if kind:
            element = OxmlElement("w:fldChar")
            element.set(qn("w:fldCharType"), kind)
        else:
            element = OxmlElement("w:instrText")
            element.set(qn("xml:space"), "preserve")
            element.text = content
        run._r.append(element)

    doc.core_properties.author = "DeepResearch"
    doc.core_properties.title = title_of(value.get("document", ""))[:255]
    out = BytesIO()
    doc.save(out)
    return out.getvalue()
