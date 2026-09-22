"""Local, owner-scoped research tracing and rotating operational logs.

Trace records share the durable event sequence, so reconnects and exports use
the same cursor. No telemetry service or API key is involved. Callback handlers
hold no asyncio primitives: native subagents may call them on their own loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import traceback
from contextlib import asynccontextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from uuid import uuid4

from . import extract
from .audit import audit_request, audit_response, bounded, request_summary, response_summary, shared_prefix
from .output import visible_text
from .store import REPORT_CALL_OUTPUT, RESEARCH_TURN_OUTPUT

_parent: ContextVar[str | None] = ContextVar("research_span", default=None)
# Workflow phase and cycle for metrics. Callbacks copy it when they are built,
# because native subagents invoke them on another event loop.
metric_scope: ContextVar[dict | None] = ContextVar("research_metric_scope", default=None)
METRIC_KEYS = ("cycle", "phase", "config_node", "purpose", "skill", "agent_name", "unit_id", "execution_id", "contract")
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


def redact_content(content, secrets=(), max_chars=20000):
    """Redact a message body without changing what kind of value it is.

    ``redact`` answers an over-long value with a ``{"preview", "truncated"}``
    object, which suits a trace payload. A tool message that later becomes
    evidence must stay text: the object used to be stringified into the excerpt
    of every page longer than the limit.
    """
    if isinstance(content, str):
        cleaned = redact(content, secrets, max(len(content) * 8 + 256, max_chars))
        return cleaned[:max_chars] if isinstance(cleaned, str) else content[:max_chars]
    if isinstance(content, list):
        blocks = []
        for block in content[:200]:
            if isinstance(block, str):
                blocks.append(redact_content(block, secrets, max_chars))
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                blocks.append({**block, "text": redact_content(block["text"], secrets, max_chars)})
            else:
                blocks.append(redact(block, secrets, max_chars))
        return blocks
    return redact(content, secrets, max_chars)


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


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _fingerprint(message):
    """Identity of one request message: a change anywhere in it ends the reusable prefix."""
    body = [getattr(message, "type", None), getattr(message, "content", None), getattr(message, "tool_calls", None) or None, getattr(message, "tool_call_id", None)]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _first(*values):
    return next((value for value in map(_count, values) if value is not None), None)


def usage_details(message, llm_output=None):
    """Normalize provider token usage across SDK shapes; unknown stays None.

    LangChain ``usage_metadata`` comes first. Raw usage covers OpenAI-compatible
    fields, DeepSeek prompt-cache hits and Anthropic cache reads.
    """
    meta = getattr(message, "usage_metadata", None) or {}
    response = getattr(message, "response_metadata", None) or {}
    raw = response.get("token_usage") or response.get("usage") or (llm_output or {}).get("token_usage") or {}
    raw = raw if isinstance(raw, dict) else {}
    input_details = meta.get("input_token_details") or {}
    output_details = meta.get("output_token_details") or {}
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    usage = {
        "input_tokens": _first(meta.get("input_tokens"), raw.get("prompt_tokens"), raw.get("input_tokens")),
        "output_tokens": _first(meta.get("output_tokens"), raw.get("completion_tokens"), raw.get("output_tokens")),
        "total_tokens": _first(meta.get("total_tokens"), raw.get("total_tokens")),
        "cache_read_tokens": _first(input_details.get("cache_read"), raw.get("prompt_cache_hit_tokens"), prompt_details.get("cached_tokens"), raw.get("cache_read_input_tokens")),
        "reasoning_tokens": _first(output_details.get("reasoning"), completion_details.get("reasoning_tokens")),
    }
    if usage["total_tokens"] is None and usage["input_tokens"] is not None and usage["output_tokens"] is not None:
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


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
            inputs = {"query": inputs} if role in {"search", "data"} else {}
    if not isinstance(inputs, dict):
        return {}
    if role in {"search", "data"}:
        value = next((inputs[key] for key in ("query", "q", "keyword", "keywords", "search_query", "question", "text") if isinstance(inputs.get(key), str)), None)
        return {"query": " ".join(value.split())[:300]} if value else {}
    if role == "read":
        value = next((inputs[key] for key in ("url", "uri", "link") if isinstance(inputs.get(key), str)), None)
        try:
            return {"url": safe_http_url(value)[:2000]} if value else {}
        except ValueError:
            return {}
    return {}


HTTP_STATUS = re.compile(r"(?i)\b(?:status(?:\s+code)?|HTTP(?:/[\d.]+)?)\s*:?\s*([1-5]\d\d)\b")
ERROR_NAME = re.compile(r"\b([A-Z][A-Za-z]*(?:Error|Exception|Timeout))\b")
EMPTY_RESULT = re.compile(r"(?i)\bno (?:readable|usable|extractable) (?:page )?content\b|\bempty (?:page|content|response|result)\b")


def request_key(inputs, secrets=()):
    """Identity of a tool request, so identical repeats are countable without storing arguments."""
    if isinstance(inputs, str):
        try:
            inputs = json.loads(inputs)
        except ValueError:
            pass
    encoded = json.dumps(redact(inputs, secrets, 256000), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def returned_error_type(content):
    """Coarse label for a tool result that reports an error; only metrics group by it."""
    text = (content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str))[:2000]
    if match := HTTP_STATUS.search(text):
        return f"HTTP {match.group(1)}"
    if match := ERROR_NAME.search(text):
        return match.group(1)
    return "EmptyContent" if EMPTY_RESULT.search(text) else "ToolReturnedError"


def model_callbacks(trace, *, metered_tools=(), model_name=None, scope=None, output_cap=None):
    """Build lazily so demo/API imports need no model SDK installation."""
    from langchain_core.callbacks import AsyncCallbackHandler

    from .activity import HIDDEN_TOOLS, NATIVE_ROLES
    from .contracts import ResearchError, utcnow
    from .sources import fetched_source, observed_sources

    # Keep the caller's dict: execute_role adds execution_id after building us.
    scope = {} if scope is None else scope
    for key, value in (metric_scope.get() or {}).items():
        scope.setdefault(key, value)
    roles = {**NATIVE_ROLES, **{source.tool: source.role for source in trace.settings.sources}}
    # Source tools DeepResearch builds itself account for their own calls and
    # answer with a stop instruction when the budget runs out (channels.SearchBudget),
    # so reserving here too would both double-count and reintroduce the fatal error.
    self_metered = {source.tool for source in trace.settings.sources if source.kind == "channel" or (source.kind == "mcp" and source.server in trace.settings.mcp_servers)}
    # Full request/response audit follows the same content-capture switch.
    audited = trace.settings.trace_capture_content and getattr(trace.settings, "llm_audit", True)
    tool_audited = trace.settings.trace_capture_content and getattr(trace.settings, "tool_audit", True)
    counters = ("model_calls", "model_errors", "unreported_model_calls", "tool_calls", "tool_errors", "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "reasoning_tokens", "max_input_tokens")

    async def record_model(key, details):
        # Metrics describe research; losing one record must never stop it.
        try:
            await trace.store.record_model_call(trace.run_id, key, {**{k: scope[k] for k in METRIC_KEYS if scope.get(k) is not None}, **details})
        except Exception as exc:
            logger.warning(json.dumps({"event": "model_metrics_failed", "error": type(exc).__name__}))

    async def audit(key, details, request=None):
        """Complete prompt and answer records; like metrics, never allowed to stop research."""
        if not audited:
            return
        try:
            if request is None:
                await trace.store.record_llm_response(trace.run_id, key, details)
            else:
                messages, tools = request
                await trace.store.record_llm_request(trace.run_id, key, details, messages, tools)
        except Exception as exc:
            logger.warning(json.dumps({"event": "llm_audit_failed", "error": type(exc).__name__}))

    class LocalCallbacks(AsyncCallbackHandler):
        raise_error = True

        def __init__(self):
            self.active = {}
            self.reservations = {}
            # Output tokens a research turn did not reserve; charged if the
            # provider never reports usage, so unknown spend stays conservative.
            self.shortfall = {}
            self.parent_id = _parent.get()
            self.budget_error = None
            self.provider_error = None
            self.totals = dict.fromkeys(counters, 0)
            # Calls of one agent loop share its execution ID; a conversion's
            # bounded retries share this handler instead.
            self.group = "calls-" + uuid4().hex
            # Message fingerprints of the previous request per engine node (the
            # agent's model node and the compaction node are separate threads).
            self.previous = {}

        def elapsed_ms(self, run_id):
            entry = self.active.get(str(run_id))
            return round((time.monotonic() - entry[0]) * 1000) if entry else None

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
                    reserved = self.reservations.pop(key, None)
                    if kind == "model":
                        self.totals["model_errors"] += 1
                        await record_model(key, {"status": "interrupted", "ended_at": utcnow(), "duration_ms": self.elapsed_ms(key), "error_code": error.code, "estimated_tokens": reserved})
                        await audit(key, {"status": "interrupted", "ended_at": utcnow(), "duration_ms": self.elapsed_ms(key), "error": {"code": error.code}})
                    await self.finish(key, error=error)

        async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            self.provider_error = None
            body = [[{"role": getattr(m, "type", "unknown"), "content": visible_text(m.content)} for m in batch] for batch in messages]
            name = model_name or (serialized or {}).get("name", "model")
            await self.begin(run_id, name, "model", body)
            self.totals["model_calls"] += 1
            started_at = utcnow()
            engine_node = (kwargs.get("metadata") or {}).get("langgraph_node")
            # How much of this request repeats the previous one of the same
            # thread from its first message on. A provider can only reuse that
            # prefix, so a low share names our request as the reason for cache
            # misses, and a high share with few cached tokens names the provider.
            sent = messages[0] if messages else []
            marks = [_fingerprint(m) for m in sent]
            before = self.previous.get(engine_node)
            self.previous[engine_node] = marks
            shared = shared_prefix(before, marks) if before is not None else None
            await record_model(
                str(run_id),
                {
                    "model": name,
                    "status": "running",
                    "started_at": started_at,
                    # The engine graph node making the call: the agent's model
                    # node, or the summarization middleware compacting its context.
                    **({"engine_node": engine_node} if isinstance(engine_node, str) else {}),
                    "prompt_messages": sum(len(batch) for batch in body),
                    "prompt_chars": sum(len(message["content"] or "") for batch in body for message in batch),
                    # None on the first request of a thread: nothing precedes it.
                    **({"prefix_messages": shared, "prefix_chars": sum(len(message["content"] or "") for message in body[0][:shared])} if shared is not None else {}),
                },
            )
            if audited:
                # Recorded before reservation, so a call rejected by the budget
                # still shows what it would have sent.
                try:
                    normalized, tools, params = audit_request(messages[0] if messages else [], kwargs.get("invocation_params"), trace.secrets)
                    metadata = kwargs.get("metadata") or {}
                    details = {
                        **{key: scope[key] for key in METRIC_KEYS if scope.get(key) is not None},
                        "model": name,
                        "status": "running",
                        "started_at": started_at,
                        # The engine graph node that made the call, e.g. the agent's model
                        # node or a summarization middleware compressing its context.
                        "node": metadata.get("langgraph_node") if isinstance(metadata.get("langgraph_node"), str) else None,
                        "group": scope.get("execution_id") or self.group,
                        "params": params,
                        "batch_size": len(messages),
                        **request_summary(normalized, tools),
                    }
                    await audit(str(run_id), details, (normalized, tools))
                except Exception as exc:
                    logger.warning(json.dumps({"event": "llm_audit_failed", "error": type(exc).__name__}))
            try:
                from langchain_core.messages.utils import count_tokens_approximately

                # Offline framework estimation, not UTF-8 bytes billed as tokens.
                # Escaped Unicode gives multilingual text a conservative weight.
                # No tokenizer download or provider request occurs here.
                payload = json.dumps({"messages": body, "tools": [t.args for t in metered_tools]}, ensure_ascii=True)
                # Research keeps a reserve for the report; writing may use it, and
                # so may converting a finished step's notes, whose research is
                # already paid for and would otherwise be lost.
                writing = scope.get("phase") in {"synthesis", "follow_up"}
                purpose = "report" if writing or scope.get("purpose") == "conversion" else "research"
                # Reserve what a call realistically writes. Reserving the whole
                # output cap made budgets run out at a fraction of real spend, and
                # parallel report sections could not fit a small budget at all;
                # settlement replaces the estimate with reported usage.
                # The node's own output cap when it has one (nodes.<name>.max_tokens).
                cap = output_cap or trace.settings.max_output_tokens
                output = min(cap, REPORT_CALL_OUTPUT if writing else RESEARCH_TURN_OUTPUT)
                size = count_tokens_approximately([("user", payload)]) + output
                await trace.store.reserve(trace.run_id, model_tokens=size, purpose=purpose)
                self.reservations[str(run_id)] = size
                if cap > output:
                    self.shortfall[str(run_id)] = cap - output
                await trace.store.event(trace.run_id, "usage.reserved", {"model_call_id": str(run_id), "estimated_tokens": size})
            except Exception as exc:
                self.budget_error = exc
                self.totals["model_errors"] += 1
                code = getattr(exc, "code", type(exc).__name__)
                await record_model(str(run_id), {"status": "error", "ended_at": utcnow(), "duration_ms": self.elapsed_ms(run_id), "error_code": code})
                await audit(str(run_id), {"status": "error", "ended_at": utcnow(), "duration_ms": self.elapsed_ms(run_id), "error": {"code": code, "stage": "reservation"}})
                await self.finish(run_id, error=exc)
                raise

        async def on_llm_end(self, response, *, run_id, **kwargs):
            answers, usage, known_usage = [], 0, False
            details = dict.fromkeys(("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "reasoning_tokens"))
            finish_reason = response_model = None
            for batch in response.generations:
                for generation in batch:
                    message = getattr(generation, "message", None)
                    answers.append({"content": visible_text(getattr(message, "content", generation.text)), "tool_calls": getattr(message, "tool_calls", [])})
                    reported = usage_details(message, response.llm_output)
                    for field, value in reported.items():
                        if value is not None:
                            details[field] = (details[field] or 0) + value
                    metadata = getattr(message, "response_metadata", None) or {}
                    finish_reason = metadata.get("finish_reason") or finish_reason
                    response_model = metadata.get("model_name") or response_model
                    # A researcher's visible sentence beside its tool calls is
                    # the live progress note. Hidden reasoning never qualifies.
                    calls = answers[-1]["tool_calls"] or []
                    note = " ".join((answers[-1]["content"] or "").split())
                    if note and scope.get("unit_id") and any(call.get("name") not in HIDDEN_TOOLS for call in calls):
                        try:
                            await trace.store.event(trace.run_id, "activity.note", {**scope, "text": redact(note[:400], trace.secrets, 400)})
                        except Exception as exc:  # A progress note must never stop research or metering.
                            logger.warning(json.dumps({"event": "activity_note_failed", "error": type(exc).__name__}))
                    if reported["total_tokens"] is not None:
                        known_usage = True
                        usage += reported["total_tokens"]
            reserved = self.reservations.pop(str(run_id), None)
            shortfall = self.shortfall.pop(str(run_id), 0)
            if not known_usage and reserved is not None and shortfall:
                # Unknown usage keeps the conservative charge the full cap implies.

                def conservative(run):
                    run["usage"]["model_tokens"] += shortfall

                await trace.store.mutate(trace.run_id, conservative)
                reserved += shortfall
            for field, value in details.items():
                self.totals[field] += value or 0
            self.totals["max_input_tokens"] = max(self.totals["max_input_tokens"], details["input_tokens"] or 0)
            if not known_usage:
                self.totals["unreported_model_calls"] += 1
            model_record = {
                **details,
                "ended_at": utcnow(),
                "duration_ms": self.elapsed_ms(run_id),
                "usage_reported": known_usage,
                "estimated_tokens": reserved,
                "finish_reason": finish_reason,
                "response_model": response_model,
                "output_chars": sum(len(answer["content"] or "") for answer in answers),
                "tool_calls": sum(len(answer["tool_calls"] or []) for answer in answers),
            }
            exchange = {"ended_at": model_record["ended_at"], "duration_ms": model_record["duration_ms"], "usage": details, "usage_reported": known_usage, "response_model": response_model}
            if audited:
                try:
                    returned = audit_response(response, trace.secrets)
                    exchange.update(response=returned, **response_summary(returned))
                except Exception as exc:
                    logger.warning(json.dumps({"event": "llm_audit_failed", "error": type(exc).__name__}))
            if known_usage and reserved is not None:

                def settle(run):
                    run["usage"]["model_tokens"] += usage - reserved
                    run["usage"]["reported_model_tokens"] = run["usage"].get("reported_model_tokens", 0) + usage

                current = await trace.store.mutate(trace.run_id, settle)
                await trace.store.event(trace.run_id, "usage.settled", {"model_call_id": str(run_id), "reserved_tokens": reserved, "reported_tokens": usage})
                ceiling = current["budget"]["max_model_tokens"]
                if ceiling is not None and current["usage"]["model_tokens"] > ceiling:
                    self.budget_error = ResearchError("BUDGET_EXHAUSTED", "预算已用尽: max_model_tokens", recoverable=False)
                    await record_model(str(run_id), {**model_record, "status": "error", "error_code": self.budget_error.code})
                    await audit(str(run_id), {**exchange, "status": "error", "error": {"code": self.budget_error.code, "stage": "settlement"}})
                    await self.finish(run_id, payload={"answers": answers, "reported_tokens": usage}, error=self.budget_error)
                    raise self.budget_error
            # Missing usage or a failed call keeps the conservative reservation;
            # uncertainty must not become a free retry or negative accounting.
            await record_model(str(run_id), {**model_record, "status": "ok"})
            await audit(str(run_id), {**exchange, "status": "ok"})
            await self.finish(run_id, payload={"answers": answers, "reported_tokens": usage})

        async def on_llm_error(self, error, *, run_id, **kwargs):
            self.provider_error = provider_failure(error)
            reserved = self.reservations.pop(str(run_id), None)
            self.shortfall.pop(str(run_id), None)
            self.totals["model_errors"] += 1
            code = self.provider_error.code if self.provider_error else type(error).__name__
            await record_model(str(run_id), {"status": "error", "ended_at": utcnow(), "duration_ms": self.elapsed_ms(run_id), "error_code": code, "estimated_tokens": reserved})
            # Provider exception text may echo request headers; keep typed facts only.
            status = getattr(error, "status_code", None)
            await audit(str(run_id), {"status": "error", "ended_at": utcnow(), "duration_ms": self.elapsed_ms(run_id), "error": {"code": code, "type": type(error).__name__, **({"status_code": status} if isinstance(status, int) else {})}})
            await self.finish(run_id, error=error)

        async def exchange(self, run_id, details, payloads):
            """Keep a tool call's own arguments and answer, whole.

            ``research_tool_call`` holds the metrics a reader groups by and a
            hash of the arguments, so a research with only MCP tools left no
            answer to "what did we send, what came back". Bodies are bounded by
            ``audit_max_chars`` and say so when they do not fit, because silent
            clipping is worse than no record at all.
            """
            if not tool_audited:
                return
            try:
                limit = trace.settings.audit_max_chars
                kept = {name: bounded(value, trace.secrets, limit) for name, value in payloads.items() if value is not None}
                measured = kept.pop("content", None)
                if measured is not None:
                    details = {**details, "chars": measured["chars"], "truncated": measured["truncated"]}
                    kept["content"] = measured["value"]
                await trace.store.record_tool_exchange(trace.run_id, str(run_id), details, payloads={name: value["value"] if isinstance(value, dict) and "value" in value else value for name, value in kept.items()})
            except Exception as exc:
                logger.warning(json.dumps({"event": "tool_audit_failed", "error": type(exc).__name__}))

        async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            name = (serialized or {}).get("name", "tool")
            inputs = kwargs.get("inputs") or input_str
            await self.begin(run_id, name, "tool", inputs)
            role = roles.get(name)
            detail = redact(tool_detail(role, inputs), trace.secrets, 2000) if role else {}
            details = {**scope, "tool_name": name, "role": role, **detail}
            request = request_key(inputs, trace.secrets)
            started = utcnow()
            await trace.store.record_call(trace.run_id, str(run_id), {**details, "started_at": started, "status": "running", "span_id": str(run_id), "request_key": request})
            await self.exchange(run_id, {**details, "started_at": started}, {"arguments": inputs})
            self.totals["tool_calls"] += 1
            await trace.store.event(trace.run_id, "activity.tool.started", {**details, "call_id": str(run_id)})
            # Host and engine tools are reserved here; research sources reserve
            # their own call so an exhausted budget ends the step gracefully.
            if name in self_metered:
                return
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
            if status == "error":
                self.totals["tool_errors"] += 1
            # Observe only the model-visible body. Do not mine provider headers
            # or mutate the object handed to the native model/tool loop.
            safe_content = redact(content, trace.secrets, 256000)
            icons = extract.declared_icons(safe_content) if status == "success" else {}
            found = observed_sources(safe_content, connector=spec.name if spec else name, origin=spec.origin if spec else "runtime", icons=icons) if status == "success" else []
            artifact = getattr(output, "artifact", None)
            fetched = fetched_source(redact(artifact, trace.secrets), connector=spec.name, origin=spec.origin) if status == "success" and spec and spec.kind in {"native", "channel"} else None
            if fetched:
                found = [source for source in found if source["id"] != fetched["id"]] + [fetched]
            page = {"url": fetched["url"], "title": fetched["title"]} if fetched else {}
            if isinstance(artifact, dict) and artifact.get("schema") == "deepresearch.budget_stop.v1":
                # The tool refused: the step's allowance, the run's ceiling or
                # its time is used up. Nothing was searched, so it must not be
                # counted as a search anywhere a reader or an operator looks.
                page["budget_stop"] = artifact.get("reason") or "step"
                # How much of the allowance was already gone, on every refusal
                # and not only on the first one of each reason.
                page.update({key: artifact[key] for key in ("used", "limit", "scope") if artifact.get(key) is not None})
            # Provider-based sources say which backend answered and what failed first.
            if isinstance(artifact, dict) and artifact.get("provider"):
                # Why a provider failed, not only that it did: the message and
                # the status code are what tell a broken key from a rate limit.
                kept = ("provider", "type", "status", "kind", "ms", "http_status", "cooldown_seconds", "message")
                attempts = [{key: item.get(key) for key in kept if item.get(key) is not None} for item in (artifact.get("attempts") or []) if isinstance(item, dict)]
                page.update(provider=artifact["provider"], provider_type=artifact.get("provider_type"), attempts=attempts, failovers=sum(item["status"] in {"error", "empty"} for item in attempts))
            elif status == "error" and spec and spec.kind == "channel":
                from .channels import failed_attempts

                attempts = failed_attempts(safe_content)
                if attempts:
                    page.update(attempts=attempts, failovers=len(attempts))
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
                    # Returned size drives the researcher's next prompt; the text is not stored here.
                    "output_chars": len(content) if isinstance(content, str) else len(json.dumps(content, ensure_ascii=False, default=str)),
                    **({"error_type": returned_error_type(safe_content)} if status == "error" else {}),
                    **page,
                },
                found,
            )
            await trace.store.event(trace.run_id, "activity.tool.completed", call)
            # What the row deliberately leaves out: the answer itself, whole.
            ended = {key: call.get(key) for key in ("ended_at", "duration_ms")}
            await self.exchange(run_id, {**ended, "status": status, **({"error_type": call.get("error_type")} if status == "error" else {})}, {"content": content, "artifact": getattr(output, "artifact", None)})
            returned_error = ResearchError("TOOL_RETURNED_ERROR", "Native tool returned an error result") if status == "error" else None
            await self.finish(run_id, payload=output, error=returned_error)

        async def on_tool_error(self, error, *, run_id, **kwargs):
            entry = self.active.get(str(run_id))
            if entry is None:
                return
            self.totals["tool_errors"] += 1
            call = await trace.store.record_call(
                trace.run_id,
                str(run_id),
                {**scope, "status": "error", "ended_at": utcnow(), "duration_ms": self.elapsed_ms(run_id), "tool_name": entry[1] if entry else "tool", "error_type": type(error).__name__},
            )
            await trace.store.event(trace.run_id, "activity.tool.completed", call)
            # The message the model was handed: the row keeps only its bucket.
            ended = {key: call.get(key) for key in ("ended_at", "duration_ms")}
            await self.exchange(run_id, {**ended, "status": "error", "error_type": type(error).__name__}, {"content": str(error)})
            await self.finish(run_id, error=error)

    return LocalCallbacks()
