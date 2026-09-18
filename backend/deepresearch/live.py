"""Opt-in, loopback-only acceptance launcher for the real DeerFlow Gateway.

This is not an alternate agent runtime. It creates private configuration copies
and launches app.gateway.app with the normal extension lifecycle. The engine copy
only isolates storage; everything research needs (its model, sources with
provider failover, roles) goes into a separate research configuration. The
operator's config, credentials, databases and skills stay unchanged.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_SENSITIVE = re.compile(r"key|token|secret|password|authorization|cookie", re.I)
# Host model fields that map onto a research model; the rest become constructor extras.
_MODEL_FIELDS = {"base_url", "max_tokens", "context_window", "temperature", "supports_thinking", "max_retries"}
_SKIPPED_MODEL_FIELDS = {"name", "display_name", "description", "use", "model", "api_key", "timeout", "request_timeout", "supports_vision"}
# Research models send the provider's thinking switch themselves.
_SKIPPED_MODEL_FIELDS |= {"when_thinking_enabled", "when_thinking_disabled", "supports_reasoning_effort", "use_responses_api", "output_version"}


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


def build_config(base, home: Path, roles=(), *, unlimited_budget=False):
    """The engine configuration with isolated application state; research lives elsewhere."""
    value = copy.deepcopy(base)
    value["plugins"] = [{"name": "deepresearch", "use": "deepresearch.extension:install", "required": True, "config": {"config_path": str(home / "research.yaml")}}]
    value["database"] = {**value.get("database", {}), "backend": "sqlite", "sqlite_dir": str(home / "database"), "postgres_url": ""}
    value["checkpointer"] = None
    value["skills"] = {**value.get("skills", {}), "path": str(home / "skills")}
    value["scheduler"] = {**value.get("scheduler", {}), "enabled": False}
    value["memory"] = {**value.get("memory", {}), "enabled": False}
    value["subagents"] = copy.deepcopy(value.get("subagents") or {})
    if unlimited_budget:
        # Native subagents resolve this policy separately from the lead agent's
        # top-level token_budget. Disable it only for the research roles.
        for role in roles:
            override = value["subagents"].setdefault("agents", {}).setdefault("deepresearch-" + role, {})
            override["token_budget"] = {"enabled": False}
    return value


def research_model(host_model, *, max_output_tokens=None):
    """Copy one host model definition into a research model at launch time.

    Research then runs from its own model entry; later host edits do not change it.
    """
    from .config import MODEL_PROVIDERS

    use = host_model["use"]
    provider = next((name for name, path in MODEL_PROVIDERS.items() if path == use), "custom")
    spec = {"name": host_model["name"], "display_name": host_model.get("display_name") or "", "provider": provider, "model": host_model["model"]}
    if provider == "custom":
        spec["use"] = use
    if host_model.get("api_key"):
        spec["api_key"] = host_model["api_key"]
    for field in _MODEL_FIELDS:
        if host_model.get(field) is not None:
            spec[field] = host_model[field]
    timeout = host_model.get("timeout") or host_model.get("request_timeout")
    if timeout:
        spec["timeout_seconds"] = timeout
    extra = {key: item for key, item in host_model.items() if key not in _MODEL_FIELDS | _SKIPPED_MODEL_FIELDS}
    if extra:
        spec["extra"] = extra
    if max_output_tokens is not None:
        spec["max_tokens"] = max_output_tokens
    return spec


def web_sources(environ, *, jina_key=True):
    """Public web sources with provider failover, using whichever keys are present."""
    search = [{"id": name, "type": name, "api_key": f"${variable}"} for name, variable in (("tavily", "TAVILY_API_KEY"), ("serper", "SERPER_API_KEY")) if environ.get(variable)]
    search.append({"id": "duckduckgo", "type": "duckduckgo"})
    reader = {"id": "jina", "type": "jina_reader"}
    if jina_key and environ.get("JINA_API_KEY"):
        reader["api_key"] = "$JINA_API_KEY"
    return [
        {"name": "public-web-search", "tool": "web_search", "role": "search", "origin": "external", "level": "L4", "publisher": "web-search-unclassified", "providers": search},
        {"name": "public-web-read", "tool": "web_fetch", "role": "read", "origin": "external", "level": "L4", "publisher": "web-content-unclassified", "providers": [reader, {"id": "direct", "type": "direct"}]},
    ]


def _write_yaml(path, data, *, reuse=False, operator_keys=()):
    if reuse:
        # A code-only restart may reuse checkpoints, but a changed execution
        # configuration must not silently acquire the old run's identity.
        # Operator keys (prices) do not change execution and may be added later.
        stored = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if {k: v for k, v in stored.items() if k not in operator_keys} != {k: v for k, v in data.items() if k not in operator_keys}:
            raise ValueError(f"Acceptance configuration changed; cannot resume {path.name}")
        return
    # Generated configuration may still contain private non-credential metadata.
    with path.open("x", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        yaml.safe_dump(data, output, allow_unicode=True, sort_keys=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true", help="Explicitly allow the UI to call configured providers")
    parser.add_argument("--model", required=True, help="A host model to copy into the research configuration as its model")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--frontend-port", type=int, default=3100)
    parser.add_argument("--jina-no-key", action="store_true", help="Use Jina Reader without JINA_API_KEY (lower rate limit)")
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

    parent = ROOT / ".deerflow" / "deepresearch"
    parent.mkdir(parents=True, exist_ok=True)
    home = args.resume_dir.resolve() if args.resume_dir else Path(tempfile.mkdtemp(prefix="live-", dir=parent))
    if args.resume_dir and (home.parent != parent.resolve() or not home.name.startswith("live-") or not home.is_dir()):
        parser.error("--resume-dir must be an existing private live directory under .deerflow/deepresearch")
    base = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    host_model = next((item for item in base.get("models", []) if item.get("name") == args.model), None)
    if host_model is None:
        parser.error("--model must name a model in config.yaml; it is copied into the research configuration")

    research = yaml.safe_load((ROOT / "deepresearch.example.yaml").read_text(encoding="utf-8"))
    model = private_config(research_model(host_model, max_output_tokens=args.max_output_tokens), os.environ, prefix="DEERFLOW_ACCEPTANCE_MODEL_SECRET")
    research.update(
        runner="deerflow",
        data_dir=str(home / "research"),
        models=[model],
        default_model=model["name"],
        extraction_model=model["name"],
        sources=web_sources(os.environ, jina_key=not args.jina_no_key),
        source_fallback=["public-web-search", "public-web-read"],
        require_dual_source=False,
    )
    if args.max_output_tokens is not None:
        research["max_output_tokens"] = args.max_output_tokens
    if args.unlimited_budget:
        # Null is a real disabled ceiling, not an arbitrarily large allowance.
        # Structural unit/iteration limits and native cancellation remain.
        research.setdefault("budget_ceiling", {}).update(max_model_tokens=None, max_tool_calls=None, max_elapsed_seconds=None)
    host = private_config(build_config(base, home, research["skills"], unlimited_budget=args.unlimited_budget), os.environ)
    _write_yaml(home / "host.yaml", host, reuse=bool(args.resume_dir))
    (home / "skills" / "public").mkdir(parents=True, exist_ok=True)
    _write_yaml(home / "research.yaml", research, reuse=bool(args.resume_dir), operator_keys=("pricing",))

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
