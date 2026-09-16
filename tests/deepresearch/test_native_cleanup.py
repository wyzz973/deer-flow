"""Native work must not outlive an interrupted research bridge submission."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from deepresearch.contracts import ResearchError
from deepresearch.native import execute_role
from deepresearch.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["audit-write", "repeated-cancel", "provider", "timeout"])
async def test_submitted_worker_is_drained_and_provider_bodies_stay_private(settings, tmp_path, monkeypatch, failure):
    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.config import SubagentConfig

    config = AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "models": [{"name": "local", "use": "langchain_openai:ChatOpenAI", "model": "local"}]})
    role = SubagentConfig(name="researcher", description="test", system_prompt="test", model="local")
    result = SimpleNamespace(status=SimpleNamespace(is_terminal=failure in {"provider", "timeout"}, value="timed_out" if failure == "timeout" else "failed"), stop_reason=None, error="SECRET_NATIVE_PROVIDER_BODY")
    captured = {"cancel": False, "cleanup": False, "callbacks_closed": False}
    submitted, cancelling = asyncio.Event(), asyncio.Event()

    class Executor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt):
            submitted.set()
            return "execution"

    def cancel(_):
        captured["cancel"] = True
        cancelling.set()
        if failure == "repeated-cancel":
            asyncio.get_running_loop().call_later(0.05, lambda: setattr(result.status, "is_terminal", True))
        else:
            result.status.is_terminal = True

    def cleanup(_):
        assert result.status.is_terminal
        captured["cleanup"] = True

    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    monkeypatch.setitem(
        sys.modules,
        "deerflow.subagents.executor",
        SimpleNamespace(SubagentExecutor=Executor, SubagentStatus=SimpleNamespace(COMPLETED="completed"), get_background_task_result=lambda _: result, request_cancel_background_task=cancel, cleanup_background_task=cleanup),
    )
    provider_error = ResearchError("MODEL_BILLING_REQUIRED", "Check model billing") if failure == "provider" else None

    async def close_callbacks():
        assert captured["cleanup"]
        captured["callbacks_closed"] = True

    monkeypatch.setattr("deepresearch.native.model_callbacks", lambda *args, **kwargs: SimpleNamespace(budget_error=None, provider_error=provider_error, close=close_callbacks))
    store = Store(tmp_path / "native.sqlite")
    await store.start()
    run = {"run_id": "r", "thread_id": "dr-r", "owner": "u"}
    await store.create(run, "key", "hash")
    event = store.event

    async def interruption(rid, kind, *args, **kwargs):
        if kind == "research.agent.started":
            if failure == "audit-write":
                raise OSError("audit unavailable")
            if failure == "repeated-cancel":
                await asyncio.Event().wait()
        return await event(rid, kind, *args, **kwargs)

    monkeypatch.setattr(store, "event", interruption)
    task = asyncio.create_task(execute_role(settings, store, run, "technical-route", {"unit": {"id": "unit"}}, [], role, {}))
    if failure == "repeated-cancel":
        await asyncio.wait_for(submitted.wait(), 2)
        task.cancel()
        await asyncio.wait_for(cancelling.wait(), 2)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
    expected = ResearchError if failure in {"provider", "timeout"} else asyncio.CancelledError if failure == "repeated-cancel" else OSError
    with pytest.raises(expected):
        await task
    assert captured["cleanup"]
    assert captured["callbacks_closed"]
    assert captured["cancel"] == (failure not in {"provider", "timeout"})
    assert "SECRET_NATIVE_PROVIDER_BODY" not in str(await store.events("r"))
