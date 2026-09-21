"""Side-channel source discovery from native tool messages.

This never changes a tool schema or the response delivered to the model. URLs
and Markdown link labels are observed as text, without assuming MCP business
field names. A discovered URL is not proof that its document was fetched or
that it supports a claim; those distinctions stay visible to clients.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urlsplit

from . import extract
from .contracts import safe_http_url
from .evidence import canonical_url, digest

MAX_SCAN_CHARS = 256000
URL = re.compile(r"https?://[^\s<>\"'\\]+")
MARKDOWN_LINK = re.compile(r"\[([^\[\]\n]{1,500})\]\((https?://[^\s)]+)\)")


def fetched_source(artifact, *, connector=None, origin="runtime"):
    """Consume optional metadata emitted by the native fetch tool itself.

    This does not guess keys in arbitrary MCP results. The original tool body
    still goes directly to the model, and successful retrieval is not a claim
    of semantic support or of having read every page section.
    """
    if not isinstance(artifact, dict) or artifact.get("schema") != "deerflow.web_page.v1":
        return None
    url = artifact.get("url")
    if not isinstance(url, str) or "redacted" in url.lower():
        return None
    try:
        safe_http_url(url)
        canonical = canonical_url(url)
    except ValueError:
        return None
    return {
        "id": "source_" + digest([origin, connector, canonical])[:24],
        "url": url,
        "canonical_url": canonical,
        "domain": (urlsplit(url).hostname or "").removeprefix("www."),
        "title": str(artifact.get("title") or url)[:1000],
        "title_observed": True,
        "origin": origin,
        "connector": connector,
        "status": "read",
        "excerpt": str(artifact.get("excerpt") or "")[:1200],
        "document_hash": artifact.get("document_hash"),
    }


# Argument names read tools use for what to open. Checked first; any other
# argument holding an absolute URL counts too, since servers name it freely.
ADDRESS_ARGUMENTS = ("url", "uri", "link", "href", "page_url", "urls", "links")
IDENTIFIER_ARGUMENTS = ("id", "doc_id", "document_id", "docId", "documentId", "page_id", "pageId", "file_id", "path", "file", "key", "name", "title")
# Shorter answers are a status line ("403 Forbidden", "not found"), not a page.
MIN_PAGE_CHARS = 40


def requested(arguments):
    """What a read call asked to open: absolute URLs, or else one identifier."""
    values = arguments if isinstance(arguments, dict) else {}
    ordered = [values[name] for name in ADDRESS_ARGUMENTS if name in values] + [value for name, value in values.items() if name not in ADDRESS_ARGUMENTS]
    urls = []
    for value in ordered:
        for entry in value if isinstance(value, list) else [value]:
            if isinstance(entry, str) and extract.http_url(entry) and entry.strip() not in urls:
                urls.append(entry.strip())
    if urls:
        return urls, None
    named = [values[name] for name in IDENTIFIER_ARGUMENTS if name in values] + [value for value in values.values() if isinstance(value, str)]
    identifier = next((" ".join(str(value).split()) for value in named if isinstance(value, (str, int)) and not isinstance(value, bool) and 0 < len(str(value).strip()) <= 300), None)
    return [], identifier


def _page(url, identifier, title, text, *, connector, origin):
    if len((text or "").strip()) < MIN_PAGE_CHARS:
        return None
    canonical = None
    if url:
        try:
            safe_http_url(url)
            canonical = canonical_url(url)
        except ValueError:
            url = None
        if url and "redacted" in url.lower():
            url = canonical = None
    if not url and not identifier:
        return None
    # A document of an internal system has no address; its identifier under the
    # source names it, so reading it twice is one reference.
    locator = None if url else f"mcp://{connector}/{identifier}"[:2000]
    return {
        "id": "source_" + digest([origin, connector, canonical or locator])[:24],
        "url": url,
        "canonical_url": canonical,
        "source_uri": locator,
        "domain": (urlsplit(url).hostname or "").removeprefix("www.") if url else None,
        "title": str(title or url or identifier)[:1000],
        "title_observed": bool(title),
        "origin": origin,
        "connector": connector,
        "status": "read",
        "text": text,
        "excerpt": text[:1200],
        "document_hash": digest(" ".join(text.split())),
    }


def opened_pages(arguments, payload, text, *, connector=None, origin="runtime"):
    """Pages a read tool opened, when the tool does not describe them itself.

    A directly exposed MCP tool (or a host tool) returns what its server
    returns: Markdown, a JSON envelope under any field names, part of a page.
    What it opened is in the call's own arguments, so the address never comes
    from the model or from a guess; title and readable text are looked up in
    the answer. Without this the page stayed an anonymous tool output that the
    converter could not match to the notes citing it by address, and a research
    that only had such tools ended with NO_EVIDENCE.
    """
    urls, identifier = requested(arguments)
    if len(urls) > 1:
        wanted = {}
        for url in urls:
            try:
                wanted[canonical_url(url)] = url
            except ValueError:
                continue
        pages = []
        for record in extract.records(payload, limit=2 * len(urls), text_limit=20000):
            try:
                asked = wanted.pop(canonical_url(record.get("url")), None)
            except ValueError:
                asked = None
            page = _page(asked, None, record.get("title"), record.get("snippet"), connector=connector, origin=origin) if asked else None
            if page:
                pages.append(page)
        return pages
    found = extract.document(payload, urls[0] if urls else None)
    # The text a field held is the page; only when none did is the tool's own text the better copy.
    body = found["text"] if found["readable"] or len(found["text"]) >= len(text) else text
    title = found["title"] if found["title"] not in {found.get("url"), urls[0] if urls else None} else None
    page = _page(urls[0] if urls else found.get("url"), identifier, title, body, connector=connector, origin=origin)
    return [page] if page else []


def _window_start(text, match, previous):
    """Where a link's own text begins.

    What follows a link (its snippet) belongs to it; what precedes it belongs to
    the entry before, except the label on its own line or in its own object
    ("1. [Title](url)", {"title": ..., "url": ...}).
    """
    start = max(0, match.start() - 350)
    if previous is None:
        return start
    own = max(text.rfind("\n", 0, match.start()), text.rfind("{", 0, match.start())) + 1
    return max(start, previous.end(), own)


def observed_sources(content, *, connector=None, origin="runtime"):
    """Return actual links and nearby excerpts, not a normalized search result."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    clipped = len(text) > MAX_SCAN_CHARS
    text = text[:MAX_SCAN_CHARS]
    labels = {html.unescape(url): label for label, url in MARKDOWN_LINK.findall(text)}
    found = {}
    matches = list(URL.finditer(text))
    for position, match in enumerate(matches):
        if clipped and match.end() == len(text):
            continue  # A cut-off locator is not a complete observed URL.
        url = html.unescape(match.group().rstrip(".,;:!?"))
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        try:
            safe_http_url(url)
            canonical = canonical_url(url)
        except ValueError:
            continue
        # Credential-redacted links cannot be reconstructed or opened safely.
        if "redacted" in url.lower():
            continue
        domain = (urlsplit(url).hostname or "").removeprefix("www.")
        source_id = "source_" + digest([origin, connector, canonical])[:24]
        found[source_id] = {
            "id": source_id,
            "url": url,
            "canonical_url": canonical,
            "domain": domain,
            # With no link label the URL is the title; signed URLs run long.
            "title": (labels.get(url) or url)[:1000],
            "title_observed": url in labels,
            "origin": origin,
            "connector": connector,
            "status": "discovered",
            # The text around this link, stopping at its neighbours: in a result
            # list the next entry's sentences belong to the next link, and a quote
            # taken from them must not be shown under this address.
            "excerpt": text[_window_start(text, match, matches[position - 1] if position else None) : min(len(text), match.end() + 700, matches[position + 1].start() if position + 1 < len(matches) else len(text))],
        }
        if len(found) >= 100:
            break
    return list(found.values())
