"""Parse ordinary chat replies without requiring provider JSON-mode support.

This is a syntax boundary, not a semantic repair engine. Invalid contracts are
returned to a bounded model correction step; evidence IDs are never invented.
"""

import json
import re

import yaml


def visible_text(content) -> str:
    """Exclude provider reasoning blocks; keep user-visible answer text only."""
    if isinstance(content, list):
        content = "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") in {"text", "output_text"})
    text = content if isinstance(content, str) else ""
    return re.sub(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", "", text, flags=re.S | re.I).strip()


def parse_contract(content, schema):
    """Accept fenced/embedded JSON and explicitly fenced YAML, then validate."""
    text = visible_text(content)
    candidates = [text]
    for language, body in re.findall(r"```(\w*)\s*\n(.*?)```", text, re.S):
        if language.lower() in {"yaml", "yml"}:
            try:
                return schema.model_validate(yaml.safe_load(body))
            except (ValueError, TypeError, yaml.YAMLError):
                continue
        candidates.append(body.strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        # raw_decode handles braces inside JSON strings correctly, unlike a
        # greedy regex. Bound attempts to avoid quadratic scans of long prose.
        starts = [m.start() for m in re.finditer(r"\{", candidate)][:100]
        for start in starts:
            try:
                value, _ = decoder.raw_decode(candidate[start:])
                return schema.model_validate(value)
            except (ValueError, TypeError):
                continue
    raise ValueError(f"Reply does not satisfy {schema.__name__}")
