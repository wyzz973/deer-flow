"""Parse ordinary chat replies without requiring provider JSON-mode support.

This is a syntax boundary, not a semantic repair engine. Invalid contracts are
returned to a bounded model correction step; evidence IDs are never invented.
"""

import json
import re

import yaml
from pydantic import ValidationError


def visible_text(content) -> str:
    """Exclude provider reasoning blocks; keep user-visible answer text only."""
    if isinstance(content, list):
        content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") in {"text", "output_text"})
    text = content if isinstance(content, str) else ""
    text = re.sub(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", "", text, flags=re.S | re.I)
    # A chat template that pre-fills "<think>" leaves only the closing tag in the
    # reply when the server runs no reasoning parser: everything before the last
    # one is reasoning (it used to end up in report sections, and a draft object
    # inside it was parsed instead of the final answer).
    closing = list(re.finditer(r"</think(?:ing)?>", text, flags=re.I))
    if closing:
        text = text[closing[-1].end() :]
    return text.strip()


def parse_contract(content, schema):
    """Accept fenced/embedded JSON and explicitly fenced YAML, then validate."""
    text = visible_text(content)
    best_error, best_score = "", 0

    def validate(value):
        nonlocal best_error, best_score
        try:
            return schema.model_validate(value)
        except ValidationError as error:
            # Prefer the complete reply over nested objects encountered by
            # raw_decode. Never echo input values, provider metadata or secrets.
            score = len(set(value) & set(schema.model_fields)) if isinstance(value, dict) else 0
            if score > best_score:
                best_score = score
                details = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]} for item in error.errors(include_input=False, include_url=False, include_context=False)[:8]]
                best_error = json.dumps(details, ensure_ascii=False)[:2000]
            raise

    candidates = [text]
    for language, body in re.findall(r"```(\w*)\s*\n(.*?)```", text, re.S):
        if language.lower() in {"yaml", "yml"}:
            try:
                return validate(yaml.safe_load(body))
            except (ValueError, TypeError, yaml.YAMLError):
                continue
        candidates.append(body.strip())
    decoder = json.JSONDecoder()
    # Every complete top-level object that satisfies the contract. A reply may
    # show an example before the real answer, so the largest one wins rather
    # than the first.
    accepted, syntax = [], ""
    for candidate in candidates:
        # raw_decode handles braces inside JSON strings correctly, unlike a
        # greedy regex. Bound attempts to avoid quadratic scans of long prose.
        starts = [m.start() for m in re.finditer(r"\{", candidate)][:100]
        covered = 0
        for start in starts:
            if start < covered:
                continue  # inside an object that was already decoded
            try:
                value, length = decoder.raw_decode(candidate[start:])
            except ValueError as error:
                syntax = syntax or f"line {getattr(error, 'lineno', '?')}: {getattr(error, 'msg', 'invalid JSON')}"
                continue
            try:
                accepted.append((length, validate(value)))
            except (ValueError, TypeError):
                continue  # a wrapper such as {"result": {...}}: look inside it
            covered = start + length
    if accepted:
        return max(accepted, key=lambda item: item[0])[1]
    # Weak models write almost-JSON: trailing commas, single quotes, comments.
    # YAML's flow syntax reads those; the result is still validated in full.
    for candidate in candidates:
        body = candidate.strip()
        if body.startswith("{") and body.endswith("}"):
            try:
                return validate(yaml.safe_load(body))
            except (ValueError, TypeError, yaml.YAMLError):
                continue
    if best_error:
        raise ValueError(f"Reply does not satisfy {schema.__name__}: " + best_error)
    cut_off = text.count("{") > text.count("}")
    hint = "the JSON object is incomplete (cut off before its closing brace)" if cut_off else ("invalid JSON, " + syntax if syntax else "no JSON object found")
    raise ValueError(f"Reply does not satisfy {schema.__name__}: {hint}")
