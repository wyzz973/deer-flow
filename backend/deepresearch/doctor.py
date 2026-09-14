"""Validate configuration without contacting a model or MCP service."""
import argparse
import importlib.util
import json
import os

from .config import load_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    args = parser.parse_args()
    settings = load_settings(args.config)
    for name in settings.skills:
        settings.read_skill(name)
    status = {"mode": settings.runner, "skills": list(settings.skills), "sources": [s.name for s in settings.sources],
              "langgraph_installed": importlib.util.find_spec("langgraph") is not None,
              "local_credentials_present": {key: bool(os.getenv(env)) for key, env in settings.local_secret_env.items()}}
    if settings.runner == "deerflow":
        from deerflow.subagents.registry import get_subagent_config
        status["agents"] = {s.agent: get_subagent_config(s.agent) is not None for s in settings.skills.values()}
        if not all(status["agents"].values()):
            print(json.dumps(status, ensure_ascii=False, indent=2))
            raise SystemExit("One or more Custom Agents are not configured")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not status["langgraph_installed"]:
        raise SystemExit("Install DeepResearch requirements (or uv sync the host workspace)")


if __name__ == "__main__":
    main()
