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
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

URL_KEYS = ("url", "link", "href", "uri", "source_url", "sourceUrl", "sourceURL", "page_url", "pageUrl", "web_url", "webUrl", "doc_url", "document_url", "website", "permalink")
TITLE_KEYS = ("title", "name", "headline", "document_name", "doc_name", "docnm_kwd", "document_keyword", "file_name", "filename", "source_title")
TEXT_KEYS = ("snippet", "summary", "description", "desc", "content", "text", "body", "abstract", "excerpt", "passage", "highlight", "highlights", "raw_content", "page_content", "markdown", "chunk", "content_with_weight", "answer")
DOCUMENT_TEXT_KEYS = ("markdown", "raw_content", "content", "text", "body", "html_content", "page_content")
DATE_KEYS = (
    "published_at",
    "publishedAt",
    "publishedTime",
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
# A site icon a source states for its own page. Read as decoration: never a
# result's address, never text, and never a source that was seen.
# Bounds for walking an answer: nested JSON strings are parsed, but only a few
# and only while they are small enough to be an answer rather than a document.
MAX_NESTED_JSON = 40
MAX_SCAN = 1_000_000
ICON_KEYS = ("logo_url", "logo", "icon_url", "icon", "favicon", "favicon_url", "site_icon", "site_logo", "logoUrl", "iconUrl", "siteIcon")
ID_KEYS = ("id", "document_id", "doc_id", "chunk_id", "_id")
MARKDOWN_LINK = re.compile(r"\[([^\[\]\n]{1,300})\]\((https?://[^\s)]+)\)")
TITLE_LINE = re.compile(r"(?im)^\s*(?:title|标题)\s*[:：]\s*(.+)$")
URL_LINE = re.compile(r"(?im)^\s*(?:url|link|source|链接|网址)\s*[:：]\s*(https?://\S+)")
HEADING = re.compile(r"(?m)^\s{0,3}#{1,2}\s+(.+?)\s*#*\s*$")
HTML_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


# A publication date is only ever the source's own claim, so it is read where a
# source states it and never computed. Values outside this window are somebody's
# placeholder (0000-00-00, an epoch zero, a year in the far future), not a date.
EARLIEST_YEAR = 1990
AHEAD_DAYS = 400
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
NUMERIC_DATE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
NAMED_DATE = re.compile(r"(?i)([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})")
DAY_FIRST_DATE = re.compile(r"(?i)(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})")


def published(value):
    """The date a source says its page carries, or None.

    Providers state it in whatever shape they like — ISO, ``Apr 21, 2026``,
    ``2026年7月10日``, epoch seconds — and plenty state something unusable
    (``3 天前``, ``recently``). An unusable value must cost the date and nothing
    else: the evidence it belongs to is still evidence, so this never raises and
    never guesses a date from a relative phrase.
    """

    def bounded(moment):
        moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return moment if EARLIEST_YEAR <= moment.year and moment <= now + timedelta(days=AHEAD_DAYS) else None

    if isinstance(value, datetime):
        return bounded(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Seconds or milliseconds; anything else is an id that happens to be a number.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return bounded(datetime.fromtimestamp(seconds, UTC))
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return bounded(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    numeric = NUMERIC_DATE.search(text)
    if numeric:
        year, month, day = (int(part) for part in numeric.groups())
        try:
            return bounded(datetime(year, month, day, tzinfo=UTC))
        except ValueError:
            return None
    for pattern, order in ((NAMED_DATE, (2, 0, 1)), (DAY_FIRST_DATE, (2, 1, 0))):
        found = pattern.search(text)
        if not found:
            continue
        parts = found.groups()
        name = parts[order[1]][:3].lower()
        if name not in MONTHS:
            continue
        try:
            return bounded(datetime(int(parts[order[0]]), MONTHS.index(name) + 1, int(parts[order[2]]), tzinfo=UTC))
        except ValueError:
            return None
    return None


# Where an HTML page states its date. Every CMS writes at least one of these;
# the prose around it ("更新于上周") is never one, so it is not read.
DATE_ATTRIBUTES = "article:published_time|article:modified_time|datepublished|datemodified|dc\\.date[\\w.]*|date|pubdate|publish[-_]?date|og:published_time|parsely-pub-date|sailthru\\.date"
PAGE_DATE = re.compile(
    rf"""<meta[^>]+(?:property|name|itemprop)\s*=\s*["']?(?:{DATE_ATTRIBUTES})["']?[^>]*?content\s*=\s*["']([^"']{{4,64}})["']"""
    rf"""|<meta[^>]+content\s*=\s*["']([^"']{{4,64}})["'][^>]*?(?:property|name|itemprop)\s*=\s*["']?(?:{DATE_ATTRIBUTES})["']"""
    r"""|<time[^>]+datetime\s*=\s*["']([^"']{4,64})["']"""
    r"""|["'](?:datePublished|dateCreated)["']\s*:\s*["']([^"']{4,64})["']""",
    re.IGNORECASE | re.DOTALL,
)


def page_date(html):
    """The date an HTML page states about itself, as the page wrote it, or None.

    Read from metadata only — a CMS writes it there, while a date in the prose
    belongs to what the page is about, not to the page.
    """
    if not isinstance(html, str) or "<" not in html:
        return None
    for found in PAGE_DATE.finditer(html[:200000]):
        value = next((group for group in found.groups() if group), None)
        if value and published(value):
            return value.strip()
    return None


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


WRAPPER_KEYS = ("result", "output", "data", "content", "text", "response")


def unwrap(value):
    """The answer inside what servers and adapters wrap it in.

    A tool that returns a string reaches us as ``{"result": "<that string>"}``
    (MCP structured content), and the string is often JSON itself. Found in a
    real run: the page's title and text were two levels down and never seen.
    """
    for _ in range(4):
        value = parse_json(value)
        if not (isinstance(value, dict) and len(value) == 1):
            break
        ((key, inner),) = value.items()
        if not (isinstance(inner, str) and key in WRAPPER_KEYS):
            break
        value = inner
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
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _weight(text):
    """Length in Latin characters: a Chinese sentence of 25 characters says as much as 50 of English."""
    return len(text) + len(CJK.findall(text))


def _headline(text, limit=80):
    """A title for an untitled chunk: its first line, cut at a word or clause."""
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    line = re.sub(r"^[#>*\-\s]+", "", line)
    if len(line) <= limit:
        return line
    cut = max(line.rfind(mark, 0, limit) for mark in (" ", "，", "。", "；", ",", ";"))
    return line[: cut if cut > limit // 2 else limit].rstrip() + "…"


def _host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.").lower()


def _record(item, text_limit, *, chunk=False):
    url = http_url(_first(item, URL_KEYS))
    title = _first(item, TITLE_KEYS)
    text = _first(item, TEXT_KEYS)
    if chunk and not title and not url and text and _weight(text) >= MIN_CHUNK_CHARS:
        title = _headline(html.unescape(text))
    if not ((url and (title or text)) or (title and text)):
        return None
    record = {"title": html.unescape(title or url or "")[:500], "url": url, "snippet": html.unescape(text or "")[:text_limit]}
    published = _first(item, DATE_KEYS)
    if published:
        record["published_at"] = published[:64]
    # Only for its own site: an icon claimed for another host would make the
    # gateway fetch, cache and show a picture chosen by somebody else.
    icon = http_url(_first(item, ICON_KEYS))
    if icon and url and _host(icon) == _host(url):
        record["icon_url"] = icon
    # Internal tools name their identifier after the record: ticket_id, docId, article_id.
    identifier = item.get(next((key for key in ID_KEYS if key in item), None) or next((key for key in item if re.search(r"(?:_id|Id|ID)$", key)), ""), None)
    if isinstance(identifier, (str, int)) and str(identifier).strip():
        record["id"] = str(identifier)[:200]
    return record


def records(value, *, limit=50, text_limit=1200, from_text=True):
    """Result records from any JSON shape, Markdown links or labeled text blocks.

    ``from_text=False`` keeps to structured answers: links in running text are
    places a document mentions, not records it returns.
    """
    value = unwrap(value)
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
    if found or not from_text:
        return found[:limit]
    text = content_text(value)
    return text_records(text, limit=limit, text_limit=text_limit)


def declared_icons(value, *, limit=4000):
    """``{host: icon URL}`` for objects that state an icon for their own address.

    A site icon is worth showing next to the sites a report cites, and only the
    source knows where an internal wiki keeps its logo. It is read from the same
    object that carries the address, and only when both name the same host: an
    icon claimed for somebody else's site would have the gateway fetch, cache
    and show a picture that site never chose.
    """
    icons = {}
    pending = [value]
    seen = parsed = 0
    while pending and seen < limit:
        item = pending.pop()
        seen += 1
        if isinstance(item, str):
            # An adapter hands back the server's answer as a string, inside a
            # content block or under structured_content, sometimes twice over.
            if parsed < MAX_NESTED_JSON and item.lstrip()[:1] in "[{" and len(item) <= MAX_SCAN:
                parsed += 1
                nested = parse_json(item)
                if not isinstance(nested, str):
                    pending.append(nested)
            continue
        if isinstance(item, dict):
            icon = http_url(_first(item, ICON_KEYS))
            address = http_url(_first(item, URL_KEYS))
            if icon and address and _host(icon) == _host(address):
                icons.setdefault(_host(address), icon)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return icons


def coverage(value, found):
    """How much of a structured answer's text its records carry, 0 to 1.

    Records are recognised by shape. When they hold nearly everything the
    answer said, the answer as a whole adds nothing to cite; when a shape was
    read only in part, or long texts were cut, the whole answer still matters.
    """
    total = 0
    pending = [unwrap(value)]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, str):
            total += len(item.strip())
    kept = sum(len(record.get("snippet") or "") + len(record.get("url") or "") + len(record.get("icon_url") or "") + (len(record.get("title") or "") if record.get("title") not in (record.get("snippet") or "") else 0) for record in found)
    return min(1.0, kept / total) if total else 0.0


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
    value = unwrap(value)
    title = url = None
    text = None
    readable = True  # False when no field held the text and the whole answer stands in for it
    if isinstance(value, dict):
        # Common envelopes: {"data": {...}}, {"results": [{...}]}.
        inner = value
        for key in ("data", "result", "document", "page"):
            nested = parse_json(inner.get(key))
            if isinstance(nested, dict):
                inner = nested
        if isinstance(inner.get("results"), list) and inner["results"] and isinstance(inner["results"][0], dict):
            inner = inner["results"][0]
        texts = [inner[key] for key in DOCUMENT_TEXT_KEYS if isinstance(inner.get(key), str) and inner[key].strip()]
        text = max(texts, key=len) if texts else None
        metadata = inner.get("metadata") if isinstance(inner.get("metadata"), dict) else {}
        title = _first(inner, TITLE_KEYS) or _first(metadata, TITLE_KEYS)
        url = http_url(_first(inner, URL_KEYS) or _first(metadata, URL_KEYS))
        date = _first(inner, DATE_KEYS) or _first(metadata, DATE_KEYS)
        if text is None:
            readable = False
            text = json.dumps(value, ensure_ascii=False, indent=1, default=str)
        date = date or page_date(text)
    else:
        text = content_text(value)
        date = page_date(text)
    text = text or ""
    if not title:
        line = TITLE_LINE.search(text[:2000]) or HEADING.search(text[:5000])
        title = line.group(1).strip() if line else None
    if not title:
        match = HTML_TITLE.search(text[:20000])
        title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip() if match else None
    return {"title": (title or url or requested_url or "")[:1000], "url": url or requested_url, "text": text, "readable": readable, "published_at": date}


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
