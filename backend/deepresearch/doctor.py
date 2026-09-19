"""Validate research configuration without contacting models or sources.

Explicit options make the only outbound calls: ``--probe-model NAME`` sends two
short requests to that model (a plain reply and a tool call) and
``--probe-sources`` sends one small request to every enabled provider.
``--probe-mcp`` connects to every enabled MCP server and lists its tools.
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sqlite3
import time

from .config import load_settings
from .secrets import SECRETS

PROBE_TIMEOUT_SECONDS = 180
SMALL_CONTEXT = 32768


async def probe_model(name, timeout=PROBE_TIMEOUT_SECONDS, settings=None):
    """A plain reply and a tool call through the engine's model factory."""
    from langchain_core.tools import tool

    from deerflow.config import get_app_config
    from deerflow.models import create_chat_model

    from .models import engine_config
    from .trace import redact, usage_details

    try:
        app_config = engine_config(get_app_config(), settings) if settings is not None else get_app_config()
    except Exception as exc:
        return {"model": name, "ok": False, "error": redact(f"{type(exc).__name__}: {exc}", max_chars=500)}
    profile = app_config.get_model_config(name)
    if profile is None:
        return {"model": name, "ok": False, "error": f"Model {name!r} is not configured in the research models"}
    extras = profile.model_extra or {}
    report = {"model": name, "use": profile.use, "max_tokens": extras.get("max_completion_tokens", extras.get("max_tokens")), "context_window": getattr(profile, "context_window", None) or extras.get("context_window")}

    @tool
    def lookup(query: str) -> str:
        """Search the research knowledge base."""
        return "no results"

    async def timed(runnable, prompt):
        from .models import complete

        started = time.monotonic()
        # Probe the way research calls the model, so a streaming-only server
        # passes here exactly when it would work in a real run.
        reply = await asyncio.wait_for(complete(runnable, prompt), timeout)
        return reply, round(time.monotonic() - started, 1)

    try:
        # Research roles run with thinking disabled, so probe the same way.
        model = create_chat_model(name=name, thinking_enabled=False, app_config=app_config, attach_tracing=False)
        plain, plain_seconds = await timed(model, "Reply with the single word: ready")
        called, tool_seconds = await timed(model.bind_tools([lookup]), "Call the lookup tool to search for: DeerFlow deep research")
    except Exception as exc:
        secrets = [value for value in (extras.get("api_key"),) if isinstance(value, str)]
        return {**report, "ok": False, "error": redact(f"{type(exc).__name__}: {exc}", secrets, max_chars=500)}
    usage = usage_details(called)
    tools = [call["name"] for call in called.tool_calls]
    warnings = []
    # Plans, outlines and findings are JSON requested in plain prompt text (no
    # JSON mode), so check that the model returns a parseable object that way.
    contract = {"ok": False, "seconds": None}
    try:
        from pydantic import BaseModel

        from .output import parse_contract

        class Probe(BaseModel):
            status: str
            items: list[int]

        answer, contract["seconds"] = await timed(model, 'Return only a JSON object with the fields "status" (the string "ready") and "items" (the list [1, 2, 3]). No explanation.')
        parsed = parse_contract(str(answer.content), Probe)
        contract["ok"] = parsed.status == "ready" and parsed.items == [1, 2, 3]
        speed = usage_details(answer)
        if speed["output_tokens"] and contract["seconds"]:
            contract["output_tokens_per_second"] = round(speed["output_tokens"] / contract["seconds"], 1)
    except Exception as exc:
        contract["error"] = type(exc).__name__

    if not tools:
        warnings.append("No tool call: researchers cannot search or read without tool calling; enable it on the model server.")
    if usage["total_tokens"] is None:
        warnings.append("No usage reported: token and cost metrics stay empty for this model. Check that the gateway supports stream_options.include_usage (models.<name>.stream_usage).")
    if not contract["ok"]:
        warnings.append("The model did not return the requested JSON object: lower nodes.plan/outline/conversion temperature to 0, raise output_retries, or choose another model for those nodes.")
    window = report["context_window"]
    if window is None:
        warnings.append("context_window is not set: context display and fraction-based summarization are unavailable.")
    elif window < SMALL_CONTEXT:
        warnings.append(f"context_window {window} is below {SMALL_CONTEXT}: lower summarization triggers and tool output limits.")
    return {
        **report,
        "ok": bool(tools),
        "plain_reply": {"seconds": plain_seconds, "text": str(plain.content)[:120]},
        "tool_call": {"seconds": tool_seconds, "tools": tools},
        "json_contract": contract,
        "usage": usage,
        "warnings": warnings,
    }


def load_saved_secrets(settings):
    """Secrets saved on the settings page live in the research database."""
    path = settings.resolve(settings.data_dir) / "research.sqlite3"
    if not path.exists():
        return
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            SECRETS.load(dict(db.execute("SELECT name, value FROM research_secret").fetchall()))
    except sqlite3.Error:
        pass


def retrieval_status(settings):
    """What researchers can search, open and cite with the enabled sources."""
    from .report_policy import results_citable

    active = settings.active_sources()
    by_role = {role: [source.name for source in active if source.role == role] for role in ("search", "read", "data")}
    citable = results_citable(settings)
    notes = []
    if not by_role["read"]:
        notes.append("No enabled source opens a full page: search results and records are cited as evidence, and researchers use the research_records prompt.")
    if not by_role["read"] and not by_role["data"] and by_role["search"] and not settings.cite_search_results:
        notes.append("Reports will cite excerpts only; declare a knowledge tool as role: data when it returns complete records.")
    opaque = [source.name for source in active if source.kind == "mcp"]
    if opaque:
        notes.append(
            "kind: mcp sources ("
            + ", ".join(opaque)
            + ") hand the MCP tool to the researcher unchanged and are cited as one whole output per call. "
            + "For a citation per record with its own title and link, configure a provider-based source instead: providers: [{type: mcp, server: ..., tool: ...}]."
        )
    return {**by_role, "search_results_citable": citable, "notes": notes}


def node_status(settings):
    """The model and parameters each workflow node really runs with."""
    from .config import NODE_NAMES
    from .models import model_for, node_output_cap

    roles = {"rewrite": "deepresearch", "plan": "deepresearch", "follow_up": "deepresearch", "outline": "report-synthesis", "section": "report-synthesis", "summary": "report-synthesis", "revision": "report-synthesis"}
    status = {}
    for name in NODE_NAMES:
        tuning = settings.node(name)
        if name == "research":
            model = {role: model_for(settings, spec, None, "research") for role, spec in settings.researchers().items()}
        elif name == "conversion":
            model = tuning.model or settings.extraction_model or "the researcher's model"
        elif name == "rewrite":
            model = tuning.model or settings.rewrite_model or model_for(settings, settings.skills["deepresearch"])
        else:
            model = model_for(settings, settings.skills[roles[name]], None, name)
        status[name] = {
            "enabled": tuning.enabled,
            "model": model,
            "temperature": tuning.temperature,
            "top_p": tuning.top_p,
            "max_tokens": node_output_cap(settings, name),
            "timeout_seconds": tuning.timeout_seconds,
            "output_retries": tuning.output_retries if tuning.output_retries is not None else settings.output_retries,
            "json_mode": tuning.json_mode,
        }
    return status


def configuration_status(settings):
    """Everything checkable offline, including missing credentials."""
    for name in settings.skills:
        settings.read_skill(name)
    status = {
        "mode": settings.runner,
        "roles": {name: {"enabled": spec.enabled, "model": spec.model, "agent": spec.agent} for name, spec in settings.skills.items()},
        "models": [{"name": model.name, "provider": model.provider, "model": model.model, "api_key": SECRETS.status(model.api_key)} for model in settings.models],
        "default_model": settings.default_model,
        "sources": [
            {
                "name": source.name,
                "tool": source.tool,
                "kind": source.kind,
                "role": source.role,
                "enabled": source.enabled,
                "providers": [{"id": item.id, "type": item.type, "enabled": item.enabled, "api_key": SECRETS.status(item.api_key)} for item in source.providers],
            }
            for source in settings.sources
        ],
        "mcp_servers": {
            name: {"transport": server.transport, "enabled": server.enabled, "allowed_tools": server.allowed_tools, "used_tools": sorted({tool for _, bound, tool in settings.mcp_bindings() if bound == name})}
            for name, server in settings.mcp_servers.items()
        },
        "retrieval": retrieval_status(settings),
        "nodes": node_status(settings),
        "langgraph_installed": importlib.util.find_spec("langgraph") is not None,
        "local_credentials_present": {key: bool(os.getenv(env)) for key, env in settings.local_secret_env.items()},
    }
    problems = []
    names = {model.name for model in settings.models}
    references = [("default_model", settings.default_model), ("rewrite_model", settings.rewrite_model), ("extraction_model", settings.extraction_model)]
    references += [(f"roles.{name}.model", spec.model) for name, spec in settings.skills.items()]
    for label, reference in references:
        if reference and names and reference not in names:
            problems.append(f"{label} refers to {reference!r}, which is not in models")
    for model in status["models"]:
        if model["api_key"] == "missing":
            problems.append(f"model {model['name']} has no API key value")
    for source in status["sources"]:
        for provider in source["providers"]:
            if provider["enabled"] and provider["api_key"] == "missing":
                problems.append(f"provider {source['name']}/{provider['id']} has no API key value")
    status["problems"] = problems
    return status


async def probe_mcp(settings):
    """Connect to every enabled MCP server and compare its tools with the configuration."""
    from .mcp import MANAGER, connection_failure

    results = {}
    for name, server in settings.mcp_servers.items():
        if not server.enabled:
            results[name] = {"ok": None, "skipped": "disabled"}
            continue
        wanted = sorted({tool for _, bound, tool in settings.mcp_bindings() if bound == name})
        started = time.monotonic()
        try:
            tools = await MANAGER.tools(name, server, refresh=True)
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            failure = connection_failure(name, TimeoutError() if isinstance(exc, TimeoutError) else exc)
            results[name] = {"ok": False, "kind": failure.kind, "error": str(failure), "seconds": round(time.monotonic() - started, 1)}
            continue
        offered = sorted(tool.name for tool in tools)
        missing = [tool for tool in wanted if tool not in offered]
        results[name] = {
            "ok": not missing,
            "seconds": round(time.monotonic() - started, 1),
            "offered_tools": offered,
            "used_tools": wanted,
            "missing_tools": missing,
            "not_allowed": [tool for tool in offered if server.allowed_tools is not None and tool not in server.allowed_tools],
        }
    return results


async def probe_sources(settings):
    from .channels import test_provider

    results = {}
    for source in settings.sources:
        for provider in source.providers:
            if provider.enabled:
                results[f"{source.name}/{provider.id}"] = await test_provider(source, provider, settings)
    return results


def engine_capacity(settings, app_config):
    """Whether the engine admits as many native roles at once as research runs.

    The engine queues roles beyond ``subagent_runtime.max_running`` and fails one
    that waits longer than its admission timeout. Research cannot raise the
    limit (it is read once, for the whole process), so a smaller value silently
    caps ``max_concurrency`` and ``writer_concurrency``.
    """
    needed = max(settings.max_concurrency, settings.writer_concurrency or settings.max_concurrency)
    runtime = getattr(app_config, "subagent_runtime", None)
    limit = getattr(runtime, "max_running", None)
    if not isinstance(limit, int) or limit >= needed:
        return {"needed": needed, "max_running": limit, "ok": True}
    timeout = getattr(runtime, "queue_timeout_seconds", None)
    warning = (
        f"The engine runs {limit} native roles at once but research is configured for {needed}: the rest queue inside the engine (a wait no research metric shows) and fail after {timeout} s. "
        f"Set subagent_runtime.max_running to at least {needed} in the engine configuration and restart, or lower max_concurrency / writer_concurrency to {limit}."
    )
    return {"needed": needed, "max_running": limit, "ok": False, "warning": warning}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--probe-model", metavar="NAME", help="Also call this research model twice to check a reply, tool calling and usage")
    parser.add_argument("--probe-sources", action="store_true", help="Also send one small request to every enabled source provider")
    parser.add_argument("--probe-mcp", action="store_true", help="Also connect to every enabled MCP server, list its tools and check the tools research uses")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    load_saved_secrets(settings)
    status = configuration_status(settings)
    legacy = {spec.agent for spec in settings.skills.values() if spec.agent}
    if settings.runner == "deerflow" and legacy:
        from deerflow.config import get_app_config
        from deerflow.subagents.registry import get_subagent_config

        # Roles bound to host subagents come from older files. Pass the loaded
        # host config: the registry's global subagent config stays empty otherwise.
        app_config = get_app_config()
        status["legacy_agents"] = {name: get_subagent_config(name, app_config=app_config) is not None for name in sorted(legacy)}
        if not all(status["legacy_agents"].values()):
            print(json.dumps(status, ensure_ascii=False, indent=2))
            raise SystemExit("One or more roles are bound to host subagents that are not configured")
    if settings.runner == "deerflow":
        try:
            from deerflow.config import get_app_config

            status["engine_capacity"] = engine_capacity(settings, get_app_config())
        except Exception as error:  # noqa: BLE001 - the host configuration may be absent where doctor runs
            status["engine_capacity"] = {"ok": None, "error": type(error).__name__}
    if args.probe_model:
        status["model_probe"] = asyncio.run(probe_model(args.probe_model, settings=settings))
    if args.probe_sources:
        status["source_probe"] = asyncio.run(probe_sources(settings))
    if args.probe_mcp:
        status["mcp_probe"] = asyncio.run(probe_mcp(settings))
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if args.probe_mcp and any(item.get("ok") is False for item in status["mcp_probe"].values()):
        raise SystemExit("MCP probe failed: a server is unreachable, rejected its credentials, or lacks a tool research uses")
    if args.probe_model and not status["model_probe"]["ok"]:
        raise SystemExit("Model probe failed: DeepResearch needs a reachable model with tool calling")
    if not status["langgraph_installed"]:
        raise SystemExit("Install DeepResearch requirements (or uv sync the host workspace)")
    if status["problems"]:
        raise SystemExit("Configuration problems: " + "; ".join(status["problems"]))


if __name__ == "__main__":
    main()
