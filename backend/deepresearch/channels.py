"""Research source tools with provider failover.

Each provider-based source becomes one model-facing tool with a stable schema:
search (query), read (URL, read in excerpts) or data (knowledge query).
Providers are tried in order. Rate limits, exhausted quotas, bad keys and
outages cool a provider down for every run; a page one provider cannot read
just moves on to the next provider. Results are rendered in one format whatever
the backend returned, and the tool artifact records the provider that answered
and every attempt, so metrics and audits can show failovers.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from collections import OrderedDict
from typing import Literal

from pydantic import BaseModel, Field

from . import extract
from .audit import scrub_text
from .evidence import canonical_url, digest
from .providers import PROVIDER_FAILURES, ProviderError, Request, call

MAX_ATTEMPTS = 6
# (first cooldown, ceiling) in seconds; transient failures back off exponentially.
COOLDOWN = {"rate_limit": (30, 600), "timeout": (15, 300), "network": (15, 300), "server": (20, 300), "quota": (3600, 3600), "auth": (3600, 3600), "config": (600, 600)}
TRANSIENT = {"rate_limit", "timeout", "network", "server"}
DESCRIPTIONS = {
    "search": "Search the web. Returns result titles, links, dates and snippets. Snippets are for discovery only: open a page with the read tool before relying on it. Run separate focused searches for separate questions.",
    "read": "Open a web page or online document by its exact URL (from search results or the user) and return its readable text in excerpts. "
    "Continue a long page with start_index from the previous result, or pass query to jump to a section.",
    "data": "Search this knowledge source and return matching records with their titles and content. Records can be cited as evidence.",
}


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="Focused search query.")
    max_results: int = Field(default=8, ge=1, le=20, description="Number of results, 1-20.")
    time_range: Literal["day", "week", "month", "year"] | None = Field(default=None, description="Only results from this recent period, when currency matters.")


class ReadArgs(BaseModel):
    url: str = Field(min_length=8, max_length=4000, description="Exact http(s) URL to open.")
    start_index: int = Field(default=0, ge=0, description="Character position to continue from, as returned by a previous call.")
    max_length: int = Field(default=8000, ge=1000, le=16000, description="Maximum characters in this excerpt, 1000-16000.")
    query: str | None = Field(default=None, max_length=200, description="Optional literal text to locate within the page.")


class DataArgs(BaseModel):
    query: str = Field(min_length=1, max_length=1000, description="What to look for in this knowledge source.")
    max_results: int = Field(default=8, ge=1, le=30, description="Number of records, 1-30.")


class Health:
    """Process-wide provider state: rate limits and quotas are account-wide."""

    def __init__(self):
        self._state = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(source, provider):
        return digest([source.name, provider.model_dump(mode="json")])[:24]

    def ready(self, key):
        with self._lock:
            state = self._state.get(key) or {}
            return state.get("until", 0) <= time.time(), state

    def failure(self, key, source, provider, error):
        with self._lock:
            state = self._state.setdefault(key, {"source": source.name, "provider": provider.id, "type": provider.type, "successes": 0, "failures": 0, "consecutive": 0})
            state["failures"] += 1
            state["consecutive"] += 1
            base, ceiling = COOLDOWN.get(error.kind, (15, 300))
            seconds = error.retry_after if error.retry_after is not None else min(ceiling, base * 2 ** (state["consecutive"] - 1))
            state.update(until=time.time() + seconds, last_error_kind=error.kind, last_error=str(error)[:300], last_failure_at=time.time())

    def success(self, key, source, provider, milliseconds):
        with self._lock:
            state = self._state.setdefault(key, {"source": source.name, "provider": provider.id, "type": provider.type, "successes": 0, "failures": 0, "consecutive": 0})
            state["successes"] += 1
            state.update(consecutive=0, until=0, last_success_at=time.time(), last_latency_ms=milliseconds)

    def reset(self):
        with self._lock:
            self._state.clear()

    def snapshot(self):
        now = time.time()
        with self._lock:
            return [{**state, "key": key, "cooling": state.get("until", 0) > now, "cooldown_seconds": max(0, round(state.get("until", 0) - now))} for key, state in self._state.items()]


HEALTH = Health()


class PageCache:
    """Documents already fetched in a run, so reading on does not refetch the page."""

    def __init__(self, entries=64, ttl=3600):
        self.entries, self.ttl = entries, ttl
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def get(self, run_id, url):
        key = (run_id, canonical_url(url))
        with self._lock:
            item = self._items.get(key)
            if item is None or time.time() - item["at"] > self.ttl:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return item

    def put(self, run_id, url, document):
        with self._lock:
            self._items[(run_id, canonical_url(url))] = {**document, "at": time.time()}
            while len(self._items) > self.entries:
                self._items.popitem(last=False)


PAGES = PageCache()


def _message(error):
    return " ".join(scrub_text(str(error)).split())[:300]


async def run_providers(source, request, settings, request_secrets=None):
    """Try providers in order; return (provider, outcome, attempts) or raise ProviderError.

    ``request_secrets`` carries this request's own credentials (see
    ``request_secret_headers``); they take precedence over saved secrets.
    """
    enabled = [provider for provider in source.providers if provider.enabled]
    ready, cooling = [], []
    for provider in enabled:
        available, state = HEALTH.ready(HEALTH.key(source, provider))
        (ready if available else cooling).append((provider, state))
    # When every provider cools down, transient ones get one more chance rather
    # than failing the research outright; quota and key problems do not.
    order = ready + [(provider, state) for provider, state in cooling if state.get("last_error_kind") in TRANSIENT]
    attempts, empty = [], None
    skipped = [
        {"provider": provider.id, "type": provider.type, "status": "skipped", "kind": state.get("last_error_kind"), "cooldown_seconds": max(0, round(state.get("until", 0) - time.time()))}
        for provider, state in cooling
        if state.get("last_error_kind") not in TRANSIENT
    ]
    for provider, _ in order[:MAX_ATTEMPTS]:
        key = HEALTH.key(source, provider)
        started = time.monotonic()
        try:
            outcome = await asyncio.wait_for(call(provider, request, servers=settings.mcp_servers, request_secrets=request_secrets), provider.timeout_seconds + 15)
        except ProviderError as error:
            failure = error
        except TimeoutError:
            failure = ProviderError("timeout", f"No answer within {provider.timeout_seconds:g}s")
        except Exception as error:  # A provider bug must not end the source; it is recorded.
            failure = ProviderError("server", type(error).__name__)
        else:
            milliseconds = round((time.monotonic() - started) * 1000)
            if request.role != "read" and not outcome.records:
                attempts.append({"provider": provider.id, "type": provider.type, "status": "empty", "ms": milliseconds})
                HEALTH.success(key, source, provider, milliseconds)
                empty = empty or (provider, outcome)
                continue
            HEALTH.success(key, source, provider, milliseconds)
            attempts.append({"provider": provider.id, "type": provider.type, "status": "ok", "ms": milliseconds})
            return provider, outcome, skipped + attempts
        milliseconds = round((time.monotonic() - started) * 1000)
        attempts.append({"provider": provider.id, "type": provider.type, "status": "error", "kind": failure.kind, "message": _message(failure), "ms": milliseconds})
        if failure.kind in PROVIDER_FAILURES:
            HEALTH.failure(key, source, provider, failure)
    if empty is not None:
        return empty[0], empty[1], skipped + attempts
    detail = (
        "; ".join(
            f"{item['provider']} (skipped: {item.get('kind') or 'cooling'} cooldown {item.get('cooldown_seconds', 0)}s)"
            if item["status"] == "skipped"
            else f"{item['provider']} ({item.get('kind') or item['status']}: {item.get('message', '')})"
            for item in skipped + attempts
        )
        or "no enabled provider"
    )
    raise ProviderError("exhausted", f"All providers failed for {source.tool}: {detail}", status=None)


FAILED_ATTEMPT = re.compile(r"(?:: |; )([A-Za-z0-9][A-Za-z0-9_-]{0,79}) \((rate_limit|quota|auth|timeout|network|server|config|invalid|not_found|blocked|empty|unsupported|skipped|error)[:)]")


def failed_attempts(text):
    """Provider attempts from this module's own all-providers-failed message."""
    if not isinstance(text, str) or "All providers failed for" not in text:
        return []
    return [{"provider": provider, "status": "skipped" if kind == "skipped" else "error", "kind": kind} for provider, kind in FAILED_ATTEMPT.findall(text.split("All providers failed for", 1)[1])]


def _excerpt(text, start_index, max_length, query):
    matches = [match.start() for match in re.finditer(re.escape(query), text, re.I)][:30] if query else []
    position = next((max(start_index, match - 250) for match in matches if match >= start_index), start_index)
    position = min(position, len(text))
    end = min(position + max_length, len(text))
    return position, end, matches


def build_tool(source, settings, run_id, request_secrets=None):
    """One StructuredTool for a provider-based source; returns (content, artifact)."""
    from langchain_core.tools import StructuredTool, ToolException

    description = source.description or DESCRIPTIONS[source.role]

    async def providers(request):
        try:
            return await run_providers(source, request, settings, request_secrets)
        except ProviderError as error:
            raise ToolException("Error: " + str(error)) from None

    async def search(query: str, max_results: int = 8, time_range: str | None = None):
        query = " ".join(query.split())
        provider, outcome, attempts = await providers(Request("search", query=query, max_results=max_results, time_range=time_range))
        found = outcome.records[:max_results]
        artifact = {"schema": "deepresearch.search.v1", "source": source.name, "provider": provider.id, "provider_type": provider.type, "query": query, "results": found, "attempts": attempts, "raw_preview": outcome.raw[:2000]}
        return extract.render_search(query, found, provider.id), artifact

    async def read(url: str, start_index: int = 0, max_length: int = 8000, query: str | None = None):
        if not extract.http_url(url):
            raise ToolException("Error: url must be an absolute http(s) URL")
        cached = PAGES.get(run_id, url)
        if cached is None:
            provider, outcome, attempts = await providers(Request("read", url=url))
            page = {"title": outcome.document.get("title") or url, "text": outcome.document["text"], "provider": provider.id, "provider_type": provider.type}
            PAGES.put(run_id, url, page)
        else:
            page, attempts = cached, [{"provider": cached["provider"], "type": cached["provider_type"], "status": "cache", "ms": 0}]
        text = page["text"]
        position, end, matches = _excerpt(text, start_index, max_length, query)
        excerpt = text[position:end]
        next_position = end if end < len(text) else None
        artifact = {
            "schema": "deerflow.web_page.v1",
            "url": url,
            "title": str(page["title"])[:1000],
            "document_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "start_index": position,
            "end_index": end,
            "total_chars": len(text),
            "next_start_index": next_position,
            "excerpt": excerpt[:1200],
            "source": source.name,
            "provider": page["provider"],
            "provider_type": page["provider_type"],
            "attempts": attempts,
        }
        header = f"Source: {url}\nTitle: {artifact['title']}\nExcerpt: characters {position}-{end} of {len(text)}."
        if query:
            header += f"\nLiteral matches: {matches}." if matches else "\nNo literal match found; do not infer absence of the concept from this alone."
        footer = f"\n\n[More content: call {source.tool} with start_index={next_position}, or query a section title.]" if next_position is not None else "\n\n[End of document.]"
        return header + "\n\n" + excerpt + footer, artifact

    async def data(query: str, max_results: int = 8):
        query = " ".join(query.split())
        provider, outcome, attempts = await providers(Request("data", query=query, max_results=max_results))
        found = outcome.records[:max_results]
        artifact = {"schema": "deepresearch.records.v1", "source": source.name, "provider": provider.id, "provider_type": provider.type, "query": query, "records": found, "attempts": attempts, "raw_preview": outcome.raw[:2000]}
        return extract.render_records(source.name, found, provider.id), artifact

    coroutine, schema = {"search": (search, SearchArgs), "read": (read, ReadArgs), "data": (data, DataArgs)}[source.role]
    return StructuredTool(name=source.tool, description=description, args_schema=schema, coroutine=coroutine, response_format="content_and_artifact", handle_tool_error=True)


async def test_provider(source, provider, settings, *, query=None, url=None):
    """Run one provider once for the settings page, bypassing cooldowns."""
    from .providers import call

    request = Request(source.role, query=query or "DeerFlow deep research", url=url or "https://example.com/", max_results=3)
    started = time.monotonic()
    try:
        outcome = await asyncio.wait_for(call(provider, request, servers=settings.mcp_servers), provider.timeout_seconds + 15)
    except ProviderError as error:
        return {"ok": False, "kind": error.kind, "message": _message(error), "ms": round((time.monotonic() - started) * 1000)}
    except TimeoutError:
        return {"ok": False, "kind": "timeout", "message": "No answer in time", "ms": round((time.monotonic() - started) * 1000)}
    except Exception as error:
        return {"ok": False, "kind": "server", "message": type(error).__name__, "ms": round((time.monotonic() - started) * 1000)}
    sample = outcome.records[:3] if source.role != "read" else [{"title": (outcome.document or {}).get("title"), "url": (outcome.document or {}).get("url"), "snippet": ((outcome.document or {}).get("text") or "")[:300]}]
    return {"ok": True, "ms": round((time.monotonic() - started) * 1000), "count": len(outcome.records) if source.role != "read" else 1, "sample": sample}
