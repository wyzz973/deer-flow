"""Complete, owner-scoped records of every model request and response.

The trace keeps bounded span payloads for its timeline. This module keeps what
each model actually saw (system prompt, task, earlier turns, tool results, tool
schemas and request parameters) and exactly what it returned, so a person can
audit any call. An agent loop resends its whole prefix on every turn, so each
unique message is stored once, compressed, and a call refers to message hashes.

Known credential values, bearer tokens and credential query parameters are
removed. Nothing else is shortened below a generous per-message ceiling, and
hidden reasoning a provider returned is kept for the owner's audit.
"""

from __future__ import annotations

import hashlib
import json
import re
import zlib

# One message beyond this is cut with an explicit marker; a researcher turn is
# normally far below it. Binary content (images, files) is summarized instead.
MESSAGE_LIMIT = 1_000_000
PREVIEW = 240
# Request parameters worth auditing. Client objects, keys and endpoints are not.
PARAMS = (
    "model",
    "model_name",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "stop",
    "tool_choice",
    "parallel_tool_calls",
    "reasoning_effort",
    "reasoning",
    "thinking",
    "stream",
    "n",
    "seed",
    "response_format",
    "frequency_penalty",
    "presence_penalty",
    "logprobs",
    "top_logprobs",
    "extra_body",
    "verbosity",
    "service_tier",
    "_type",
)
ROLES = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool", "function": "function", "developer": "developer", "AIMessageChunk": "assistant"}
BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"']+")
QUERY_SECRET = re.compile(r"(?i)([?&](?:token|key|api_key|apikey|access_token|signature|sig|password|secret|x-amz-signature)=)[^&\s\"']+")
CREDENTIAL_KEY = re.compile(r"authorization|cookie|csrf|password|secret|api[_-]?key|access[_-]?token|refresh[_-]?token", re.I)


def scrub_text(text, secrets=()):
    """Remove known credential values and self-describing credential tokens."""
    for secret in sorted((s for s in secrets if isinstance(s, str) and len(s) >= 4), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = BEARER.sub("Bearer [redacted]", text)
    return QUERY_SECRET.sub(r"\1[redacted]", text)


def scrub(value, secrets=(), depth=0):
    if depth > 30:
        return "[depth limit]"
    if isinstance(value, str):
        return scrub_text(value, secrets)
    if isinstance(value, dict):
        return {str(key): "[redacted]" if CREDENTIAL_KEY.search(str(key)) and isinstance(item, str) else scrub(item, secrets, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(item, secrets, depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_text(str(value), secrets)


def bounded(value, secrets=(), max_chars=MESSAGE_LIMIT):
    """A payload kept whole where it fits, and marked where it does not.

    Silent clipping is the worst of both: the reader cannot tell whether what
    they are looking at is the whole answer, so a record that had to cut says so.
    """
    cleaned = scrub(value, secrets)
    text = cleaned if isinstance(cleaned, str) else json.dumps(cleaned, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return {"value": cleaned, "chars": len(text), "truncated": False}
    return {"value": text[:max_chars], "chars": len(text), "truncated": True}


def _bounded(text):
    text = text if isinstance(text, str) else str(text)
    if len(text) <= MESSAGE_LIMIT:
        return text
    return text[:MESSAGE_LIMIT] + f"\n…[audit ceiling: {len(text) - MESSAGE_LIMIT} more characters not stored]"


def _block(item):
    if isinstance(item, str):
        return {"type": "text", "text": _bounded(item)}
    if not isinstance(item, dict):
        return {"type": "unknown", "text": _bounded(item)}
    kind = item.get("type")
    if kind in {"text", "output_text", "input_text"}:
        return {"type": "text", "text": _bounded(item.get("text") or "")}
    if kind in {"thinking", "reasoning", "redacted_thinking"}:
        return {"type": "reasoning", "text": _bounded(item.get("thinking") or item.get("reasoning") or item.get("text") or "")}
    if kind in {"image_url", "image", "file", "audio", "input_audio", "video"}:
        image = item.get("image_url")
        url = image.get("url") if isinstance(image, dict) else image if isinstance(image, str) else item.get("url")
        data = item.get("base64") or item.get("data")
        if isinstance(url, str) and url.startswith("data:"):
            data, url = url, None
        return {"type": kind, "mime_type": item.get("mime_type") or item.get("mimeType"), "url": url, "bytes": len(data) if isinstance(data, str) else None}
    if kind in {"tool_use", "tool_call", "function_call"}:
        return {"type": "tool_call", "id": item.get("id"), "name": item.get("name"), "args": item.get("input") or item.get("args") or item.get("arguments")}
    return {str(key): _bounded(value) if isinstance(value, str) else value for key, value in list(item.items())[:50]}


def _content(value):
    if isinstance(value, list):
        return [_block(item) for item in value[:1000]]
    return _bounded(value if isinstance(value, str) else "" if value is None else json.dumps(value, ensure_ascii=False, default=str))


def audit_message(message):
    """A provider-neutral view of one chat message, as the model received it."""
    kind = getattr(message, "type", None) or "unknown"
    role = getattr(message, "role", None) if kind == "chat" else ROLES.get(kind, kind)
    item = {"role": role or "user", "content": _content(getattr(message, "content", ""))}
    for key in ("name", "tool_call_id"):
        value = getattr(message, key, None)
        if value:
            item[key] = value
    if kind == "tool" and getattr(message, "status", "success") != "success":
        item["status"] = message.status
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        item["tool_calls"] = [{"id": call.get("id"), "name": call.get("name"), "args": call.get("args")} for call in calls]
    invalid = getattr(message, "invalid_tool_calls", None) or []
    if invalid:
        item["invalid_tool_calls"] = [{"id": call.get("id"), "name": call.get("name"), "args": call.get("args"), "error": call.get("error")} for call in invalid]
    extra = getattr(message, "additional_kwargs", None) or {}
    reasoning = extra.get("reasoning_content") or extra.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        item["reasoning"] = _bounded(reasoning)
    return item


def audit_request(messages, invocation_params, secrets=()):
    """Normalize what one model call was given: messages, tool schemas, parameters."""
    params = invocation_params if isinstance(invocation_params, dict) else {}
    kept = {key: params[key] for key in PARAMS if params.get(key) is not None}
    tools = params.get("tools") or params.get("functions")
    return (
        scrub([audit_message(message) for message in messages], secrets),
        scrub(tools, secrets) if isinstance(tools, list) and tools else None,
        scrub(kept, secrets),
    )


def audit_response(response, secrets=()):
    generations = []
    for batch in getattr(response, "generations", None) or []:
        for generation in batch:
            message = getattr(generation, "message", None)
            item = audit_message(message) if message is not None else {"role": "assistant", "content": _bounded(getattr(generation, "text", ""))}
            metadata = getattr(message, "response_metadata", None) or {}
            info = getattr(generation, "generation_info", None) or {}
            item["finish_reason"] = metadata.get("finish_reason") or info.get("finish_reason")
            item["response_model"] = metadata.get("model_name") or metadata.get("model")
            usage = getattr(message, "usage_metadata", None)
            if usage:
                item["usage"] = dict(usage)
            generations.append(item)
    return scrub({"generations": generations}, secrets)


def encode(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest(), zlib.compress(raw, 6)


def decode(blob):
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content or [] if isinstance(block, dict) and block.get("type") in {"text", "reasoning"})


def tool_name(schema):
    if not isinstance(schema, dict):
        return None
    function = schema.get("function")
    return (function.get("name") if isinstance(function, dict) else None) or schema.get("name")


def request_summary(messages, tools):
    roles = {}
    for message in messages:
        roles[message["role"]] = roles.get(message["role"], 0) + 1
    last = next((message for message in reversed(messages) if message["role"] in {"user", "tool"}), None)
    return {
        "message_count": len(messages),
        "roles": roles,
        "system_chars": sum(len(_text(message["content"])) for message in messages if message["role"] == "system"),
        "prompt_chars": sum(len(_text(message["content"])) for message in messages),
        "tools": [name for name in map(tool_name, tools or []) if name],
        "last_input": {"role": last["role"], "name": last.get("name"), "preview": " ".join(_text(last["content"]).split())[:PREVIEW]} if last else None,
    }


def response_summary(response):
    generation = (response.get("generations") or [{}])[0]
    text = _text(generation.get("content"))
    return {
        "preview": " ".join(text.split())[:PREVIEW],
        "output_chars": len(text),
        "reasoning_chars": len(generation.get("reasoning") or ""),
        "tool_calls": [call.get("name") for call in generation.get("tool_calls") or []],
        "finish_reason": generation.get("finish_reason"),
    }


def openai_request(detail):
    """The call as an OpenAI-compatible Chat Completions body, for copying and replay."""
    messages = []
    for message in detail.get("messages") or []:
        if message is None:
            continue
        entry = {"role": message["role"], "content": message.get("content")}
        if message.get("tool_calls"):
            entry["tool_calls"] = [{"id": call.get("id"), "type": "function", "function": {"name": call.get("name"), "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False)}} for call in message["tool_calls"]]
        if message["role"] == "tool":
            entry["tool_call_id"] = message.get("tool_call_id")
        elif message.get("name"):
            entry["name"] = message["name"]
        messages.append(entry)
    params = detail.get("params") or {}
    body = {key: value for key, value in params.items() if key not in {"_type", "model_name", "stream"}}
    body["model"] = params.get("model") or params.get("model_name") or detail.get("model")
    body["messages"] = messages
    if detail.get("tools"):
        body["tools"] = detail["tools"]
    return body


def shared_prefix(previous, current):
    count = 0
    for left, right in zip(previous, current, strict=False):
        if left != right:
            break
        count += 1
    return count
