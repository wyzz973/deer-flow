"""Fault-injection regressions for the real failure and workflow boundaries."""

import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from deepresearch.api import build_router
from deepresearch.contracts import CreateResearch, ResearchError
from deepresearch.native import model_budget_config, native_thread_id
from deepresearch.runner import DemoRunner
from deepresearch.service import ResearchService
from deepresearch.store import Store
from deepresearch.trace import provider_failure


async def settle(service, rid, states=("COMPLETED", "FAILED", "AWAITING_PLAN_CONFIRMATION")):
    async with asyncio.timeout(5):
        while True:
            run = await service.store.get(rid)
            if rid not in service.tasks and run["status"] in states:
                return run
            await asyncio.sleep(0.01)


async def completed(service, key="initial"):
    run = await service.create("local-demo", CreateResearch(query="Compare databases"), key)
    await settle(service, run["run_id"])
    await service.decision(run["run_id"], 1, "approve")
    result = await settle(service, run["run_id"], ("COMPLETED", "FAILED"))
    assert result["status"] == "COMPLETED", result.get("error")
    return result


@pytest.mark.parametrize("unit", ["AI-RELEASE-INDUSTRY-CONTEXT", "AI-AGENT-AND-TOOLING-RECENT-MONTH", "AI-CAPABILITY-EVIDENCE-RECENT-MONTH", "AI-MODEL-RELEASES-RECENT-MONTH", "x" * 80])
def test_native_ids_cover_the_live_failure_and_contract_maximum(unit):
    from deerflow.utils.thread_id import validate_thread_id

    run = {"thread_id": "dr-fa984b8d-91bd-4dd6-8609-6e88d4660a86"}
    with pytest.raises(ValueError):
        validate_thread_id(run["thread_id"] + "-" + unit)
    value = native_thread_id(run, "technical-route", unit)
    assert validate_thread_id(value) == value and len(value) <= 64
    assert value == native_thread_id(run, "technical-route", unit)
    assert value != native_thread_id({**run, "cycle": 1}, "technical-route", unit)
    assert value != native_thread_id(run, "deepresearch", unit)
    assert value != native_thread_id(run, "technical-route", unit[:-1] + "y")


def test_effective_subagent_policy_preserves_per_agent_caps_and_disabled_policy():
    from deerflow.config.app_config import AppConfig
    from deerflow.subagents.config import SubagentConfig

    config = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [{"name": "local", "use": "langchain_openai:ChatOpenAI", "model": "local"}],
            "subagents": {"agents": {"researcher": {"token_budget": {"enabled": True, "max_tokens": 10000, "max_input_tokens": 8000, "hard_stop_threshold": 0.8, "warn_threshold": 0.5}}}},
        }
    )
    role = SubagentConfig(name="researcher", description="research", system_prompt="research", model="local")
    run = {"budget": {"max_model_tokens": 120000}, "units": ["one", "two"], "usage": {"model_tokens": 100000}}
    bounded, _ = model_budget_config(config, role, 4096, run=run, researcher=True)
    policy = bounded.subagents.get_token_budget_for("researcher")
    assert policy.max_tokens == 10000 and policy.max_input_tokens == 8000
    assert policy.hard_stop_threshold == 0.8 and policy.warn_threshold == 0.25
    assert config.subagents.get_token_budget_for("researcher").warn_threshold == 0.5
    config.subagents.agents["researcher"].token_budget.enabled = False
    disabled, _ = model_budget_config(config, role, 4096, run=run, researcher=True)
    assert not disabled.subagents.get_token_budget_for("researcher").enabled


@pytest.mark.parametrize("status,code", [(401, "MODEL_AUTH_REQUIRED"), (402, "MODEL_BILLING_REQUIRED"), (403, "MODEL_ACCESS_DENIED"), (429, "MODEL_RATE_LIMIT"), (504, "MODEL_TIMEOUT"), (503, "MODEL_UNAVAILABLE")])
def test_provider_failures_are_actionable_without_echoing_credentials(status, code):
    error = RuntimeError("PRIVATE-PROVIDER-BODY-AND-CREDENTIAL")
    error.status_code = status
    safe = provider_failure(error)
    assert safe.code == code and "PRIVATE" not in str(safe)


@pytest.mark.asyncio
async def test_cancel_drains_inflight_database_work_before_returning(tmp_path):
    store = Store(tmp_path / "drain.sqlite")
    await store.start()
    entered, release = threading.Event(), threading.Event()

    def write(db):
        entered.set()
        release.wait(3)
        db.execute("CREATE TABLE finished (value INTEGER)")

    task = asyncio.create_task(store.call(write))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert await store.call(lambda db: db.execute("SELECT name FROM sqlite_master WHERE name='finished'").fetchone())


@pytest.mark.asyncio
async def test_report_publication_is_atomic_replay_safe_and_cancel_fenced(tmp_path):
    store = Store(tmp_path / "publish.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "status": "RENDERING", "conversation": []}, "key", "hash")
    with pytest.raises(KeyError):
        await store.publish_report("r", "broken", {"report": {}, "limitations": []})
    assert await store.call(lambda db: db.execute("SELECT COUNT(*) FROM research_report").fetchone()[0]) == 0
    body = {"report": {"title": "Verified report"}, "limitations": [], "markdown": "report"}
    first = await store.publish_report("r", "same-publication", body)
    again = await store.publish_report("r", "same-publication", body)
    assert first == again and first["version"] == 1
    run = await store.get("r")
    assert run["status"] == "COMPLETED" and len(run["conversation"]) == 1
    assert len(await store.events("r")) == 1
    await store.patch("r", status="CANCELLED", cancel_requested=True)
    with pytest.raises(ResearchError, match="取消"):
        await store.publish_report("r", "later", body)
    with pytest.raises(ResearchError, match="取消"):
        await store.reserve("r", tool_calls=1)


@pytest.mark.asyncio
async def test_create_replay_survives_capacity_and_rechecks_acl(settings, monkeypatch):
    import sys

    settings.max_active_runs = 1
    service = ResearchService(settings)
    await service.start()
    try:
        request = CreateResearch(query="Compare databases")
        run = await service.create("alice", request, "repeat")
        duplicate = await service.create("alice", request, "repeat")
        assert duplicate["run_id"] == run["run_id"]
        with pytest.raises(ResearchError, match="幂等"):
            await service.create("alice", CreateResearch(query="Different query"), "repeat")

        async def deny(_request, _run):
            return False

        settings.access_policy = "stability_acl:deny"
        monkeypatch.setitem(sys.modules, "stability_acl", SimpleNamespace(deny=deny))
        monkeypatch.setitem(sys.modules, "deerflow_extension_api", SimpleNamespace(resolve_principal=lambda _: SimpleNamespace(user_id="alice")))
        app = FastAPI()
        app.include_router(build_router(service))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/deepresearch", json=request.model_dump(), headers={"Idempotency-Key": "repeat"})
            assert response.status_code == 403 and "Compare databases" not in response.text
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("editing", [False, True])
async def test_accepted_input_survives_a_crash_before_task_submission(settings, monkeypatch, editing):
    service = ResearchService(settings)
    await service.start()
    if editing:
        run = await service.create("local-demo", CreateResearch(query="Compare databases"), "editing")
        run = await settle(service, run["run_id"])
    else:
        run = await completed(service)
    rid = run["run_id"]
    monkeypatch.setattr(service, "_launch", lambda *args: None)
    await service.message(rid, "Explain the scope", "durable-message", plan_version=1 if editing else None, secrets={"cookie": "TRANSIENT-SECRET"})
    accepted = await service.store.get(rid)
    assert accepted["pending_operation"]
    assert "TRANSIENT-SECRET" not in json.dumps(accepted)
    await service.stop()
    restarted = ResearchService(settings)
    await restarted.start()
    try:
        assert (await restarted.store.get(rid))["status"] == "FAILED"
        await restarted.retry(rid)
        final = await settle(restarted, rid)
        assert final["status"] == ("AWAITING_PLAN_CONFIRMATION" if editing else "COMPLETED"), final.get("error")
        assert not final.get("pending_operation")
        assert len([m for m in final["conversation"] if m["id"] == "durable-message"]) == 1
        if editing:
            assert final["plan"]["plan_version"] == 2
        else:
            assert final["usage"]["tool_calls"] == 4
            assert final["conversation"][-1]["id"] == "answer-durable-message"
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_followup_replay_does_not_advance_cycle_twice_or_hide_events(settings, monkeypatch):
    service = ResearchService(settings)

    class Counting(DemoRunner):
        responses = 0

        async def respond(self, *args):
            self.responses += 1
            return await super().respond(*args)

    runner = Counting(settings, service.store)
    service.runner = runner
    await service.start()
    try:
        run = await completed(service)
        rid = run["run_id"]
        mutate = service.store.mutate
        fail_once = True

        async def crash_after_transition(run_id, fn):
            nonlocal fail_once
            value = await mutate(run_id, fn)
            if fn.__name__ == "begin_cycle" and fail_once:
                fail_once = False
                raise OSError("Simulated crash after durable cycle transition")
            return value

        monkeypatch.setattr(service.store, "mutate", crash_after_transition)
        await service.message(rid, "补充研究 a new scenario", "new-cycle")
        failed = await settle(service, rid)
        assert failed["status"] == "FAILED" and failed["cycle"] == 1
        await service.retry(rid)
        pending = await settle(service, rid)
        assert pending["status"] == "AWAITING_PLAN_CONFIRMATION", pending.get("error")
        assert pending["cycle"] == 1 and runner.responses == 1
        assert pending["report"] is None
        await service.decision(rid, pending["plan"]["plan_version"], "approve")
        final = await settle(service, rid)
        assert final["status"] == "COMPLETED" and final["usage"]["tool_calls"] == 8
        done = [e for e in await service.store.events(rid, limit=1000) if e["type"] == "research.unit.completed"]
        assert len(done) == 4 and {e["data"]["cycle"] for e in done} == {0, 1}
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_invalid_plan_does_not_cancel_its_countdown(settings):
    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="Compare databases"), "bad-edit")
        run = await settle(service, run["run_id"])
        timer = service.countdowns[run["run_id"]]
        with pytest.raises(ResearchError, match="计划不符合"):
            await service.decision(run["run_id"], 1, "edit", {"goal": "bad"})
        assert service.countdowns[run["run_id"]] is timer and not timer.cancelled()
        with pytest.raises(ResearchError, match="幂等"):
            await service.message(run["run_id"], run["query"], "initial", plan_version=1)
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_bookkeeping_failure_cannot_leak_admission_or_credentials(settings, monkeypatch):
    service = ResearchService(settings)
    await service.start()
    context = {"secrets": {"cookie": "private"}}
    monkeypatch.setattr(service, "context", lambda *args: context)
    mutate = service.store.mutate

    async def fail_elapsed(rid, fn):
        if "elapsed_seconds" in fn.__code__.co_consts:
            raise OSError("bookkeeping unavailable")
        return await mutate(rid, fn)

    monkeypatch.setattr(service.store, "mutate", fail_elapsed)
    try:
        run = await service.create("u", CreateResearch(query="Compare databases"), "bookkeeping")
        task = service.tasks[run["run_id"]]
        outcomes = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcomes[0], OSError)
        assert run["run_id"] not in service.tasks and context["secrets"] == {}
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_cancel_drains_units_and_shutdown_rejects_new_work(settings):
    service = ResearchService(settings)
    entered = asyncio.Event()
    drained = []

    class Slow(DemoRunner):
        async def research(self, run, unit, dependencies):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.append(unit.id)

    service.runner = Slow(settings, service.store)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="Compare databases"), "cancel")
        rid = run["run_id"]
        await settle(service, rid)
        await service.decision(rid, 1, "approve")
        await asyncio.wait_for(entered.wait(), 2)
        cancelled = await service.cancel(rid)
        assert cancelled["status"] == "CANCELLED" and rid not in service.tasks
        assert drained and set(cancelled["unit_statuses"].values()) == {"CANCELLED"}
        assert not cancelled.get("pending_operation")
    finally:
        await service.stop()
    with pytest.raises(ResearchError, match="正在停止"):
        await service.create("u", CreateResearch(query="Another research"), "after-stop")


@pytest.mark.asyncio
async def test_shutdown_fences_an_admission_already_waiting_on_authorization(settings):
    service = ResearchService(settings)
    await service.start()
    entered, release = asyncio.Event(), asyncio.Event()

    async def authorize(_):
        entered.set()
        await release.wait()

    creator = asyncio.create_task(service.create("u", CreateResearch(query="Compare databases"), "shutdown-race", authorize=authorize))
    await asyncio.wait_for(entered.wait(), 2)
    stopping = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(ResearchError, match="正在停止"):
        await creator
    await stopping
    assert not service.tasks and service.graph is None
    restarted = ResearchService(settings)
    await restarted.start()
    try:
        runs = await restarted.store.list()
        assert len(runs) == 1 and runs[0]["status"] == "FAILED"
        assert runs[0]["error"]["code"] == "PROCESS_INTERRUPTED"
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_cached_unit_recovery_repairs_status_and_missing_completion_event(settings, monkeypatch):
    service = ResearchService(settings)
    await service.start()
    save = service.store.save_unit
    failed = False

    async def crash_after_save(*args):
        nonlocal failed
        await save(*args)
        if not failed:
            failed = True
            raise OSError("Interrupted between result and status publication")

    monkeypatch.setattr(service.store, "save_unit", crash_after_save)
    try:
        run = await service.create("u", CreateResearch(query="Compare databases"), "unit-publication")
        rid = run["run_id"]
        await settle(service, rid)
        await service.decision(rid, 1, "approve")
        first = await settle(service, rid)
        assert first["status"] == "FAILED" and first["usage"]["tool_calls"] == 4
        await service.retry(rid)
        final = await settle(service, rid)
        assert final["status"] == "COMPLETED" and final["usage"]["tool_calls"] == 4
        assert set(final["unit_statuses"].values()) == {"COMPLETED"}
        assert sum(e["type"] == "research.unit.completed" for e in await service.store.events(rid)) == 2
    finally:
        await service.stop()
