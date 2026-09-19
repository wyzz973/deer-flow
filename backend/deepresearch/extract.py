"""Find records and documents in tool results whose format is not fixed.

Search APIs, knowledge bases and MCP servers all return different shapes: JSON
with arbitrary field names, Markdown lists, "Title:/URL:" blocks or plain text.
Nothing here needs a schema. It looks for common structure (an object carrying
a URL or a title next to text), so results can be rendered consistently and a
read page keeps its address and title. The text a model cites always comes from
the provider's own response.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urlsplit

URL_KEYS = ("url", "link", "href", "uri", "source_url", "sourceUrl", "sourceURL", "page_url", "pageUrl", "website", "permalink")
TITLE_KEYS = ("title", "name", "headline", "document_name", "doc_name", "docnm_kwd", "document_keyword", "file_name", "filename", "source_title")
TEXT_KEYS = ("snippet", "summary", "description", "content", "text", "body", "abstract", "excerpt", "highlight", "highlights", "raw_content", "markdown", "chunk", "content_with_weight", "answer")
DOCUMENT_TEXT_KEYS = ("markdown", "raw_content", "content", "text", "body", "html_content", "page_content")
DATE_KEYS = (
    "published_at",
    "publishedAt",
    "publishedDate",
    "published_date",
    "datePublished",
    "date",
    "publish_date",
    "publish_time",
    "publishTime",
    "page_age",
    "age",
    "updated_at",
    "last_updated",
    "updated",
    "update_time",
    "updateTime",
    "modified_at",
    "created_at",
    "create_time",
)
ID_KEYS = ("id", "document_id", "doc_id", "chunk_id", "_id")
MARKDOWN_LINK = re.compile(r"\[([^\[\]\n]{1,300})\]\((https?://[^\s)]+)\)")
TITLE_LINE = re.compile(r"(?im)^\s*(?:title|标题)\s*[:：]\s*(.+)$")
URL_LINE = re.compile(r"(?im)^\s*(?:url|link|source|链接|网址)\s*[:：]\s*(https?://\S+)")
HEADING = re.compile(r"(?m)^\s{0,3}#{1,2}\s+(.+?)\s*#*\s*$")
HTML_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


def http_url(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    return value if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password else None


def _first(item, keys):
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value and all(isinstance(part, str) for part in value):
            return " … ".join(part.strip() for part in value if part.strip())
    return None


def parse_json(value):
    """JSON inside a string, including a fenced block; otherwise the value itself."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    fenced = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", text, re.S)
    if fenced:
        text = fenced.group(1)
    if text[:1] in "[{":
        try:
            return json.loads(text)
        except ValueError:
            return value
    return value


def content_text(content):
    """Text of a tool result: a string, LangChain/MCP content blocks or JSON."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, default=str)


# A chunk with text but neither title nor link is still a record when it sits
# in a list of such chunks (a knowledge-base tool returning {content, score,
# doc_id}). Shorter strings are labels or status fields, not content.
MIN_CHUNK_CHARS = 40


def _headline(text, limit=80):
    """A title for an untitled chunk: its first line, cut at a word or clause."""
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = re.sub(r"^[#>*\-\s]+", "", line)
    if len(line) <= limit:
        return line
    cut = max(line.rfind(mark, 0, limit) for mark in (" ", "，", "。", "；", ",", ";"))
    return line[: cut if cut > limit // 2 else limit].rstrip() + "…"


def _record(item, text_limit, *, chunk=False):
    url = http_url(_first(item, URL_KEYS))
    title = _first(item, TITLE_KEYS)
    text = _first(item, TEXT_KEYS)
    if chunk and not title and not url and text and len(text) >= MIN_CHUNK_CHARS:
        title = _headline(html.unescape(text))
    if not ((url and (title or text)) or (title and text)):
        return None
    record = {"title": html.unescape(title or url or "")[:500], "url": url, "snippet": html.unescape(text or "")[:text_limit]}
    published = _first(item, DATE_KEYS)
    if published:
        record["published_at"] = published[:64]
    # Internal tools name their identifier after the record: ticket_id, docId, article_id.
    identifier = item.get(next((key for key in ID_KEYS if key in item), None) or next((key for key in item if re.search(r"(?:_id|Id|ID)$", key)), ""), None)
    if isinstance(identifier, (str, int)) and str(identifier).strip():
        record["id"] = str(identifier)[:200]
    return record


def records(value, *, limit=50, text_limit=1200):
    """Result records from any JSON shape, Markdown links or labeled text blocks."""
    value = parse_json(value)
    found, seen = [], set()

    def add(record):
        key = record.get("url") or (record.get("title"), record.get("snippet", "")[:120])
        if key in seen:
            return
        seen.add(key)
        found.append(record)

    def holds_records(item):
        """Whether a dict wraps a result list, however record-like its own fields look."""
        for child in item.values():
            if isinstance(child, list) and any(isinstance(entry, dict) and _record(entry, text_limit, chunk=True) is not None for entry in child[:5]):
                return True
        return False

    def walk(item, depth=0, chunk=False):
        if len(found) >= limit or depth > 8:
            return
        if isinstance(item, dict):
            # An envelope such as {"name": "kb_search", "description": ..., "items": [...]}
            # looks like one record; its list is what was asked for.
            record = None if holds_records(item) else _record(item, text_limit, chunk=chunk)
            if record is not None:
                add(record)
                return
            for child in item.values():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
                elif isinstance(child, str) and child.strip()[:1] in "[{":
                    walk(parse_json(child), depth + 1)
        elif isinstance(item, list):
            # Entries of a list of objects are candidates even without a title or link.
            chunks = sum(isinstance(child, dict) for child in item) >= 1
            for child in item:
                walk(child, depth + 1, chunk=chunks)

    if isinstance(value, (dict, list)):
        walk(value)
    if found:
        return found[:limit]
    text = content_text(value)
    return text_records(text, limit=limit, text_limit=text_limit)


def text_records(text, *, limit=50, text_limit=1200):
    """Records from Markdown links or "Title: / URL:" blocks in plain text."""
    found = []
    blocks = re.split(r"\n\s*\n", text or "")
    for block in blocks:
        url_line = URL_LINE.search(block)
        title_line = TITLE_LINE.search(block)
        if url_line and http_url(url_line.group(1).rstrip(".,;)")):
            body = URL_LINE.sub("", TITLE_LINE.sub("", block)).strip()
            found.append({"title": (title_line.group(1).strip() if title_line else url_line.group(1))[:500], "url": url_line.group(1).rstrip(".,;)"), "snippet": " ".join(body.split())[:text_limit]})
    if not found:
        for match in MARKDOWN_LINK.finditer(text or ""):
            url = http_url(match.group(2).rstrip(".,;"))
            if url:
                tail = text[match.end() : match.end() + text_limit].split("\n\n")[0]
                found.append({"title": match.group(1).strip()[:500], "url": url, "snippet": " ".join(tail.split())[:text_limit]})
    unique, seen = [], set()
    for record in found:
        if record["url"] not in seen:
            seen.add(record["url"])
            unique.append(record)
    return unique[:limit]


def document(value, requested_url=None):
    """The readable text, title and address of one fetched document."""
    value = parse_json(value)
    title = url = None
    text = None
    if isinstance(value, dict):
        # Common envelopes: {"data": {...}}, {"results": [{...}]}.
        inner = value
        for key in ("data", "result", "document", "page"):
            if isinstance(inner.get(key), dict):
                inner = inner[key]
        if isinstance(inner.get("results"), list) and inner["results"] and isinstance(inner["results"][0], dict):
            inner = inner["results"][0]
        texts = [inner[key] for key in DOCUMENT_TEXT_KEYS if isinstance(inner.get(key), str) and inner[key].strip()]
        text = max(texts, key=len) if texts else None
        metadata = inner.get("metadata") if isinstance(inner.get("metadata"), dict) else {}
        title = _first(inner, TITLE_KEYS) or _first(metadata, TITLE_KEYS)
        url = http_url(_first(inner, URL_KEYS) or _first(metadata, URL_KEYS))
        if text is None:
            text = json.dumps(value, ensure_ascii=False, indent=1, default=str)
    else:
        text = content_text(value)
    text = text or ""
    if not title:
        line = TITLE_LINE.search(text[:2000]) or HEADING.search(text[:5000])
        title = line.group(1).strip() if line else None
    if not title:
        match = HTML_TITLE.search(text[:20000])
        title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip() if match else None
    return {"title": (title or url or requested_url or "")[:1000], "url": url or requested_url, "text": text}


def render_search(query, found, provider, *, citable=False):
    if not found:
        return f'No results for "{query}" (via {provider}).'
    note = "These excerpts are the evidence; rely only on what they say." if citable else "Snippets are for discovery; open a page before relying on it."
    lines = [f'Search results for "{query}" ({len(found)} via {provider}). {note}', ""]
    for index, record in enumerate(found, 1):
        label = record["title"].replace("[", "(").replace("]", ")") or record["url"]
        lines.append(f"{index}. [{label}]({record['url']})" if record.get("url") else f"{index}. {label}")
        if record.get("published_at"):
            lines.append(f"   Published: {record['published_at']}")
        if record.get("snippet"):
            lines.append("   " + " ".join(record["snippet"].split()))
    return "\n".join(lines)


def render_records(source, found, provider):
    if not found:
        return f"No matching records in {source} (via {provider})."
    lines = [f"{len(found)} records from {source} (via {provider}).", ""]
    for index, record in enumerate(found, 1):
        heading = f"[{index}] {record['title']}"
        if record.get("url"):
            heading += f" ({record['url']})"
        lines.append(heading)
        if record.get("published_at"):
            lines.append(f"Date: {record['published_at']}")
        lines.append(record.get("snippet") or "")
        lines.append("")
    return "\n".join(lines).rstrip()
