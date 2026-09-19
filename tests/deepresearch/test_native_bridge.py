"""Test the native adapter boundary without a model, sandbox, or MCP service."""

import sys
import types
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from deepresearch.native import execute_role, native_thread_id, output_instruction
from deepresearch.store import Store


@dataclass
class Agent:
    name: str = "technical-researcher"
    system_prompt: str = "Research carefully"
    model: str = "local-chat"
    max_turns: int = 20
    timeout_seconds: int = 180
    skills: list[str] | None = None


@pytest.mark.asyncio
async def test_roles_use_native_executor_with_scoped_tools_and_credentials(settings, tmp_path, monkeypatch):
    from deerflow.config.app_config import AppConfig

    config = AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "models": [{"name": "local-chat", "model": "local-chat", "use": "langchain_openai:ChatOpenAI", "max_tokens": 32768}]})
    # This test exercises the engine boundary with the engine's own model list.
    settings.models, settings.default_model = [], None
    captured = {}
    completed = types.SimpleNamespace(is_terminal=True)
    result = types.SimpleNamespace(status=completed, result="ordinary prose", stop_reason=None, snapshot_tool_receipts=lambda: [])

    class Executor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute_async(self, prompt):
            captured["prompt"] = prompt
            return "native-execution"

    def host_tools(**kwargs):
        raise AssertionError("Research roles do not inherit the host tool list")

    monkeypatch.setitem(sys.modules, "deerflow.config", types.SimpleNamespace(get_app_config=lambda: config))
    monkeypatch.setitem(sys.modules, "deerflow.tools", types.SimpleNamespace(get_available_tools=host_tools))
    monkeypatch.setattr("deepresearch.native.engine_tools", lambda names: [types.SimpleNamespace(name=name) for name in sorted(names)])
    monkeypatch.setitem(
        sys.modules,
        "deerflow.subagents.executor",
        types.SimpleNamespace(
            SubagentExecutor=Executor,
            SubagentStatus=types.SimpleNamespace(COMPLETED=completed),
            get_background_task_result=lambda _: result,
            request_cancel_background_task=lambda _: None,
            cleanup_background_task=lambda _: captured.update(cleaned=True),
        ),
    )
    monkeypatch.setattr("deepresearch.native.model_callbacks", lambda *a, **kw: types.SimpleNamespace(budget_error=None, close=AsyncMock()))
    store = Store(tmp_path / "native.sqlite")
    await store.start()
    run = {"run_id": "r", "thread_id": "dr-r", "owner": "alice"}
    await store.create(run, "k", "h")
    from deepresearch.trace import metric_scope

    source = types.SimpleNamespace(name="web_search")
    token = metric_scope.set({"phase": "dispatch", "cycle": 0})
    try:
        reply = await execute_role(settings, store, run, "technical-route", {"unit": {"id": "R1"}}, [source], Agent(), {"secrets": {"research_cookie": "private-value"}, "user_role": "member"})
    finally:
        metric_scope.reset(token)
    assert reply.answer == "ordinary prose"
    # Each native execution leaves one metrics record, even with stub callbacks.
    (agent_run,) = await store.agent_runs("r")
    assert agent_run == {**agent_run, "id": "native-execution", "phase": "dispatch", "skill": "technical-route", "unit_id": "R1", "purpose": "agent", "status": "completed", "agent_name": "technical-researcher"}
    assert agent_run["duration_ms"] >= 0 and agent_run["model"] == "local-chat"
    assert reply.execution_id == "native-execution"
    assert captured["thread_id"] == native_thread_id(run, "technical-route", "R1")
    assert captured["user_id"] == "alice"
    assert captured["request_secrets"] == {"research_cookie": "private-value"}
    # The run's source tools plus the engine tools research settings allow.
    assert [t.name for t in captured["tools"]] == ["read_file", "web_search"]
    assert captured["execution_callbacks"]
    assert captured["config"].max_turns == Agent().max_turns
    assert captured["app_config"].models[0].model_extra["max_tokens"] == settings.max_output_tokens
    assert config.models[0].model_extra["max_tokens"] == 32768
    # The engine's per-turn receipt ledger would rewrite the head of every
    # request; research roles run without it, and the host setting is untouched.
    assert captured["app_config"].verification.receipts_enabled is False and config.verification.receipts_enabled is True
    assert "private-value" not in captured["prompt"]
    assert captured["cleaned"]

    # Diagnostic payload limits cannot truncate the answer used by research.
    settings.trace_max_chars = 1000
    result.result = "Long research notes. " * 200
    result.ai_messages = [{"type": "ai", "content": "notes", "response_metadata": {"headers": {"Authorization": "private"}}}]
    long_reply = await execute_role(settings, store, run, "technical-route", {"unit": {"id": "R2"}}, [], Agent(), {})
    assert long_reply.answer == result.result.strip()
    assert "response_metadata" not in long_reply.messages[0]

    # Without engine stamping, receipts come from the archived tool messages in
    # the engine's r1..rN order, with the status the engine normalized.
    result.ai_messages = [
        {"type": "ai", "content": "", "tool_calls": [{"id": "c1", "name": "web_search", "args": {}}, {"id": "c2", "name": "web_fetch", "args": {}}]},
        {"type": "tool", "tool_call_id": "c1", "name": "web_search", "content": "ok"},
        {"type": "tool", "tool_call_id": "c2", "content": "Error: 429", "status": "success", "additional_kwargs": {"deerflow_tool_meta": {"status": "error"}}},
    ]
    derived = await execute_role(settings, store, run, "technical-route", {"unit": {"id": "R2b"}}, [], Agent(), {})
    assert derived.receipts == [
        {"id": "r1", "tool_call_id": "c1", "tool_name": "web_search", "status": "success"},
        {"id": "r2", "tool_call_id": "c2", "tool_name": "web_fetch", "status": "error"},
    ]
    # An operator who wants the ledger in the model's context can keep it.
    settings.tool_receipt_ledger = True
    await execute_role(settings, store, run, "technical-route", {"unit": {"id": "R2c"}}, [], Agent(), {})
    assert captured["app_config"].verification.receipts_enabled is True
    settings.tool_receipt_ledger = False

    settings.skills["technical-route"].max_turns = 5
    await execute_role(settings, store, run, "technical-route", {"unit": {"id": "R3"}}, [], Agent(), {})
    assert captured["config"].max_turns == 5

    # An agent bound through an older file keeps read_file for its native skills.
    settings.skills["deepresearch"].agent = "research-planner"
    await execute_role(settings, store, run, "deepresearch", {}, [], Agent(name="planner"), {})
    assert [tool.name for tool in captured["tools"]] == ["read_file"]
    # A self-contained planner or writer gets no tools: its methodology is in the prompt.
    settings.skills["report-synthesis"].agent = None
    await execute_role(settings, store, run, "report-synthesis", {}, [], Agent(name="writer", skills=[]), {})
    assert captured["tools"] == []
    assert "Skill files" not in captured["config"].system_prompt
    # A role allowlist narrows sources and can add engine tools.
    settings.skills["technical-route"].tools = ["grep"]
    await execute_role(settings, store, run, "technical-route", {"unit": {"id": "R4"}}, [source], Agent(skills=[]), {})
    assert [tool.name for tool in captured["tools"]] == ["grep"]


def test_progress_sentences_name_the_reader_language():
    researcher = output_instruction("technical-route", {"language": "Simplified Chinese (简体中文)"})
    assert "in Simplified Chinese (简体中文) saying what you will check next" in researcher
    assert "never mention skill files" in researcher
    assert "the language of the user's request" in output_instruction("technical-route", {})
    assert "Markdown" in output_instruction("report-synthesis", {"language": "English"})
    assert "output_schema" in output_instruction("technical-route", {"output_schema": {}, "language": "English"})
