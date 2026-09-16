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


def observed_sources(content, *, connector=None, origin="runtime"):
    """Return actual links and nearby excerpts, not a normalized search result."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    clipped = len(text) > MAX_SCAN_CHARS
    text = text[:MAX_SCAN_CHARS]
    labels = {html.unescape(url): label for label, url in MARKDOWN_LINK.findall(text)}
    found = {}
    for match in URL.finditer(text):
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
            "title": labels.get(url) or url,
            "title_observed": url in labels,
            "origin": origin,
            "connector": connector,
            "status": "discovered",
            "excerpt": text[max(0, match.start() - 350) : min(len(text), match.end() + 700)],
        }
        if len(found) >= 100:
            break
    return list(found.values())
