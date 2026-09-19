"""Configuration doctor: offline checks and an explicit research model probe."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

ROOT = Path(__file__).resolve().parents[2]


class FakeModel:
    def __init__(self, *, tool_calls=True, error=None):
        self.tool_calls, self.error, self.tools = tool_calls, error, None

    def bind_tools(self, tools):
        bound = FakeModel(tool_calls=self.tool_calls, error=self.error)
        bound.tools = tools
        return bound

    async def ainvoke(self, prompt):
        if self.error:
            raise self.error
        usage = {"input_tokens": 40, "output_tokens": 6, "total_tokens": 46}
        if self.tools is None:
            return AIMessage(content="ready", usage_metadata=usage)
        calls = [{"id": "c1", "name": self.tools[0].name, "args": {"query": "DeerFlow"}}] if self.tool_calls else []
        return AIMessage(content="", tool_calls=calls, usage_metadata=usage)


def install(monkeypatch, model):
    from deerflow.config.app_config import AppConfig

    host = AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "models": []})
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: host)
    created = []

    def create_chat_model(**kwargs):
        created.append(kwargs)
        return model

    monkeypatch.setattr("deerflow.models.create_chat_model", create_chat_model)
    return created


def offline_settings(monkeypatch, context_window=65536):
    from deepresearch.config import load_settings

    monkeypatch.setenv("LOCAL_MODEL_API_KEY", "local-not-checked")
    settings = load_settings(ROOT / "examples/deepresearch/offline/research.yaml")
    settings.models[0].context_window = context_window
    return settings


def test_status_needs_no_host_agents_and_reports_missing_credentials(monkeypatch, capsys, tmp_path):
    from deepresearch.doctor import main

    monkeypatch.setenv("LOCAL_MODEL_API_KEY", "local-not-checked")
    monkeypatch.delenv("RAGFLOW_API_KEY", raising=False)
    config = tmp_path / "research.yaml"
    config.write_text((ROOT / "examples/deepresearch/offline/research.yaml").read_text(encoding="utf-8").replace(".deerflow/deepresearch", str(tmp_path / "data")), encoding="utf-8")

    def no_host(*args, **kwargs):
        raise AssertionError("Self-contained roles never look up host subagents")

    monkeypatch.setattr("deerflow.subagents.registry.get_subagent_config", no_host)
    with pytest.raises(SystemExit, match="RAGFLOW_API_KEY|internal-knowledge/ragflow"):
        main(["--config", str(config)])
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "deerflow" and "legacy_agents" not in status
    assert status["models"] == [{"name": "local-model", "provider": "vllm", "model": "Qwen/Qwen3-32B", "api_key": "set"}]
    assert status["problems"] == ["provider internal-knowledge/ragflow has no API key value"]
    monkeypatch.setenv("RAGFLOW_API_KEY", "ragflow-test")
    main(["--config", str(config)])
    assert json.loads(capsys.readouterr().out)["problems"] == []


@pytest.mark.asyncio
async def test_probe_uses_the_research_model_and_reports_a_small_context(monkeypatch):
    from deepresearch.doctor import probe_model

    created = install(monkeypatch, FakeModel())
    report = await probe_model("local-model", settings=offline_settings(monkeypatch, context_window=16000))
    assert [(item["name"], item["thinking_enabled"], item["attach_tracing"]) for item in created] == [("local-model", False, False)]
    assert created[0]["app_config"].get_model_config("local-model").model == "Qwen/Qwen3-32B"
    assert report["ok"] and report["tool_call"]["tools"] == ["lookup"] and report["plain_reply"]["text"] == "ready"
    assert (report["max_tokens"], report["context_window"], report["usage"]["total_tokens"]) == (8192, 16000, 46)
    assert any("below 32768" in warning for warning in report["warnings"])


@pytest.mark.asyncio
async def test_probe_failures_are_readable_and_never_echo_credentials(monkeypatch):
    from deepresearch.doctor import probe_model

    install(monkeypatch, FakeModel(tool_calls=False))
    probe = await probe_model("local-model", settings=offline_settings(monkeypatch))
    assert probe["ok"] is False and probe["warnings"][0].startswith("No tool call")

    install(monkeypatch, FakeModel(error=ConnectionError("connect to http://127.0.0.1:8000/v1 refused; Bearer sk-secret; key local-not-checked")))
    probe = await probe_model("local-model", settings=offline_settings(monkeypatch))
    assert probe["ok"] is False and probe["error"].startswith("ConnectionError")
    assert "sk-secret" not in probe["error"] and "local-not-checked" not in probe["error"]

    missing = await probe_model("missing", settings=offline_settings(monkeypatch))
    assert missing["ok"] is False and "not configured" in missing["error"]


def test_doctor_names_an_engine_that_admits_fewer_roles_than_research_runs(settings):
    from types import SimpleNamespace

    from deepresearch.doctor import engine_capacity

    settings.max_concurrency, settings.writer_concurrency = 6, 8
    host = SimpleNamespace(subagent_runtime=SimpleNamespace(max_running=3, queue_timeout_seconds=300))
    capacity = engine_capacity(settings, host)
    assert (capacity["needed"], capacity["max_running"], capacity["ok"]) == (8, 3, False)
    assert "subagent_runtime.max_running" in capacity["warning"] and "8" in capacity["warning"]
    host.subagent_runtime.max_running = 8
    assert engine_capacity(settings, host) == {"needed": 8, "max_running": 8, "ok": True}
    # A host without the section imposes no limit research could know about.
    assert engine_capacity(settings, SimpleNamespace())["ok"] is True
