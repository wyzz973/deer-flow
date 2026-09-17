"""Configuration doctor: an explicit probe tells whether a host model can research."""

import json

import pytest
from langchain_core.messages import AIMessage

from deerflow.config.model_config import ModelConfig


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


def install(monkeypatch, model, **profile):
    config = type("Config", (), {"get_model_config": lambda self, name: ModelConfig(name=name, use="langchain_openai:ChatOpenAI", model="qwen", **profile) if name == "local" else None})()
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    created = []

    def create_chat_model(**kwargs):
        created.append(kwargs)
        return model

    monkeypatch.setattr("deerflow.models.create_chat_model", create_chat_model)
    return created


def test_doctor_finds_every_custom_agent_of_a_real_research_config(monkeypatch, capsys):
    from pathlib import Path

    import yaml

    from deepresearch.doctor import main
    from deerflow.config.app_config import AppConfig

    root = Path(__file__).resolve().parents[2]
    base = yaml.safe_load((root / "config.example.yaml").read_text(encoding="utf-8"))
    fragment = yaml.safe_load((root / "examples/deepresearch/offline/host-config.fragment.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate({**base, **fragment})
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    main(["--config", str(root / "examples/deepresearch/offline/research.yaml")])
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "deerflow" and all(status["agents"].values()) and len(status["agents"]) == 4


@pytest.mark.asyncio
async def test_probe_reports_tool_calling_usage_and_a_small_context(monkeypatch):
    from deepresearch.doctor import probe_model

    created = install(monkeypatch, FakeModel(), max_tokens=8192, context_window=16000)
    report = await probe_model("local")
    assert created == [{"name": "local", "thinking_enabled": False, "attach_tracing": False}]
    assert report["ok"] and report["tool_call"]["tools"] == ["lookup"] and report["plain_reply"]["text"] == "ready"
    assert (report["max_tokens"], report["context_window"], report["usage"]["total_tokens"]) == (8192, 16000, 46)
    assert any("below 32768" in warning for warning in report["warnings"])


def test_probe_failures_exit_nonzero_with_a_readable_reason(monkeypatch, capsys):
    from deepresearch.doctor import main

    install(monkeypatch, FakeModel(tool_calls=False), context_window=65536)
    with pytest.raises(SystemExit, match="tool calling"):
        main(["--probe-model", "local"])
    probe = json.loads(capsys.readouterr().out)["model_probe"]
    assert probe["ok"] is False and probe["warnings"][0].startswith("No tool call")

    install(monkeypatch, FakeModel(error=ConnectionError("connect to http://127.0.0.1:8000/v1 refused; Bearer sk-secret")))
    with pytest.raises(SystemExit):
        main(["--probe-model", "local"])
    probe = json.loads(capsys.readouterr().out)["model_probe"]
    assert probe["ok"] is False and probe["error"].startswith("ConnectionError") and "sk-secret" not in probe["error"]

    with pytest.raises(SystemExit, match="not in the models list"):
        main(["--probe-model", "missing"])
