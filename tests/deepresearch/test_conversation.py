"""Research conversation lifecycle: edits pause server-owned auto-start."""

import asyncio

import pytest

from deepresearch.contracts import CreateResearch, ResearchError
from deepresearch.service import ResearchService


async def wait_status(service, run_id, expected, timeout=3):
    async with asyncio.timeout(timeout):
        while True:
            run = await service.store.get(run_id)
            # Phase persistence precedes the driver's final bookkeeping. The
            # production countdown retries RUN_BUSY during that short window;
            # direct service tests must also wait until a decision is admissible.
            if run["status"] in expected and run_id not in service.tasks:
                return run
            await asyncio.sleep(0.01)


async def settled(service, run_id):
    """Wait for terminal persistence and the execution's final cleanup."""
    run = await wait_status(service, run_id, {"COMPLETED", "FAILED"})
    task = service.tasks.get(run_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 3)
    assert run["status"] == "COMPLETED", run.get("error")
    return await service.store.get(run_id)


@pytest.mark.asyncio
async def test_server_countdown_starts_without_browser(settings):
    settings.plan_countdown_seconds = 0.08
    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="比较两种数据库"), "countdown")
        plan = await wait_status(service, run["run_id"], {"AWAITING_PLAN_CONFIRMATION"})
        assert plan["auto_start_at"] is not None
        completed = await wait_status(service, run["run_id"], {"COMPLETED", "FAILED"})
        assert completed["status"] == "COMPLETED", completed.get("error")
        assert completed["usage"]["tool_calls"] == 4
        events = await service.store.events(run["run_id"])
        assert sum(e["type"] == "plan.auto_started" for e in events) == 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_edit_pauses_countdown_and_revises_same_run(settings):
    settings.plan_countdown_seconds = 0.15
    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="比较两种数据库"), "edit")
        await wait_status(service, run["run_id"], {"AWAITING_PLAN_CONFIRMATION"})
        paused = await service.pause_plan(run["run_id"], 1)
        assert paused["status"] == "EDITING_PLAN" and paused["auto_start_at"] is None
        await asyncio.sleep(0.2)
        assert (await service.store.get(run["run_id"]))["usage"]["tool_calls"] == 0
        await service.message(run["run_id"], "只关注运维成本", "message-1", plan_version=1)
        revised = await wait_status(service, run["run_id"], {"AWAITING_PLAN_CONFIRMATION"})
        assert revised["run_id"] == run["run_id"]
        assert revised["plan"]["plan_version"] == 2
        assert "只关注运维成本" in revised["plan"]["constraints"]
        assert len([m for m in revised["conversation"] if m["kind"] == "plan"]) == 2
        duplicate = await service.message(run["run_id"], "只关注运维成本", "message-1", plan_version=1)
        assert duplicate["plan"]["plan_version"] == 2
        with pytest.raises(ResearchError, match="幂等"):
            await service.message(run["run_id"], "a different instruction", "message-1", plan_version=2)
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_restart_pauses_pending_plan_instead_of_reusing_old_credentials(settings):
    service = ResearchService(settings)
    await service.start()
    run = await service.create("u", CreateResearch(query="比较两种数据库"), "restart", {"cookie": "private-secret"})
    await wait_status(service, run["run_id"], {"AWAITING_PLAN_CONFIRMATION"})
    await service.stop()
    restarted = ResearchService(settings)
    await restarted.start()
    try:
        current = await restarted.store.get(run["run_id"])
        assert current["auto_start_at"] is None
        assert current["auto_start_paused"] is True
        assert "private-secret" not in str(current)
    finally:
        await restarted.stop()


@pytest.mark.asyncio
async def test_followups_preserve_reports_and_do_not_repeat_search_for_rewrites(settings):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from deepresearch.api import build_router

    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("local-demo", CreateResearch(query="Compare databases"), "followups")
        run_id = run["run_id"]
        await wait_status(service, run_id, {"AWAITING_PLAN_CONFIRMATION"})
        await service.decision(run_id, 1, "approve")
        first = await settled(service, run_id)
        original_report = first["report"]
        await service.message(run_id, "Explain the report", "answer-1")
        answer = await settled(service, run_id)
        assert answer["usage"]["tool_calls"] == 4
        assert answer["report"] == original_report
        assert answer["conversation"][-1]["kind"] == "text"
        await service.message(run_id, "改写报告，保留现有证据", "rewrite-1")
        revised = await settled(service, run_id)
        assert revised["usage"]["tool_calls"] == 4
        assert revised["report"]["version"] == original_report["version"] + 1
        reports = [m["report"] for m in revised["conversation"] if m["kind"] == "report"]
        assert reports[0] == original_report
        assert len(reports) == 2

        app = FastAPI()
        app.include_router(build_router(service, local_demo=True))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            old = await client.get(f"/api/deepresearch/{run_id}/report?version=1")
            assert old.status_code == 200 and old.json() == original_report
            missing = await client.get(f"/api/deepresearch/{run_id}/report?version=999")
            assert missing.status_code == 404
            exported = await client.get(f"/api/deepresearch/{run_id}/trace/export")
            assert exported.status_code == 200 and "application/x-ndjson" in exported.headers["content-type"]
            assert "trace.started" in exported.text

        await service.message(run_id, "补充研究新的部署场景", "research-1")
        new_plan = await wait_status(service, run_id, {"AWAITING_PLAN_CONFIRMATION", "FAILED"})
        assert new_plan["status"] == "AWAITING_PLAN_CONFIRMATION", new_plan.get("error")
        assert new_plan["usage"]["tool_calls"] == 4
        assert new_plan["cycle"] == 1
        assert new_plan["plan"]["plan_version"] > 1
        await service.decision(run_id, new_plan["plan"]["plan_version"], "approve")
        new_report = await settled(service, run_id)
        assert new_report["usage"]["tool_calls"] == 8
        assert new_report["report"]["version"] == 3
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_duplicate_approval_and_stale_edits_cannot_restart_execution(settings):
    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="Compare databases"), "two-tabs")
        run_id = run["run_id"]
        plan = await wait_status(service, run_id, {"AWAITING_PLAN_CONFIRMATION"})
        with pytest.raises(ResearchError):
            await service.pause_plan(run_id, 999)
        assert (await service.store.get(run_id))["auto_start_at"] == plan["auto_start_at"]
        decisions = await asyncio.gather(service.decision(run_id, 1, "approve"), service.decision(run_id, 1, "approve"), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in decisions) == 1
        completed = await settled(service, run_id)
        assert completed["usage"]["tool_calls"] == 4
    finally:
        await service.stop()
