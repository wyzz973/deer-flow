"""Project native execution records into research references after execution.

Nothing here sits between a tool and the research model. The only structures
we inspect are DeerFlow/LangChain's own ToolMessage and tool receipt envelopes;
the tool's business payload stays opaque. A receipt proves a call occurred,
not that the model's interpretation of its result is correct.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import RawEvidence
from .evidence import digest
from .sources import fetched_source, observed_sources


@dataclass
class NativeExecution:
    answer: str
    execution_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


# The host's tool-output budget moves oversized results to this virtual path and
# leaves a synopsis in the ToolMessage. Its transform trail declares that fact.
EXTERNALIZED_PATH = re.compile(r"/mnt/user-data/outputs/[^\s\]\)\"'<>]+")


def _externalized_path(message, text):
    trail = (message.get("additional_kwargs") or {}).get("deerflow_tool_transforms")
    if not isinstance(trail, list) or not trail or not isinstance(trail[-1], dict) or trail[-1].get("kind") != "externalized":
        return None
    match = EXTERNALIZED_PATH.search(text)
    return match.group(0).rstrip(".,;:") if match else None


# Browser actions that may leave the page opened by browser_navigate.
BROWSER_LEAVES_PAGE = {"browser_click", "browser_type", "browser_back", "browser_close"}


def research_observations(execution: NativeExecution, sources):
    from deerflow.utils.messages import message_content_to_text

    from .contracts import safe_http_url
    from .evidence import canonical_url

    by_tool = {source.tool: source for source in sources}
    receipts = {r["tool_call_id"]: r for r in execution.receipts if r.get("tool_call_id")}
    calls = {call["id"]: call for message in execution.messages for call in message.get("tool_calls", []) if call.get("id")}
    evidence, catalog = {}, []
    # Externalized page path -> the page registered by the native reader. When
    # the researcher reads that file back, the text is the same page, so it is
    # cited with the page's URL instead of an anonymous file-read receipt.
    pages = {}
    # A browser text read belongs to the URL opened by browser_navigate only when
    # the page was known before that model turn and nothing could have left it.
    browser_url, browser_url_for_call = None, {}
    for message in execution.messages:
        if message.get("type") == "ai":
            for call in message.get("tool_calls") or []:
                if call.get("name") == "browser_get_text" and call.get("id"):
                    browser_url_for_call[call["id"]] = browser_url
            continue
        if message.get("type") != "tool":
            continue
        if (message.get("name") or calls.get(message.get("tool_call_id"), {}).get("name")) in BROWSER_LEAVES_PAGE:
            browser_url = None
        call_id = message.get("tool_call_id")
        call = calls.get(call_id, {})
        name = message.get("name") or call.get("name")
        source = by_tool.get(name)
        receipt = receipts.get(call_id, {})
        status = receipt.get("status") or message.get("status", "success")
        if not name or not call_id or status != "success" or message.get("status") == "error":
            continue
        text = message_content_to_text(message.get("content") or "")
        if not text.strip():
            continue
        if name == "browser_navigate" and not source:
            try:
                browser_url = safe_http_url((call.get("args") or {}).get("url"))
            except (TypeError, ValueError):
                browser_url = None
        opened = browser_url_for_call.get(call_id) if name == "browser_get_text" and not source else None
        if opened:
            raw_id = "raw_" + digest([execution.execution_id, call_id])[:24]
            raw_ref = f"execution:{execution.execution_id}:{call_id}"
            envelope = RawEvidence(
                raw_id=raw_id,
                title=f"{name} / {receipt.get('id', call_id)}",
                source_uri=f"tool-result://{execution.execution_id}/{raw_id}",
                origin="runtime",
                source_name="native-" + digest(name)[:16],
                publisher="runtime",
                snippet=text[:20000],
                provenance="tool_output",
                raw_content_ref=raw_ref,
            )
            source_id = "source_" + digest(["external", name, canonical_url(opened)])[:24]
            document_id = "doc_" + digest([execution.execution_id, call_id, source_id])[:24]
            evidence[raw_id] = envelope
            evidence[document_id] = envelope.model_copy(
                update={"raw_id": document_id, "title": opened, "url": opened, "source_uri": None, "origin": "external", "provenance": "fetched_document", "source_id": source_id, "document_hash": digest(text)}
            )
            catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": "external", "url": opened, "title": opened, "provenance": "fetched_document"})
            catalog.append({"raw_id": raw_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": "runtime", "excerpt": text[:20000], "superseded": True})
            continue
        read_path = (call.get("args") or {}).get("path") if not source else None
        page = pages.get(read_path) if isinstance(read_path, str) else None
        if page is not None:
            document_id = "doc_" + digest([execution.execution_id, call_id, page["fetched"]["id"]])[:24]
            raw_ref = f"execution:{execution.execution_id}:{call_id}"
            evidence[document_id] = page["item"].model_copy(
                update={"raw_id": document_id, "snippet": text[:20000], "raw_content_ref": raw_ref, "source_uri": None, "provenance": "fetched_document", "title": page["fetched"]["title"], "url": page["fetched"]["url"]}
            )
            # The anonymous read_file envelope stays auditable but is superseded.
            raw_id = "raw_" + digest([execution.execution_id, call_id])[:24]
            evidence[raw_id] = RawEvidence(
                raw_id=raw_id,
                title=f"{name} / {receipt.get('id', call_id)}",
                source_uri=f"tool-result://{execution.execution_id}/{raw_id}",
                origin="runtime",
                source_name="native-" + digest(name)[:16],
                publisher="runtime",
                snippet=text[:20000],
                provenance="tool_output",
                raw_content_ref=raw_ref,
            )
            url, title = page["fetched"]["url"], page["fetched"]["title"]
            catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": page["item"].origin, "url": url, "title": title, "provenance": "fetched_document"})
            catalog.append({"raw_id": raw_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": "runtime", "excerpt": text[:20000], "superseded": True})
            continue
        raw_id = "raw_" + digest([execution.execution_id, call_id])[:24]
        item = RawEvidence(
            raw_id=raw_id,
            title=f"{source.name if source else name} / {receipt.get('id', call_id)}",
            source_uri=f"{'mcp-result' if source and source.kind == 'mcp' else 'tool-result'}://{execution.execution_id}/{raw_id}",
            # Files, sandbox and other native tools can support findings too.
            # Only explicitly configured source tools carry an internal or
            # external classification; unrelated host tools stay runtime-only.
            origin=source.origin if source else "runtime",
            source_name=source.name if source else "native-" + digest(name)[:16],
            source_level=source.level if source else "L4",
            publisher=source.publisher if source else "runtime",
            snippet=text[:20000],
            provenance="tool_output",
            raw_content_ref=f"execution:{execution.execution_id}:{call_id}",
        )
        evidence[raw_id] = item
        fetched = fetched_source(message.get("artifact"), connector=source.name if source else name, origin=item.origin) if source and source.kind == "native" else None
        if fetched:
            document_id = "doc_" + digest([execution.execution_id, call_id, fetched["id"]])[:24]
            document = item.model_copy(
                update={"raw_id": document_id, "title": fetched["title"], "url": fetched["url"], "source_id": fetched["id"], "source_uri": None, "provenance": "fetched_document", "document_hash": fetched["document_hash"]}
            )
            evidence[document_id] = document
            if external := _externalized_path(message, text):
                pages[external] = {"fetched": fetched, "item": document}
            catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "url": fetched["url"], "title": fetched["title"], "provenance": "fetched_document"})
        for observed in observed_sources(text, connector=source.name if source else name, origin=item.origin):
            if fetched and observed["canonical_url"] == fetched["canonical_url"]:
                continue
            document_id = "doc_" + digest([execution.execution_id, call_id, observed["id"]])[:24]
            document = item.model_copy(update={"raw_id": document_id, "title": observed["title"], "url": observed["url"], "source_id": observed["id"], "source_uri": None, "snippet": observed["excerpt"], "provenance": "observed_source"})
            evidence[document_id] = document
            catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "url": observed["url"], "title": observed["title"], "excerpt": observed["excerpt"]})
        # When the native reader registered the page itself, cite that page
        # (with its URL) rather than the anonymous tool envelope.
        catalog.append({"raw_id": raw_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "excerpt": item.snippet, "superseded": bool(fetched)})
    return list(evidence.values()), catalog


def ground_source_annotations(evidences, annotations):
    """Accept source labels/excerpts only when present in the native result.

    The model can interpret arbitrary result formats, but cannot invent a URL
    or silently turn a paraphrase into an allegedly verbatim source excerpt.
    """
    by_id = {e.raw_id: e for e in evidences}
    original = {e.raw_content_ref: e.snippet for e in evidences if e.provenance == "tool_output"}

    def normalize(text):
        return " ".join(text.split()).casefold()

    for annotation in annotations:
        evidence = by_id.get(annotation.raw_id)
        if evidence is None or evidence.provenance not in {"observed_source", "fetched_document"}:
            continue
        body = normalize(original.get(evidence.raw_content_ref, ""))
        if annotation.title.strip() and normalize(annotation.title) in body:
            evidence.title = annotation.title.strip()
        if annotation.quote.strip() and normalize(annotation.quote) in body:
            evidence.snippet = annotation.quote.strip()
