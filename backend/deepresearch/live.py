"""Opt-in, loopback-only acceptance launcher for the real DeerFlow Gateway.

This is not an alternate agent runtime. It creates a private configuration
copy and launches app.gateway.app with the normal extension lifecycle. The
operator's config, credentials, databases, and skill directory stay unchanged.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_SENSITIVE = re.compile(r"key|token|secret|password|authorization|cookie", re.I)


def private_config(value, environ, prefix="DEERFLOW_ACCEPTANCE_SECRET"):
    """Replace inline secrets with process-only environment references."""
    counter = 0

    def visit(item):
        nonlocal counter
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return copy.deepcopy(item)
        result = {}
        for key, child in item.items():
            if _SENSITIVE.search(str(key)) and isinstance(child, str) and child and not child.startswith("$"):
                counter += 1
                name = f"{prefix}_{counter}"
                environ[name] = child
                result[key] = "$" + name
            else:
                result[key] = visit(child)
        return result

    return visit(value)


def build_config(base, fragment, home: Path, model: str, *, max_output_tokens=None, unlimited_budget=False):
    """Keep the configured providers and tools; isolate application state."""
    if model not in {item["name"] for item in base.get("models", [])}:
        raise ValueError("Choose an existing model name from the host configuration")
    value = copy.deepcopy(base)
    if max_output_tokens is not None:
        for profile in value["models"]:
            if profile["name"] != model:
                continue
            key = "max_completion_tokens" if "max_completion_tokens" in profile else "max_tokens"
            profile[key] = max_output_tokens
            for variant in ("when_thinking_enabled", "when_thinking_disabled"):
                if isinstance(profile.get(variant), dict):
                    profile[variant][key] = max_output_tokens
    value["plugins"] = [{"name": "deepresearch", "use": "deepresearch.extension:install", "required": True, "config": {"config_path": str(home / "research.yaml")}}]
    value["database"] = {**value.get("database", {}), "backend": "sqlite", "sqlite_dir": str(home / "database"), "postgres_url": ""}
    value["checkpointer"] = None
    value["skills"] = {**value.get("skills", {}), "path": str(home / "skills")}
    value["scheduler"] = {**value.get("scheduler", {}), "enabled": False}
    value["memory"] = {**value.get("memory", {}), "enabled": False}
    value["subagents"] = copy.deepcopy(value.get("subagents") or {})
    custom = value["subagents"].setdefault("custom_agents", {})
    for name, settings in fragment["subagents"]["custom_agents"].items():
        settings = copy.deepcopy(settings)
        settings["model"] = model
        if name in {"industry-researcher", "technical-researcher"}:
            settings["tools"] = None
        custom["acceptance-" + name] = settings
        if unlimited_budget:
            # Native subagents resolve this policy separately from the lead
            # agent's top-level token_budget. Disable only acceptance roles.
            override = value["subagents"].setdefault("agents", {}).setdefault("acceptance-" + name, {})
            override["token_budget"] = {"enabled": False}
    return value


def _write_yaml(path, data, *, reuse=False):
    if reuse:
        # A code-only restart may reuse checkpoints, but a changed execution
        # configuration must not silently acquire the old run's identity.
        if yaml.safe_load(path.read_text(encoding="utf-8")) != data:
            raise ValueError(f"Acceptance configuration changed; cannot resume {path.name}")
        return
    # Generated configuration may still contain private non-credential metadata.
    with path.open("x", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        yaml.safe_dump(data, output, allow_unicode=True, sort_keys=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true", help="Explicitly allow the UI to call configured providers")
    parser.add_argument("--model", required=True, help="Existing host model name, not a provider invented by this launcher")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--frontend-port", type=int, default=3100)
    parser.add_argument("--jina-no-key", action="store_true", help="Use the native Jina client's public mode in this process only")
    parser.add_argument("--max-output-tokens", type=int, help="Per-response limit verified against the configured provider")
    parser.add_argument("--unlimited-budget", action="store_true", help="Disable cumulative tokens, tool-call and elapsed budgets for this isolated acceptance; usage is still recorded")
    parser.add_argument("--resume-dir", type=Path, help="Reuse an existing private live directory after a code-only repair; configuration must match")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live acceptance requires --allow-live")
    if args.max_output_tokens is not None and not 128 <= args.max_output_tokens <= 393216:
        parser.error("--max-output-tokens must be between 128 and 393216")

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    for name in list(os.environ):
        if name.startswith(("LANGSMITH_", "LANGFUSE_")):
            os.environ[name] = ""
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGFUSE_ENABLED"):
        os.environ[name] = "false"
    if args.jina_no_key:
        os.environ["JINA_API_KEY"] = ""

    parent = ROOT / ".deerflow" / "deepresearch"
    parent.mkdir(parents=True, exist_ok=True)
    home = args.resume_dir.resolve() if args.resume_dir else Path(tempfile.mkdtemp(prefix="live-", dir=parent))
    if args.resume_dir and (home.parent != parent.resolve() or not home.name.startswith("live-") or not home.is_dir()):
        parser.error("--resume-dir must be an existing private live directory under .deerflow/deepresearch")
    base = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    fragment = yaml.safe_load((ROOT / "examples/deepresearch/host-config.fragment.yaml").read_text(encoding="utf-8"))
    host = private_config(build_config(base, fragment, home, args.model, max_output_tokens=args.max_output_tokens, unlimited_budget=args.unlimited_budget), os.environ)
    _write_yaml(home / "host.yaml", host, reuse=bool(args.resume_dir))

    research = yaml.safe_load((ROOT / "deepresearch.example.yaml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((ROOT / "examples/deepresearch/native-web.sources.yaml").read_text(encoding="utf-8"))
    research.update(sources)
    research.update(runner="deerflow", data_dir=str(home / "research"), native_tools=None, extraction_model=args.model)
    if args.max_output_tokens is not None:
        research["max_output_tokens"] = args.max_output_tokens
    if args.unlimited_budget:
        # Null is a real disabled ceiling, not an arbitrarily large allowance.
        # Structural unit/iteration limits and native cancellation remain.
        research.setdefault("budget_ceiling", {}).update(max_model_tokens=None, max_tool_calls=None, max_elapsed_seconds=None)
    (home / "skills" / "public").mkdir(parents=True, exist_ok=bool(args.resume_dir))
    for name, spec in research["skills"].items():
        spec["agent"] = "acceptance-" + spec["agent"]
        spec["model"] = args.model
        target = home / "skills" / "custom" / name
        if not args.resume_dir:
            target.mkdir(parents=True)
            shutil.copyfile(ROOT / spec["path"], target / "SKILL.md")
    _write_yaml(home / "research.yaml", research, reuse=bool(args.resume_dir))

    os.environ.update(
        DEER_FLOW_CONFIG_PATH=str(home / "host.yaml"),
        DEER_FLOW_HOME=str(home / "host"),
        DEER_FLOW_ENV="development",
        DEER_FLOW_AUTH_DISABLED="1",
        GATEWAY_CORS_ORIGINS=f"http://127.0.0.1:{args.frontend_port},http://localhost:{args.frontend_port}",
    )
    print(f"Live acceptance data: {home}", flush=True)
    print("Loopback development authentication only; this does not validate enterprise SSO.", flush=True)
    import uvicorn

    uvicorn.run("app.gateway.app:app", host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
