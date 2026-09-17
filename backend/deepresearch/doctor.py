"""Validate configuration without contacting a model or MCP service.

``--probe-model NAME`` is the one explicit exception: it sends two short requests
to that host model to check a plain reply, tool calling and usage reporting.
"""

import argparse
import asyncio
import importlib.util
import json
import os
import time

from .config import load_settings

PROBE_TIMEOUT_SECONDS = 180
SMALL_CONTEXT = 32768


async def probe_model(name, timeout=PROBE_TIMEOUT_SECONDS):
    """A plain reply and a tool call through DeerFlow's own model factory."""
    from langchain_core.tools import tool

    from deerflow.config import get_app_config
    from deerflow.models import create_chat_model

    from .trace import redact, usage_details

    profile = get_app_config().get_model_config(name)
    if profile is None:
        raise SystemExit(f"Model {name!r} is not in the models list of the host config.yaml")
    extras = profile.model_extra or {}
    report = {"model": name, "use": profile.use, "max_tokens": extras.get("max_completion_tokens", extras.get("max_tokens")), "context_window": profile.context_window}

    @tool
    def lookup(query: str) -> str:
        """Search the research knowledge base."""
        return "no results"

    async def timed(runnable, prompt):
        started = time.monotonic()
        reply = await asyncio.wait_for(runnable.ainvoke(prompt), timeout)
        return reply, round(time.monotonic() - started, 1)

    try:
        # Research roles run with thinking disabled, so probe the same way.
        model = create_chat_model(name=name, thinking_enabled=False, attach_tracing=False)
        plain, plain_seconds = await timed(model, "Reply with the single word: ready")
        called, tool_seconds = await timed(model.bind_tools([lookup]), "Call the lookup tool to search for: DeerFlow deep research")
    except Exception as exc:
        return {**report, "ok": False, "error": redact(f"{type(exc).__name__}: {exc}", max_chars=500)}
    usage = usage_details(called)
    tools = [call["name"] for call in called.tool_calls]
    warnings = []
    if not tools:
        warnings.append("No tool call: researchers cannot search or read without tool calling; enable it on the model server.")
    if usage["total_tokens"] is None:
        warnings.append("No usage reported: token and cost metrics stay empty for this model.")
    if profile.context_window is None:
        warnings.append("context_window is not set: context display and fraction-based summarization are unavailable.")
    elif profile.context_window < SMALL_CONTEXT:
        warnings.append(f"context_window {profile.context_window} is below {SMALL_CONTEXT}: lower summarization triggers and tool output limits.")
    return {
        **report,
        "ok": bool(tools),
        "plain_reply": {"seconds": plain_seconds, "text": str(plain.content)[:120]},
        "tool_call": {"seconds": tool_seconds, "tools": tools},
        "usage": usage,
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument("--probe-model", metavar="NAME", help="Also call this host model twice to check a reply, tool calling and usage")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    for name in settings.skills:
        settings.read_skill(name)
    status = {
        "mode": settings.runner,
        "skills": list(settings.skills),
        "sources": [s.name for s in settings.sources],
        "langgraph_installed": importlib.util.find_spec("langgraph") is not None,
        "local_credentials_present": {key: bool(os.getenv(env)) for key, env in settings.local_secret_env.items()},
    }
    if settings.runner == "deerflow":
        from deerflow.config import get_app_config
        from deerflow.subagents.registry import get_subagent_config

        # Pass the loaded host config: the registry's global subagent config is
        # still empty until something loads it, which hid the first agent.
        app_config = get_app_config()
        status["agents"] = {s.agent: get_subagent_config(s.agent, app_config=app_config) is not None for s in settings.skills.values()}
        if not all(status["agents"].values()):
            print(json.dumps(status, ensure_ascii=False, indent=2))
            raise SystemExit("One or more Custom Agents are not configured")
    if args.probe_model:
        status["model_probe"] = asyncio.run(probe_model(args.probe_model))
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if args.probe_model and not status["model_probe"]["ok"]:
        raise SystemExit("Model probe failed: DeepResearch needs a reachable model with tool calling")
    if not status["langgraph_installed"]:
        raise SystemExit("Install DeepResearch requirements (or uv sync the host workspace)")


if __name__ == "__main__":
    main()
