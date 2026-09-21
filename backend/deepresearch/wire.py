"""What a source actually sent and actually received, one layer below the tool.

A research tool is the layer the model sees. Underneath it a source may call an
MCP server or an HTTP endpoint, possibly several of them down a failover chain,
and until now none of that was recorded: a research that only had MCP tools left
`output_chars` and a hash of its arguments, so "what did we send, what came
back" had no answer at all.

The recorder is bound inside the tool coroutine, not where the tools are built
and not in a callback handler. A native subagent runs its tools on its own event
loop whose context starts empty, and a callback handler runs in a context copied
for the callback, so neither of those places reaches the code that makes the
call. A task inherits the context current when it is created, so binding here
carries down through `asyncio.wait_for` and `asyncio.to_thread` to every leaf.

Nothing here holds an asyncio primitive: the same rule as `trace.py`, because
these objects cross loops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

from .contracts import utcnow

logger = logging.getLogger("deepresearch.audit")
_current: ContextVar[Scope | None] = ContextVar("deepresearch_wire", default=None)

# Response headers worth keeping. The rest can carry set-cookie and tokens.
KEPT_HEADERS = ("content-type", "retry-after", "x-request-id", "content-length")


@dataclass(frozen=True)
class WireRecorder:
    """Where outbound calls of one run are written. Built once per research step."""

    store: object
    run_id: str
    secrets: tuple = ()
    max_chars: int = 65536

    async def write(self, record, request=None, response=None):
        """Audit records describe research; losing one must never stop it."""
        try:
            await self.store.record_wire_call(self.run_id, "wire-" + uuid4().hex, record, request=request, response=response)
        except Exception as exc:  # noqa: BLE001 - observability must not fail research
            logger.warning(json.dumps({"event": "wire_audit_failed", "error": type(exc).__name__}))


@dataclass
class Scope:
    """The recorder plus what the enclosing tool call is."""

    recorder: WireRecorder
    call_id: str | None = None
    unit_id: str | None = None
    tool: str | None = None
    source: str | None = None
    extra: dict = field(default_factory=dict)

    def details(self):
        return {"call_id": self.call_id, "unit_id": self.unit_id, "tool": self.tool, "source": self.source, **self.extra}


@contextmanager
def bind(recorder, **scope):
    """Make ``recorder`` the sink for outbound calls made inside this block."""
    if recorder is None:
        yield None
        return
    token = _current.set(Scope(recorder, **scope))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def current():
    return _current.get()


def call_id(callbacks):
    """The id the enclosing tool call was filed under.

    LangChain hands a tool coroutine that declares ``callbacks`` the child
    callback manager of that tool run, and its ``parent_run_id`` is the run id
    ``trace.on_tool_start`` used as the ``research_tool_call`` row id. That makes
    every outbound call self-correlating, with no time windows and without
    touching the artifact (an MCP artifact is usually ``None``, so there is
    nothing to put an id in).
    """
    value = getattr(callbacks, "parent_run_id", None)
    return str(value) if value else None


def recorded(coroutine, recorder, **scope):
    """Bind the recorder for one research tool call.

    ``functools.wraps`` must not be used here: ``inspect.signature`` follows
    ``__wrapped__``, the wrapped function has no ``callbacks`` parameter, and
    LangChain then silently stops injecting it. The tool is built with an
    explicit name, description and args_schema, so nothing is lost by leaving
    the wrapper bare.

    The callback manager itself is never passed on. A call that inherited it
    would be recorded as a second tool call and charge the run's tool budget
    twice — a real run once billed 25 searches as 41.
    """

    async def call(*args, callbacks=None, **arguments):
        with bind(recorder, call_id=call_id(callbacks), **scope):
            return await coroutine(*args, **arguments)

    return call


def redacted_url(url, secrets=()):
    from .audit import scrub_text

    return scrub_text(str(url or ""), secrets)


def _body(value, secrets, max_chars):
    """A payload kept whole where it fits, and explicitly marked where it does not.

    Silent clipping is worse than no record: a reader cannot tell whether what
    they are looking at is the whole answer.
    """
    from .audit import scrub

    if value is None:
        return None
    cleaned = scrub(value, secrets)
    text = cleaned if isinstance(cleaned, str) else json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return {"body": cleaned, "chars": len(text), "truncated": False}
    return {"body": text[:max_chars], "chars": len(text), "truncated": True}


def _request(value, secrets, max_chars):
    """The request as its own fields, with only an oversized body bounded.

    Keeping the shape matters: a reader looks for ``arguments`` or ``body``,
    not for a wrapper around them. Credential-named header values are replaced
    by ``scrub``; header names are kept because which header was sent is often
    the whole question.
    """
    if value is None:
        return None
    from .audit import scrub

    cleaned = scrub(value, secrets)
    if not isinstance(cleaned, dict):
        return cleaned
    for name in ("body", "arguments"):
        if cleaned.get(name) is None:
            continue
        kept = _body(cleaned[name], secrets, max_chars)
        if kept["truncated"]:
            cleaned = {**cleaned, name: kept["body"], name + "_chars": kept["chars"], name + "_truncated": True}
    return cleaned


class Entry:
    """What the body of an outbound call fills in before it returns."""

    def __init__(self, secrets, max_chars):
        self.secrets, self.max_chars, self.response, self.extra = secrets, max_chars, None, {}
        # A server that answered "this is an error" is an error, even though
        # nothing was raised on our side.
        self.outcome = None

    def responded(self, response=None, *, status=None, body=None, artifact=None, http_status=None, headers=None, **extra):
        record = _body(body, self.secrets, self.max_chars) or {}
        if response is not None:  # an httpx.Response
            http_status = response.status_code
            headers = {name: response.headers.get(name) for name in KEPT_HEADERS if response.headers.get(name)}
            if body is None:
                record = _body(_text(response), self.secrets, self.max_chars) or {}
        if artifact is not None:
            record["artifact"] = _body(artifact, self.secrets, self.max_chars)
        if status is not None:
            record["is_error"] = status == "error"
            self.outcome = "error" if status == "error" else self.outcome
        if headers:
            record["headers"] = headers
        self.response = record or None
        self.extra.update({key: value for key, value in {"http_status": http_status, "retry_after": (headers or {}).get("retry-after"), **extra}.items() if value is not None})


def _text(response):
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - a body that is not JSON is still worth keeping
        try:
            return response.text
        except Exception:  # noqa: BLE001 - a streamed body may not be readable here
            return None


@asynccontextmanager
async def outbound(kind, details, *, request=None, secrets=()):
    """Record one outbound call, or do nothing when no recorder is bound."""
    scope = _current.get()
    if scope is None:
        yield Entry((), 0)
        return
    recorder = scope.recorder
    secrets = tuple(secrets) + tuple(recorder.secrets)
    entry = Entry(secrets, recorder.max_chars)
    started, at = time.monotonic(), utcnow()

    def finished(outcome, error=None):
        record = {
            **scope.details(),
            **details,
            "kind": kind,
            "started_at": at,
            "ended_at": utcnow(),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "status": entry.outcome or outcome,
            **entry.extra,
        }
        if "url" in record:
            record["url"] = redacted_url(record["url"], secrets)
            record["url_host"] = urlsplit(str(details.get("url") or "")).hostname
        if error is not None:
            record["error"] = {"type": type(error).__name__, "kind": getattr(error, "kind", None), "http_status": getattr(error, "status", None)}
        return record

    try:
        yield entry
    except BaseException as error:
        # Cancellation is the step's deadline unwinding, not an outcome of this
        # call. Its result is already on the tool call as a timed-out attempt,
        # and writing here would hold a dying task open for a SQLite transaction.
        if not isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            await recorder.write(finished("error", error), _request(request, secrets, recorder.max_chars), entry.response)
        raise
    else:
        await recorder.write(finished("ok"), _request(request, secrets, recorder.max_chars), entry.response)
