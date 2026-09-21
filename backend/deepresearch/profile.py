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
import re

from pydantic import ValidationError

from .config import McpServerSpec, Settings
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
    "max_searches_per_unit",
    "max_seconds_per_unit",
    "max_findings_per_unit",
    "report_time_reserve_seconds",
    "plan_min_units",
    "plan_max_units",
    "supplement_gap_codes",
    "writer_concurrency",
    "nodes",
    "output_retries",
    "allow_limited_report",
    "cite_search_results",
    "max_synthesis_repairs",
    "max_report_sections",
    "report_length_scale",
    "trace_capture_content",
    "tool_audit",
    "wire_audit",
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
    "audit_max_chars",
    "audit_retention_days",
    "tool_timeout_seconds",
    "tool_retries",
)
# Fields added after run fingerprints existed. Removing them at their defaults
# keeps the fingerprint of an unchanged operator file identical across upgrades.
LATER_FIELDS = {
    "tool_audit",
    "wire_audit",
    "audit_max_chars",
    "audit_retention_days",
    "prompts",
    "models",
    "default_model",
    "rewrite_model",
    "llm_audit",
    "mcp_servers",
    "engine_tools",
    "compaction",
    "max_searches_per_unit",
    "max_seconds_per_unit",
    "max_findings_per_unit",
    "report_time_reserve_seconds",
    "plan_min_units",
    "plan_max_units",
    "supplement_gap_codes",
    "writer_concurrency",
    "report_length_scale",
    "nodes",
    "tool_receipt_ledger",
}
LATER_SKILL_DEFAULTS = {"methodology": None, "name": "", "tools": None, "enabled": True}
LATER_SOURCE_DEFAULTS = {"description": "", "providers": [], "mcp_tool": None, "enabled": True}


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


SENSITIVE_NAME = re.compile(r"authorization|cookie|token|secret|password|passwd|api[_-]?key|(^|[_-])key($|[_-])|credential|session", re.I)
HIDDEN = "[hidden]"


def _is_reference(value) -> bool:
    return isinstance(value, str) and ("$" in value or "secret:" in value)


def _credential_fields(body: dict):
    """(path, mapping, key) of every header, environment or URL value that can carry a credential."""
    for name, server in (body.get("mcp_servers") or {}).items():
        if isinstance(server, dict):
            for field in ("headers", "env"):
                for key in server.get(field) or {}:
                    yield f"mcp_servers.{name}.{field}.{key}", server[field], key
    for source in body.get("sources") or []:
        for provider in (source.get("providers") or []) if isinstance(source, dict) else []:
            for key in provider.get("headers") or {}:
                yield f"sources.{source.get('name')}.providers.{provider.get('id')}.headers.{key}", provider["headers"], key


def guard(operator: Settings, overrides: dict | None) -> None:
    """What the settings page may never introduce, whoever is signed in.

    The operator's file is trusted: it can point a role at any Skill file,
    start a local MCP process or name any model class. The settings API is a
    web form. It must not read files of the host (a role ``path`` is read and
    then shown to every signed-in user), start processes (a stdio MCP server
    runs its command as the Gateway, and the host screens such commands on its
    own MCP page), import classes, or store a pasted credential where the
    settings view shows it again.
    """
    overrides = overrides or {}
    base = operator.model_dump(mode="json")
    problems = []
    for name, spec in (overrides.get("skills") or {}).items():
        allowed = (base["skills"].get(name) or {}).get("path")
        if isinstance(spec, dict) and spec.get("path") and spec["path"] != allowed:
            problems.append(f"skills.{name}.path: roles edited on the settings page carry their methodology as text; a Skill file path belongs in the operator configuration")
    for name, server in (overrides.get("mcp_servers") or {}).items():
        if isinstance(server, dict) and server.get("transport") == "stdio" and McpServerSpec.model_validate(server).model_dump(mode="json") != base["mcp_servers"].get(name):
            problems.append(f"mcp_servers.{name}: a stdio MCP server starts a process on the Gateway host and can only be defined in the operator configuration; use an http or sse server here")
    known_classes = {model.get("use") for model in base["models"]}
    for model in overrides.get("models") or []:
        if isinstance(model, dict) and model.get("provider") == "custom" and model.get("use") not in known_classes:
            problems.append(f"models.{model.get('name')}.use: a custom model class can only be introduced in the operator configuration")
    operator_values = {path: mapping.get(key) for path, mapping, key in _credential_fields(base)}
    for path, mapping, key in _credential_fields(overrides):
        value = mapping.get(key)
        if SENSITIVE_NAME.search(key) and value and not _is_reference(value) and operator_values.get(path) != value:
            problems.append(f"{path}: reference the credential ($ENV_NAME, secret:NAME, or inside a value: Bearer ${{ENV_NAME}}, sid=${{secret:NAME}}) instead of saving its value")
    if problems:
        raise ValueError("; ".join(problems[:10]))


def masked(body: dict) -> dict:
    """The settings view for a user who cannot edit: no literal credential values.

    References stay readable (they name a variable, not its value). A literal
    in the operator's own file is that operator's choice, but it is not shown
    to every signed-in user.
    """
    body = copy.deepcopy(body)
    for _, mapping, key in _credential_fields(body):
        if SENSITIVE_NAME.search(key) and mapping.get(key) and not _is_reference(mapping[key]):
            mapping[key] = HIDDEN
    for source in body.get("sources") or []:
        for provider in source.get("providers") or []:
            if provider.get("url") and "?" in provider["url"] and not _is_reference(provider["url"].split("?", 1)[1]):
                provider["url"] = provider["url"].split("?", 1)[0] + "?" + HIDDEN
    return body


def effective(operator: Settings, overrides: dict | None) -> Settings:
    """Operator settings with the stored overrides applied and validated."""
    guard(operator, overrides)
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
