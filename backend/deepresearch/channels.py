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
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from . import extract, wire
from .audit import scrub_text
from .contracts import ResearchError
from .evidence import canonical_url, digest
from .providers import PROVIDER_FAILURES, ProviderError, Request, call
from .report_policy import results_citable

MAX_ATTEMPTS = 6
# (first cooldown, ceiling) in seconds; transient failures back off exponentially.
COOLDOWN = {"rate_limit": (30, 600), "timeout": (15, 300), "network": (15, 300), "server": (20, 300), "quota": (3600, 3600), "auth": (3600, 3600), "config": (600, 600)}
TRANSIENT = {"rate_limit", "timeout", "network", "server"}
DESCRIPTIONS = {
    "search": "Search the web. Returns result titles, links, dates and snippets. Snippets are for discovery only: open a page with the read tool before relying on it. Run separate focused searches for separate questions.",
    "read": "Open a web page or online document by its exact URL (from search results or the user) and return its readable text in excerpts. "
    "Continue a long page with start_index from the previous result, or pass query to jump to a section.",
    "data": "Search this knowledge source and return matching records with their titles and content. Records can be cited as evidence.",
    # A search source whose results are the evidence: no read source is
    # configured, or the operator declared that search returns citable text.
    "search_citable": "Search and return result titles, links, dates and excerpts. These results can be cited as evidence: rely only on what an excerpt actually says. Run separate focused searches for separate questions.",
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
        attempts.append({"provider": provider.id, "type": provider.type, "status": "error", "kind": failure.kind, "message": _message(failure), "http_status": failure.status, "ms": milliseconds})
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


# Artifact of a reply that ends a step's searching. It is an instruction to the
# model, never something it read, so observation skips it.
BUDGET_STOP = "deepresearch.budget_stop.v1"


@dataclass(frozen=True)
class Stop:
    reason: Literal["step", "run", "time"]
    text: str
    # How much of the allowance was gone when this refusal happened. The event
    # is emitted once per reason, so without these the repeats say only "no".
    used: int | None = None
    limit: int | None = None

    @property
    def artifact(self):
        return {"schema": BUDGET_STOP, "reason": self.reason, "scope": self.reason, **{key: value for key, value in {"used": self.used, "limit": self.limit}.items() if value is not None}}


class SearchBudget:
    """How many searches one research step may make, enforced gracefully.

    A researcher that keeps searching used to end the whole run with
    ``BUDGET_EXHAUSTED``. A step budget instead winds that step down: the step
    knows its allowance up front (it is in the task payload) and, once it is
    used, search tools answer with a stop instruction so the model writes its
    notes from the evidence it already has. Page reads never count against the
    allowance, because only an opened page is citable; they count toward the
    run-wide ceiling, which winds every tool down the same way.
    """

    def __init__(self, store, run_id, unit_id, limit=None, deadline=None):
        self.store, self.run_id, self.unit_id, self.limit = store, run_id, unit_id, limit
        # time.monotonic() after which the step must wrap up: its own time
        # allowance (max_seconds_per_unit) or what the run's time budget leaves
        # for research. Like searches, time winds a step down instead of
        # killing it: a step cut off by its hard timeout loses all it has read.
        self.deadline = deadline
        self.used = 0
        self.announced = set()

    async def reserve(self, kind="search"):
        """Account one source call, or return the Stop that ends it."""
        if self.deadline is not None and time.monotonic() >= self.deadline:
            text = "Stop: the time for this research step is used up. Do not call search or read tools again. Write your research notes now from the evidence you already collected and say what remains unverified."
            return await self._wrap_up(Stop("time", text, self.used, self.limit))
        searching = kind != "read"
        if searching:
            if self.limit is not None and self.used >= self.limit:
                text = f"No more searches in this step: it has used all {self.limit} of its searches. You may still open pages you already found with the read tool; "
                text += "then write your research notes from that evidence and say what remains unverified."
                return await self._wrap_up(Stop("step", text, self.used, self.limit))
            # Claim the slot before waiting on the ledger: searches sent in one
            # turn run concurrently and would otherwise all see it free.
            self.used += 1
        try:
            await self.store.reserve(self.run_id, tool_calls=1)
        except ResearchError as error:
            if searching:
                self.used -= 1
            if error.code != "BUDGET_EXHAUSTED":
                raise
            text = "Stop: the research-wide tool budget is used up. Write your research notes now from the evidence you already collected, say what remains unverified, and do not call search or read tools again."
            return await self._wrap_up(Stop("run", text, self.used, self.limit))
        return None

    def call_timeout(self):
        """Seconds a source call started now may take, or None without a deadline.

        A call that starts before the deadline may finish a little after it,
        but a slow source must not hold the step until its hard timeout.
        """
        if self.deadline is None:
            return None
        return max(5.0, self.deadline - time.monotonic() + 10.0)

    async def _wrap_up(self, stop):
        if stop.reason not in self.announced:
            self.announced.add(stop.reason)
            reason = {"step": f"this step has used all {self.limit} of its searches", "time": "the time for this research step is used up"}.get(stop.reason, "the research-wide tool budget is used up")
            await self.store.event(self.run_id, "research.search.limited", {"unit_id": self.unit_id, "used": self.used, "limit": self.limit, "reason": reason, "scope": stop.reason})
        return stop


def _stated(value):
    """A provider's date claim as text a payload can carry, or None.

    Providers report whatever their page said, in whatever shape; the artifact
    is read by the model, stored and serialized, so it holds one normal form.
    """
    moment = extract.published(value)
    return moment.isoformat() if moment else None


def build_tool(source, settings, run_id, request_secrets=None, budget=None, recorder=None):
    """One StructuredTool for a provider-based source; returns (content, artifact)."""
    from langchain_core.tools import StructuredTool, ToolException

    citable_results = source.role == "search" and results_citable(settings)
    description = source.description or DESCRIPTIONS["search_citable" if citable_results else source.role]

    async def spent(kind="search"):
        """None while the call is affordable, else the Stop that ends it."""
        return await budget.reserve(kind) if budget is not None else None

    async def providers(request):
        try:
            return await asyncio.wait_for(run_providers(source, request, settings, request_secrets), budget.call_timeout() if budget is not None else None)
        except TimeoutError:
            raise ToolException("Error: the source did not answer in the time left for this step. Do not retry it; write your research notes from what you already have.") from None
        except ProviderError as error:
            raise ToolException("Error: " + str(error)) from None

    async def search(query: str, max_results: int = 8, time_range: str | None = None):
        if stop := await spent():
            return stop.text, stop.artifact
        query = " ".join(query.split())
        provider, outcome, attempts = await providers(Request("search", query=query, max_results=max_results, time_range=time_range))
        found = outcome.records[:max_results]
        artifact = {"schema": "deepresearch.search.v1", "source": source.name, "provider": provider.id, "provider_type": provider.type, "query": query, "results": found, "attempts": attempts, "raw_preview": outcome.raw[:2000]}
        return extract.render_search(query, found, provider.id, citable=citable_results), artifact

    async def read(url: str, start_index: int = 0, max_length: int = 8000, query: str | None = None):
        if not extract.http_url(url):
            raise ToolException("Error: url must be an absolute http(s) URL")
        cached = PAGES.get(run_id, url)
        # A page already read in this run is free: continuing it costs no budget.
        if cached is None and (stop := await spent("read")):
            return stop.text, stop.artifact
        if cached is None:
            provider, outcome, attempts = await providers(Request("read", url=url))
            page = {"title": outcome.document.get("title") or url, "text": outcome.document["text"], "published_at": _stated(outcome.document.get("published_at")), "provider": provider.id, "provider_type": provider.type}
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
            "published_at": page.get("published_at"),
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
        if stop := await spent():
            return stop.text, stop.artifact
        query = " ".join(query.split())
        provider, outcome, attempts = await providers(Request("data", query=query, max_results=max_results))
        found = outcome.records[:max_results]
        artifact = {"schema": "deepresearch.records.v1", "source": source.name, "provider": provider.id, "provider_type": provider.type, "query": query, "records": found, "attempts": attempts, "raw_preview": outcome.raw[:2000]}
        return extract.render_records(source.name, found, provider.id), artifact

    coroutine, schema = {"search": (search, SearchArgs), "read": (read, ReadArgs), "data": (data, DataArgs)}[source.role]
    if recorder is not None:
        coroutine = wire.recorded(coroutine, recorder, tool=source.tool, source=source.name)
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
