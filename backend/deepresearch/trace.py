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


def provider_failure(error):
    """Classify provider status without exposing its free-form response body."""
    from .contracts import ResearchError

    status = getattr(error, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool):
        return None
    known = {
        401: ("MODEL_AUTH_REQUIRED", "模型服务鉴权失败，请检查配置的 API 凭据"),
        403: ("MODEL_ACCESS_DENIED", "模型服务拒绝访问，请检查账户权限与模型配置"),
        402: ("MODEL_BILLING_REQUIRED", "模型服务要求处理余额或计费状态，请检查 API 账户"),
        429: ("MODEL_RATE_LIMIT", "模型服务限流，请稍后从检查点重试"),
        408: ("MODEL_TIMEOUT", "模型服务请求超时，可从检查点重试"),
        504: ("MODEL_TIMEOUT", "模型服务请求超时，可从检查点重试"),
    }
    if status in known:
        return ResearchError(*known[status])
    if isinstance(status, int) and status >= 500:
        return ResearchError("MODEL_UNAVAILABLE", "模型服务暂时不可用，可从检查点重试")
    return None


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
        if kind == "workflow":
            # Recovery keeps old attempts visible while explicitly terminating
            # callback spans whose parent already reached a terminal state.
            await self.store.reconcile_trace(self.run_id)
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


def tool_detail(role, inputs):
    """Show what a search or read step is doing, from the call's own arguments.

    Only the operator-declared role decides which common argument is shown.
    Tool results are never inspected here, and other tools show just a name.
    """
    from .contracts import safe_http_url

    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs)
        except ValueError:
            inputs = {"query": inputs} if role == "search" else {}
    if not isinstance(inputs, dict):
        return {}
    if role == "search":
        value = next((inputs[key] for key in ("query", "q", "keywords", "search_query", "question") if isinstance(inputs.get(key), str)), None)
        return {"query": " ".join(value.split())[:300]} if value else {}
    if role == "read":
        value = next((inputs[key] for key in ("url", "uri", "link") if isinstance(inputs.get(key), str)), None)
        try:
            return {"url": safe_http_url(value)[:2000]} if value else {}
        except ValueError:
            return {}
    return {}


def model_callbacks(trace, *, metered_tools=(), model_name=None, scope=None):
    """Build lazily so demo/API imports need no model SDK installation."""
    from langchain_core.callbacks import AsyncCallbackHandler

    from .activity import HIDDEN_TOOLS, NATIVE_ROLES
    from .contracts import ResearchError, utcnow
    from .sources import fetched_source, observed_sources

    scope = scope or {}
    roles = {**NATIVE_ROLES, **{source.tool: source.role for source in trace.settings.sources}}

    class LocalCallbacks(AsyncCallbackHandler):
        raise_error = True

        def __init__(self):
            self.active = {}
            self.reservations = {}
            self.parent_id = _parent.get()
            self.budget_error = None
            self.provider_error = None

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
                payload={"error": error_details(error), "output": payload} if error else payload,
            )

        async def close(self):
            """Finalize missing callbacks only after the native worker drains.

            Unknown model usage retains its reservation. An interrupted tool
            result is never promoted to evidence or reported as successful.
            """
            error = ResearchError("NATIVE_CALLBACK_INTERRUPTED", "Native execution ended without a terminal callback")
            for key, (_, _, kind) in tuple(self.active.items()):
                if kind == "tool":
                    await self.on_tool_error(error, run_id=key)
                else:
                    self.reservations.pop(key, None)
                    await self.finish(key, error=error)

        async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            self.provider_error = None
            body = [[{"role": getattr(m, "type", "unknown"), "content": visible_text(m.content)} for m in batch] for batch in messages]
            await self.begin(run_id, model_name or (serialized or {}).get("name", "model"), "model", body)
            try:
                from langchain_core.messages.utils import count_tokens_approximately

                # Offline framework estimation, not UTF-8 bytes billed as tokens.
                # Escaped Unicode gives multilingual text a conservative weight.
                # No tokenizer download or provider request occurs here.
                payload = json.dumps({"messages": body, "tools": [t.args for t in metered_tools]}, ensure_ascii=True)
                size = count_tokens_approximately([("user", payload)]) + trace.settings.max_output_tokens
                await trace.store.reserve(trace.run_id, model_tokens=size)
                self.reservations[str(run_id)] = size
                await trace.store.event(trace.run_id, "usage.reserved", {"model_call_id": str(run_id), "estimated_tokens": size})
            except Exception as exc:
                self.budget_error = exc
                await self.finish(run_id, error=exc)
                raise

        async def on_llm_end(self, response, *, run_id, **kwargs):
            answers, usage, known_usage = [], 0, False
            for batch in response.generations:
                for generation in batch:
                    message = getattr(generation, "message", None)
                    answers.append({"content": visible_text(getattr(message, "content", generation.text)), "tool_calls": getattr(message, "tool_calls", [])})
                    # A researcher's visible sentence beside its tool calls is
                    # the live progress note. Hidden reasoning never qualifies.
                    calls = answers[-1]["tool_calls"] or []
                    note = " ".join((answers[-1]["content"] or "").split())
                    if note and scope.get("unit_id") and any(call.get("name") not in HIDDEN_TOOLS for call in calls):
                        try:
                            await trace.store.event(trace.run_id, "activity.note", {**scope, "text": redact(note[:400], trace.secrets, 400)})
                        except Exception as exc:  # A progress note must never stop research or metering.
                            logger.warning(json.dumps({"event": "activity_note_failed", "error": type(exc).__name__}))
                    total = (getattr(message, "usage_metadata", None) or {}).get("total_tokens")
                    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                        known_usage = True
                        usage += total
            reserved = self.reservations.pop(str(run_id), None)
            if known_usage and reserved is not None:

                def settle(run):
                    run["usage"]["model_tokens"] += usage - reserved
                    run["usage"]["reported_model_tokens"] = run["usage"].get("reported_model_tokens", 0) + usage

                current = await trace.store.mutate(trace.run_id, settle)
                await trace.store.event(trace.run_id, "usage.settled", {"model_call_id": str(run_id), "reserved_tokens": reserved, "reported_tokens": usage})
                ceiling = current["budget"]["max_model_tokens"]
                if ceiling is not None and current["usage"]["model_tokens"] > ceiling:
                    self.budget_error = ResearchError("BUDGET_EXHAUSTED", "预算已用尽: max_model_tokens", recoverable=False)
                    await self.finish(run_id, payload={"answers": answers, "reported_tokens": usage}, error=self.budget_error)
                    raise self.budget_error
            # Missing usage or a failed call keeps the conservative reservation;
            # uncertainty must not become a free retry or negative accounting.
            await self.finish(run_id, payload={"answers": answers, "reported_tokens": usage})

        async def on_llm_error(self, error, *, run_id, **kwargs):
            self.provider_error = provider_failure(error)
            self.reservations.pop(str(run_id), None)
            await self.finish(run_id, error=error)

        async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            name = (serialized or {}).get("name", "tool")
            await self.begin(run_id, name, "tool", kwargs.get("inputs") or input_str)
            role = roles.get(name)
            detail = redact(tool_detail(role, kwargs.get("inputs") or input_str), trace.secrets, 2000) if role else {}
            details = {**scope, "tool_name": name, "role": role, **detail}
            await trace.store.record_call(trace.run_id, str(run_id), {**details, "started_at": utcnow(), "status": "running", "span_id": str(run_id)})
            await trace.store.event(trace.run_id, "activity.tool.started", {**details, "call_id": str(run_id)})
            # All tools now use the native call path with their original
            # schemas. Reserve here; there is no separate search wrapper.
            try:
                await trace.store.reserve(trace.run_id, tool_calls=1)
            except Exception as exc:
                self.budget_error = exc
                await self.on_tool_error(exc, run_id=run_id)
                raise

        async def on_tool_end(self, output, *, run_id, **kwargs):
            entry = self.active.get(str(run_id))
            if entry is None:
                return  # A duplicate/end-after-rejection cannot revive a call.
            name = entry[1] if entry else getattr(output, "name", "tool")
            spec = next((s for s in trace.settings.sources if s.tool == name), None)
            status = "error" if getattr(output, "status", None) == "error" else "success"
            content = getattr(output, "content", output)
            # Observe only the model-visible body. Do not mine provider headers
            # or mutate the object handed to the native model/tool loop.
            safe_content = redact(content, trace.secrets, 256000)
            found = observed_sources(safe_content, connector=spec.name if spec else name, origin=spec.origin if spec else "runtime") if status == "success" else []
            fetched = fetched_source(redact(getattr(output, "artifact", None), trace.secrets), connector=spec.name, origin=spec.origin) if status == "success" and spec and spec.kind == "native" else None
            if fetched:
                found = [source for source in found if source["id"] != fetched["id"]] + [fetched]
            page = {"url": fetched["url"], "title": fetched["title"]} if fetched else {}
            call = await trace.store.record_call(
                trace.run_id,
                str(run_id),
                {
                    **scope,
                    "tool_name": name,
                    "provider_call_id": getattr(output, "tool_call_id", None),
                    "status": status,
                    "ended_at": utcnow(),
                    "duration_ms": round((time.monotonic() - entry[0]) * 1000) if entry else None,
                    **page,
                },
                found,
            )
            await trace.store.event(trace.run_id, "activity.tool.completed", call)
            returned_error = ResearchError("TOOL_RETURNED_ERROR", "Native tool returned an error result") if status == "error" else None
            await self.finish(run_id, payload=output, error=returned_error)

        async def on_tool_error(self, error, *, run_id, **kwargs):
            entry = self.active.get(str(run_id))
            if entry is None:
                return
            call = await trace.store.record_call(trace.run_id, str(run_id), {**scope, "status": "error", "ended_at": utcnow(), "tool_name": entry[1] if entry else "tool", "error_type": type(error).__name__})
            await trace.store.event(trace.run_id, "activity.tool.completed", call)
            await self.finish(run_id, error=error)

    return LocalCallbacks()
