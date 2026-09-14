"""Local, owner-scoped research tracing and rotating operational logs.

Trace records share the durable event sequence, so reconnects and exports use
the same cursor. No telemetry service or API key is involved. Callback handlers
hold no asyncio primitives: native subagents may call them on their own loop.
"""

from __future__ import annotations

import json
import logging
import re
import time
import traceback
from contextlib import asynccontextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from uuid import uuid4

from .output import visible_text

_parent: ContextVar[str | None] = ContextVar("research_span", default=None)
logger = logging.getLogger("deepresearch.audit")
_sensitive = re.compile(r"authorization|cookie|csrf|password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|reasoning|thinking", re.I)


def current_context():
    """Transport-only tests can call the adapter outside a LangGraph run."""
    try:
        from langgraph.runtime import get_runtime

        return get_runtime().context or {}
    except (ImportError, RuntimeError):
        return {}


def redact(value, secrets=(), max_chars=16000):
    """Bound payload size and remove credential carriers and known values."""

    def clean(item, depth=0):
        if depth > 12:
            return "[depth limit]"
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if isinstance(item, dict):
            return {str(k): "[redacted]" if _sensitive.search(str(k)) else clean(v, depth + 1) for k, v in list(item.items())[:200]}
        if isinstance(item, (list, tuple)):
            return [clean(v, depth + 1) for v in item[:200]]
        if isinstance(item, str):
            text = visible_text(item)
            for secret in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
                text = text.replace(secret, "[redacted]")
            text = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [redacted]", text)
            text = re.sub(r"(?i)([?&](?:token|key|api_key|signature|sig|password|secret|x-amz-signature)=)[^&\s\"']+", r"\1[redacted]", text)
            text = re.sub(r"(?im)((?:authorization|cookie|set-cookie|password|api[_-]?key|csrf[_-]?token)\s*[:=]\s*)[^\r\n]+", r"\1[redacted]", text)
            return text[:max_chars]
        return item if item is None or isinstance(item, (int, float, bool)) else str(type(item).__name__)

    cleaned = clean(value)
    encoded = json.dumps(cleaned, ensure_ascii=False)
    return cleaned if len(encoded) <= max_chars else {"preview": encoded[:max_chars], "truncated": True}


def install_log(data_dir):
    """Attach only to our logger; never replace the host's logging config."""
    handler = RotatingFileHandler(data_dir / "research.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return handler


def error_details(error):
    """Unknown provider exception strings can contain headers or request bodies.

    Retain machine-readable failure metadata and stack locations, without source
    lines (which may themselves contain credentials) or arbitrary exception text.
    """
    from .contracts import ResearchError

    data = {"type": type(error).__name__, "stack": [{"file": frame.filename, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(error.__traceback__)]}
    if isinstance(error, ResearchError):
        data.update(code=error.code, message=str(error))
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        data["status_code"] = status
    return data


class LocalTrace:
    def __init__(self, store, run_id, settings, secrets=()):
        self.store, self.run_id, self.settings = store, run_id, settings
        self.secrets = tuple(secrets)

    async def write(self, phase, span_id, parent_id, name, kind, **data):
        payload = data.pop("payload", None)
        if payload is not None and self.settings.trace_capture_content:
            data["payload"] = redact(payload, self.secrets, self.settings.trace_max_chars)
        event = {"trace_id": self.run_id, "span_id": span_id, "parent_span_id": parent_id, "name": name, "kind": kind, **data}
        await self.store.event(self.run_id, "trace." + phase, event)
        # Detailed bodies belong to the access-controlled trace, not stdout.
        summary = {k: v for k, v in event.items() if k != "payload"}
        summary["event"] = phase
        level = logging.ERROR if data.get("status") == "error" else logging.INFO
        logger.log(level, json.dumps(summary, ensure_ascii=False))

    @asynccontextmanager
    async def span(self, name, kind, payload=None):
        span_id, parent_id = str(uuid4()), _parent.get()
        started = time.monotonic()
        await self.write("started", span_id, parent_id, name, kind, payload=payload)
        token = _parent.set(span_id)
        output = {}
        try:
            yield output
        except BaseException as exc:
            # An interrupt is a normal suspended workflow, not an error.
            status = "paused" if type(exc).__name__ == "GraphInterrupt" else "cancelled" if type(exc).__name__ == "CancelledError" else "error"
            await self.write(
                "ended",
                span_id,
                parent_id,
                name,
                kind,
                status=status,
                duration_ms=round((time.monotonic() - started) * 1000),
                error_type=type(exc).__name__,
                payload={"error": error_details(exc), "details": output} if status == "error" else None,
            )
            raise
        else:
            await self.write("ended", span_id, parent_id, name, kind, status="ok", duration_ms=round((time.monotonic() - started) * 1000), payload=output)
        finally:
            _parent.reset(token)


def model_callbacks(trace, *, metered_tools=(), model_name=None):
    """Build lazily so demo/API imports need no model SDK installation."""
    from langchain_core.callbacks import AsyncCallbackHandler

    class LocalCallbacks(AsyncCallbackHandler):
        raise_error = True

        def __init__(self):
            self.active = {}
            self.parent_id = _parent.get()
            self.budget_error = None

        async def begin(self, run_id, name, kind, payload):
            key = str(run_id)
            self.active[key] = (time.monotonic(), name, kind)
            await trace.write("started", key, self.parent_id, name, kind, payload=payload)

        async def finish(self, run_id, payload=None, error=None):
            key = str(run_id)
            entry = self.active.pop(key, None)
            if entry is None:
                return
            started, name, kind = entry
            await trace.write(
                "ended",
                key,
                self.parent_id,
                name,
                kind,
                status="error" if error else "ok",
                duration_ms=round((time.monotonic() - started) * 1000),
                error_type=type(error).__name__ if error else None,
                payload={"error": error_details(error)} if error else payload,
            )

        async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            body = [[{"role": getattr(m, "type", "unknown"), "content": visible_text(m.content)} for m in batch] for batch in messages]
            await self.begin(run_id, model_name or (serialized or {}).get("name", "model"), "model", body)
            try:
                size = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + trace.settings.max_output_tokens
                size += sum(len(json.dumps(t.args, ensure_ascii=False).encode("utf-8")) for t in metered_tools)
                await trace.store.reserve(trace.run_id, model_tokens=size)
            except Exception as exc:
                self.budget_error = exc
                await self.finish(run_id, error=exc)
                raise

        async def on_llm_end(self, response, *, run_id, **kwargs):
            answers, usage = [], 0
            for batch in response.generations:
                for generation in batch:
                    message = getattr(generation, "message", None)
                    answers.append({"content": visible_text(getattr(message, "content", generation.text)), "tool_calls": getattr(message, "tool_calls", [])})
                    usage += (getattr(message, "usage_metadata", None) or {}).get("total_tokens", 0)
            if usage:
                await trace.store.mutate(trace.run_id, lambda r: r["usage"].update(reported_model_tokens=r["usage"].get("reported_model_tokens", 0) + usage))
            await self.finish(run_id, payload={"answers": answers, "reported_tokens": usage})

        async def on_llm_error(self, error, *, run_id, **kwargs):
            await self.finish(run_id, error=error)

        async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            name = (serialized or {}).get("name", "tool")
            await self.begin(run_id, name, "tool", kwargs.get("inputs") or input_str)
            # All tools now use the native call path with their original
            # schemas. Reserve here; there is no separate search wrapper.
            try:
                await trace.store.reserve(trace.run_id, tool_calls=1)
            except Exception as exc:
                self.budget_error = exc
                await self.finish(run_id, error=exc)
                raise

        async def on_tool_end(self, output, *, run_id, **kwargs):
            await self.finish(run_id, payload=output)

        async def on_tool_error(self, error, *, run_id, **kwargs):
            await self.finish(run_id, error=error)

    return LocalCallbacks()
