"""Research profile: user-editable settings layered over the operator's YAML.

The YAML file stays the operator's baseline and the ceiling for everything that
affects security or capacity. The settings page stores overrides for roles,
methodologies, prompts, models, sources, search channels and research behavior.
A run keeps an immutable snapshot of the effective settings it was created
with, so later edits never change research that already exists.
"""

from __future__ import annotations

import copy
import json
import logging

from pydantic import ValidationError

from .config import Settings
from .evidence import digest

logger = logging.getLogger("deepresearch.audit")

# Fields the settings page may change. Everything else (runner, data directory,
# capacity, budget ceilings, credential plumbing, access policy, the native tool
# ceiling) remains operator configuration.
EDITABLE = (
    "skills",
    "prompts",
    "compaction",
    "models",
    "default_model",
    "rewrite_model",
    "extraction_model",
    "mcp_servers",
    "engine_tools",
    "sources",
    "source_fallback",
    "require_dual_source",
    "max_concurrency",
    "plan_countdown_seconds",
    "max_output_tokens",
    "output_retries",
    "allow_limited_report",
    "cite_search_results",
    "max_synthesis_repairs",
    "max_report_sections",
    "trace_capture_content",
    "llm_audit",
    "pricing",
)
# Operator-owned and process-wide; a run snapshot never carries them.
OPERATOR_ONLY = (
    "runner",
    "runner_factory",
    "data_dir",
    "max_active_runs",
    "favicons",
    "pricing",
    "budget_ceiling",
    "local_secret_env",
    "request_secret_headers",
    "access_policy",
    "native_tools",
    "source_priority_file",
    "trace_max_chars",
    "tool_timeout_seconds",
    "tool_retries",
)
# Fields added after run fingerprints existed. Removing them at their defaults
# keeps the fingerprint of an unchanged operator file identical across upgrades.
LATER_FIELDS = {"prompts", "models", "default_model", "rewrite_model", "llm_audit", "mcp_servers", "engine_tools", "compaction"}
LATER_SKILL_DEFAULTS = {"methodology": None, "name": "", "tools": None, "enabled": True}
LATER_SOURCE_DEFAULTS = {"description": "", "providers": [], "mcp_tool": None}


def fingerprint(settings: Settings, bodies: dict[str, str]) -> str:
    """Identity of operator configuration for runs created before snapshots."""
    data = settings.model_dump(mode="json", exclude={"pricing", *LATER_FIELDS})
    for spec in data["skills"].values():
        for key, default in LATER_SKILL_DEFAULTS.items():
            if key in spec and spec[key] == default:
                spec.pop(key)
    for source in data["sources"]:
        for key, default in LATER_SOURCE_DEFAULTS.items():
            if key in source and source[key] == default:
                source.pop(key)
    return digest([data, bodies])


def _drop_unknown(body: dict, error: ValidationError) -> list[str]:
    """Remove the exact fields pydantic reported as unknown; return their paths."""
    removed: list[str] = []
    for item in error.errors():
        if item.get("type") != "extra_forbidden":
            continue
        path = list(item.get("loc", ()))
        target: object = body
        for key in path[:-1]:
            if isinstance(target, dict):
                target = target.get(key)
            elif isinstance(target, list) and isinstance(key, int) and key < len(target):
                target = target[key]
            else:
                target = None
                break
        if path and isinstance(target, dict) and path[-1] in target:
            target.pop(path[-1])
            removed.append(".".join(str(part) for part in path))
    return removed


def validated(body: dict) -> Settings:
    """Validate settings that may come from an earlier version of this schema.

    Saved overrides and run snapshots outlive the code that wrote them. A field
    a later version renamed or dropped must not strand an existing run, so the
    unknown fields pydantic names are removed and validation runs once more.
    Every other error still fails loudly.
    """
    try:
        return Settings.model_validate(body)
    except ValidationError as error:
        candidate = copy.deepcopy(body)
        removed = _drop_unknown(candidate, error)
        if not removed:
            raise
        logger.warning('{"event": "research_settings_unknown_fields", "fields": %s}', json.dumps(sorted(removed)))
        return Settings.model_validate(candidate)


def effective(operator: Settings, overrides: dict | None) -> Settings:
    """Operator settings with the stored overrides applied and validated."""
    base = operator.model_dump(mode="json")
    merged = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if key not in EDITABLE:
            continue
        if key == "prompts":
            merged["prompts"] = {**base["prompts"], **(value or {})}
        else:
            merged[key] = value
    settings = validated(merged)
    # Reuse Skill file bodies read at startup for roles that still point at the
    # same file; inline methodology never needs the cache.
    settings._skill_cache = {
        name: text for name, text in operator._skill_cache.items() if name in settings.skills and name in operator.skills and settings.skills[name].methodology is None and settings.skills[name].path == operator.skills[name].path
    }
    return settings


def snapshot(settings: Settings) -> dict:
    """Self-contained execution settings for one run: methodology text is inlined."""
    body = settings.model_dump(mode="json", exclude=set(OPERATOR_ONLY))
    for name, spec in body["skills"].items():
        # Inline text without Skill-file front matter, so file and inline forms compare equal.
        spec["methodology"] = settings.methodology(name)
        spec["path"] = None
    return body


def restore(operator: Settings, body: dict) -> Settings:
    """Settings for executing a run from its snapshot and current operator fields."""
    operator_fields = operator.model_dump(mode="json", include=set(OPERATOR_ONLY))
    return validated({**body, **operator_fields})


def editable_view(settings: Settings) -> dict:
    """Editable fields as the settings page shows them, methodology text included."""
    body = settings.model_dump(mode="json", include=set(EDITABLE))
    for name, spec in body["skills"].items():
        spec["methodology"] = settings.methodology(name)
        spec["path"] = None
    return body


def overrides_from(settings: Settings, operator: Settings) -> dict:
    """The editable fields that differ from the operator baseline."""
    current = editable_view(settings)
    base = editable_view(operator)
    result = {}
    for key, value in current.items():
        if key == "prompts":
            changed = {name: text for name, text in value.items() if base["prompts"].get(name) != text}
            if changed:
                result["prompts"] = changed
        elif value != base.get(key):
            result[key] = value
    return result
