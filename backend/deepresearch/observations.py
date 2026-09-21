"""Project native execution records into research references after execution.

Nothing here sits between a tool and the research model: tools keep their own
schemas and the model reads their results unchanged. Afterwards the archived
ToolMessages are turned into evidence. A research source says what it returned
in an artifact of its own; a directly exposed tool (an MCP server's, the
host's) says nothing, so the page it opened or the records it returned are
looked up in its arguments and its answer, whatever shape that has. A receipt
proves a call occurred, not that the model's interpretation of its result is
correct.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from . import extract
from .channels import BUDGET_STOP
from .contracts import RawEvidence
from .evidence import digest
from .sources import fetched_source, observed_sources, opened_pages

# Share of an answer's text its records must carry for the records alone to be cited.
RECORDS_COVER_ANSWER = 0.8
# Artifacts research sources emit about their own result.
OWN_ARTIFACTS = {"deerflow.web_page.v1", "deepresearch.search.v1", "deepresearch.records.v1"}


@dataclass
class NativeExecution:
    answer: str
    execution_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None


def derived_receipts(messages):
    """Receipts of an execution whose engine did not stamp any.

    Research keeps the engine's receipt ledger out of the model's context, and
    the engine's switch for it also stops stamping. A receipt only states that a
    call happened and how it ended, which the archived tool messages already
    say; ids follow the engine's r1..rN order of tool results.
    """
    calls = {call["id"]: call for message in messages for call in message.get("tool_calls") or [] if call.get("id")}
    receipts = []
    for message in messages:
        if message.get("type") != "tool" or not message.get("tool_call_id"):
            continue
        meta = (message.get("additional_kwargs") or {}).get("deerflow_tool_meta")
        status = (meta.get("status") if isinstance(meta, dict) else None) or message.get("status") or "success"
        name = message.get("name") or calls.get(message["tool_call_id"], {}).get("name") or ""
        receipts.append({"id": f"r{len(receipts) + 1}", "tool_call_id": message["tool_call_id"], "tool_name": name, "status": str(status)})
    return receipts


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


def derive(item, **updates):
    """A derived evidence record, validated instead of copied blindly.

    ``model_copy`` skips validation, so a title taken from a signed URL or text
    that arrived empty used to be stored and only rejected later, in
    evidence_merge, where it failed the whole unit. Validation bounds display
    fields here and reports the records that cannot be evidence at all.
    """
    from pydantic import ValidationError

    try:
        return RawEvidence.model_validate({**item.model_dump(mode="json"), **updates})
    except ValidationError:
        return None


def call_label(source, call):
    """A reader-facing title for a source tool's combined output: what was asked, not a receipt id."""
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    asked = next((args[key] for key in ("query", "q", "keywords", "search_query", "question", "url", "uri", "link") if isinstance(args.get(key), str) and args[key].strip()), None)
    return f"{source.name}: {' '.join(asked.split())[:160]}" if asked else None


def research_observations(execution: NativeExecution, sources, cite_search_results=False):
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
        artifact = message.get("artifact")
        if isinstance(artifact, dict) and artifact.get("schema") == BUDGET_STOP:
            continue  # the budget's instruction to the model, not a read
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
            document = derive(envelope, raw_id=document_id, title=opened, url=opened, source_uri=None, origin="external", provenance="fetched_document", source_id=source_id, document_hash=digest(text))
            if document is None:
                continue
            evidence[document_id] = document
            catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": "external", "url": opened, "title": opened, "provenance": "fetched_document"})
            catalog.append({"raw_id": raw_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": "runtime", "excerpt": text[:20000], "superseded": True})
            continue
        read_path = (call.get("args") or {}).get("path") if not source else None
        page = pages.get(read_path) if isinstance(read_path, str) else None
        if page is not None:
            document_id = "doc_" + digest([execution.execution_id, call_id, page["fetched"]["id"]])[:24]
            raw_ref = f"execution:{execution.execution_id}:{call_id}"
            known = page["fetched"]
            document = derive(page["item"], raw_id=document_id, snippet=text[:20000], raw_content_ref=raw_ref, source_uri=known.get("source_uri"), provenance="fetched_document", title=known["title"], url=known["url"])
            if document is None:
                continue
            evidence[document_id] = document
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
            # A declared source's output can be cited as a whole (an MCP tool
            # whose answer has no record structure), so its title says what was
            # asked rather than showing a receipt id.
            title=(call_label(source, call) if source else None) or f"{source.name if source else name} / {receipt.get('id', call_id)}",
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
        fetched = fetched_source(artifact, connector=source.name if source else name, origin=item.origin) if source and source.kind in {"native", "channel"} else None
        # A directly exposed tool describes nothing: what it opened or returned
        # is read from its arguments and its answer.
        undescribed = bool(source) and not (isinstance(artifact, dict) and artifact.get("schema") in OWN_ARTIFACTS)
        structured = artifact.get("structured_content") if isinstance(artifact, dict) else None
        answer = structured if isinstance(structured, (dict, list)) else text
        opened = opened_pages(call.get("args"), answer, text, connector=source.name, origin=item.origin) if undescribed and source.role == "read" else []
        # A knowledge source's records become separate citable evidence with
        # their own titles; the combined tool output stays as a superseded copy.
        records = artifact.get("records") if source and source.role == "data" and isinstance(artifact, dict) and artifact.get("schema") == "deepresearch.records.v1" else None
        # Where search results are the evidence (no source opens pages, or the
        # operator declared them citable), each result is cited on its own with
        # its title and link instead of the whole result list.
        if records is None and cite_search_results and source and source.role == "search" and isinstance(artifact, dict) and artifact.get("schema") == "deepresearch.search.v1":
            records = [record for record in artifact.get("results") or [] if isinstance(record, dict) and not record.get("opaque")]
        # The same for a directly exposed search or knowledge tool that answers
        # with structured records. They are found by shape, so unless they carry
        # nearly all of the answer it stays citable next to them: a format read
        # only in part, or texts cut to length, must not lose the rest. Links in
        # a text answer remain observed sources, as before.
        found_by_shape = False
        if records is None and undescribed and (source.role == "data" or (source.role == "search" and cite_search_results)):
            records = extract.records(answer, limit=50, text_limit=1200 if source.role == "search" else 4000, from_text=False) or None
            found_by_shape = bool(records) and extract.coverage(answer, records) < RECORDS_COVER_ANSWER
        recorded_urls = set()
        if records:
            for index, record in enumerate(records[:100]):
                if not isinstance(record, dict) or not str(record.get("snippet") or "").strip():
                    continue
                record_id = "rec_" + digest([execution.execution_id, call_id, index])[:24]
                try:
                    link = safe_http_url(record.get("url")) if record.get("url") else None
                except ValueError:
                    link = None
                found = derive(
                    item,
                    raw_id=record_id,
                    title=str(record.get("title") or item.title)[:1000],
                    url=link,
                    source_uri=f"tool-result://{execution.execution_id}/{record_id}",
                    snippet=str(record["snippet"])[:20000],
                    published_at=None,
                    document_hash=digest(str(record["snippet"])),
                )
                if found is None:
                    continue
                evidence[record_id] = found
                if link:
                    recorded_urls.add(canonical_url(link))
                catalog.append({"raw_id": record_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "title": found.title, "url": link, "record": index + 1})
        for page in opened:
            document_id = "doc_" + digest([execution.execution_id, call_id, page["id"]])[:24]
            address = {"title": page["title"], "url": page["url"], "source_uri": page["source_uri"]}
            document = derive(item, raw_id=document_id, source_id=page["id"], snippet=page["text"][:20000], provenance="fetched_document", document_hash=page["document_hash"], **address)
            if document is None:
                continue
            evidence[document_id] = document
            fetched = fetched or page
            if len(opened) == 1 and (external := _externalized_path(message, text)):
                pages[external] = {"fetched": page, "item": document}
            if page["canonical_url"]:
                recorded_urls.add(page["canonical_url"])
            catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, **address, "provenance": "fetched_document"})
        if fetched and not opened:
            document_id = "doc_" + digest([execution.execution_id, call_id, fetched["id"]])[:24]
            document = derive(item, raw_id=document_id, title=fetched["title"], url=fetched["url"], source_id=fetched["id"], source_uri=None, provenance="fetched_document", document_hash=fetched["document_hash"])
            if document is None:
                # The page cannot be evidence, but the call still happened: its
                # links and its envelope are recorded below like any other.
                fetched = None
            else:
                evidence[document_id] = document
                if external := _externalized_path(message, text):
                    pages[external] = {"fetched": fetched, "item": document}
                catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "url": fetched["url"], "title": fetched["title"], "provenance": "fetched_document"})
        for observed in observed_sources(text, connector=source.name if source else name, origin=item.origin):
            if fetched and not opened and observed["canonical_url"] == fetched["canonical_url"]:
                continue
            if observed["canonical_url"] in recorded_urls:
                continue  # already cited as its own search result
            document_id = "doc_" + digest([execution.execution_id, call_id, observed["id"]])[:24]
            document = derive(item, raw_id=document_id, title=observed["title"], url=observed["url"], source_id=observed["id"], source_uri=None, snippet=observed["excerpt"], provenance="observed_source")
            if document is None:
                continue
            evidence[document_id] = document
            catalog.append({"raw_id": document_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "url": observed["url"], "title": observed["title"], "excerpt": observed["excerpt"]})
        # When the native reader registered the page itself, cite that page
        # (with its URL) rather than the anonymous tool envelope.
        envelope = {"raw_id": raw_id, "receipt_id": receipt.get("id"), "tool_call_id": call_id, "tool_name": name, "origin": item.origin, "excerpt": item.snippet, "superseded": bool(fetched or (records and not found_by_shape))}
        if source:
            # What was asked: the converter matches the notes to a call by it.
            envelope["title"] = item.title
        catalog.append(envelope)
    return list(evidence.values()), catalog


def ground_source_annotations(evidences, annotations):
    """Accept source labels/excerpts only when present in the native result.

    The model can interpret arbitrary result formats, but cannot invent a URL
    or silently turn a paraphrase into an allegedly verbatim source excerpt.
    """
    by_id = {e.raw_id: e for e in evidences}
    # The complete tool output: only the call's own envelope holds it. Records
    # derived from the same call share its reference but carry one record each.
    original = {e.raw_content_ref: e.snippet for e in evidences if e.provenance == "tool_output" and e.raw_id.startswith("raw_")}

    def normalize(text):
        return " ".join(text.split()).casefold()

    for annotation in annotations:
        evidence = by_id.get(annotation.raw_id)
        if evidence is None or evidence.provenance not in {"observed_source", "fetched_document"}:
            continue
        body = normalize(original.get(evidence.raw_content_ref, ""))
        if annotation.title.strip() and normalize(annotation.title) in body:
            evidence.title = annotation.title.strip()
        # A link seen in a result list sits next to other results. Its quote must
        # come from its own surroundings, or a neighbour's sentence would be
        # shown under this link's address.
        # A read page's own text counts too: inside a JSON answer the same
        # sentence is escaped and would never match.
        scope = normalize(evidence.snippet) if evidence.provenance == "observed_source" else body + " " + normalize(evidence.snippet)
        if annotation.quote.strip() and normalize(annotation.quote) in scope:
            evidence.snippet = annotation.quote.strip()
