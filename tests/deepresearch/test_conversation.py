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
async def test_edit_pauses_countdown_and_a_conversational_revision_starts_research(settings):
    settings.plan_countdown_seconds = 0.15
    service = ResearchService(settings)
    await service.start()
    demo_plan = service.runner.plan

    async def revised_units(run, proposed=None):
        # A revision may replace units; the run snapshot must follow the plan.
        plan = await demo_plan(run, proposed)
        if proposed and "revision" in proposed:
            plan.research_units = [unit.model_copy(update={"id": unit.id + "-v2"}) for unit in plan.research_units]
        return plan

    service.runner.plan = revised_units
    try:
        run = await service.create("u", CreateResearch(query="比较两种数据库"), "edit")
        await wait_status(service, run["run_id"], {"AWAITING_PLAN_CONFIRMATION"})
        paused = await service.pause_plan(run["run_id"], 1)
        assert paused["status"] == "EDITING_PLAN" and paused["auto_start_at"] is None
        await asyncio.sleep(0.2)
        assert (await service.store.get(run["run_id"]))["usage"]["tool_calls"] == 0
        await service.message(run["run_id"], "只关注运维成本", "message-1", plan_version=1)
        revised = await settled(service, run["run_id"])
        # Like ChatGPT: the revision is the approval, with no second countdown.
        assert revised["run_id"] == run["run_id"]
        assert revised["plan"]["plan_version"] == 2
        assert "只关注运维成本" in revised["plan"]["constraints"]
        planned = [unit["id"] for unit in revised["plan"]["research_units"]]
        assert all(unit_id.endswith("-v2") for unit_id in planned)
        assert [unit["id"] for unit in revised["units"]] == planned
        assert set(revised["unit_statuses"]) == set(planned)
        ids = [message["id"] for message in revised["conversation"]]
        assert ids.index("message-1") < ids.index("ack-2") < ids.index("plan-2")
        assert revised["conversation"][ids.index("ack-2")]["text"].endswith("只关注运维成本")
        assert len([m for m in revised["conversation"] if m["kind"] == "plan"]) == 2
        events = await service.store.events(run["run_id"], limit=1000)
        assert [e["data"].get("source") for e in events if e["type"] == "plan.auto_started"] == ["revision"]
        assert revised["usage"]["tool_calls"] == 4
        duplicate = await service.message(run["run_id"], "只关注运维成本", "message-1", plan_version=1)
        assert duplicate["plan"]["plan_version"] == 2
        with pytest.raises(ResearchError, match="幂等"):
            await service.message(run["run_id"], "a different instruction", "message-1", plan_version=2)
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_messages_during_research_become_updates_without_restarting(settings):
    from deepresearch.runner import DemoRunner

    settings.max_concurrency = 1
    service = ResearchService(settings)
    entered, release, seen = asyncio.Event(), asyncio.Event(), []

    class Slow(DemoRunner):
        async def research(self, run, unit, dependencies):
            current = await self.store.get(run["run_id"])
            seen.append((unit.id, [item["text"] for item in current.get("steering", [])]))
            if unit.id == "R1":
                entered.set()
                await release.wait()
            return await super().research(run, unit, dependencies)

    service.runner = Slow(settings, service.store)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="比较两种数据库"), "steer")
        rid = run["run_id"]
        await wait_status(service, rid, {"AWAITING_PLAN_CONFIRMATION"})
        await service.decision(rid, 1, "approve")
        await asyncio.wait_for(entered.wait(), 3)
        updated = await service.message(rid, "加上运维成本", "update-1")
        assert updated["status"] == "RESEARCHING" and rid in service.tasks
        assert updated["steering"][0]["text"] == "加上运维成本"
        assert [m["id"] for m in updated["conversation"][-2:]] == ["update-1", "update-update-1"]
        again = await service.message(rid, "加上运维成本", "update-1")
        assert len(again["steering"]) == 1
        release.set()
        completed = await settled(service, rid)
        assert seen == [("R1", []), ("R2", ["加上运维成本"])]
        assert completed["usage"]["tool_calls"] == 4
        events = await service.store.events(rid, limit=1000)
        assert sum(e["type"] == "conversation.steering" for e in events) == 1
    finally:
        release.set()
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
        # A follow-up is a new task with its own budget: nothing was searched
        # again, and what the research spent stays on record.
        assert answer["usage"]["tool_calls"] == 0
        assert answer["usage_history"][0]["tool_calls"] == 4
        assert answer["report"] == original_report
        assert answer["conversation"][-1]["kind"] == "text"
        await service.message(run_id, "改写报告，保留现有证据", "rewrite-1")
        revised = await settled(service, run_id)
        assert revised["usage"]["tool_calls"] == 0
        assert [item["tool_calls"] for item in revised["usage_history"]] == [4, 0]
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
            stream = await client.get(f"/api/deepresearch/{run_id}/events?after=999999")
            assert stream.status_code == 200 and "no-transform" in stream.headers["cache-control"]
            exported = await client.get(f"/api/deepresearch/{run_id}/trace/export")
            assert exported.status_code == 200 and "application/x-ndjson" in exported.headers["content-type"]
            assert "trace.started" in exported.text

        await service.message(run_id, "补充研究新的部署场景", "research-1")
        new_plan = await wait_status(service, run_id, {"AWAITING_PLAN_CONFIRMATION", "FAILED"})
        assert new_plan["status"] == "AWAITING_PLAN_CONFIRMATION", new_plan.get("error")
        assert new_plan["usage"]["tool_calls"] == 0
        assert new_plan["cycle"] == 1
        assert new_plan["plan"]["plan_version"] > 1
        await service.decision(run_id, new_plan["plan"]["plan_version"], "approve")
        new_report = await settled(service, run_id)
        # The new research cycle searched again, on a budget of its own.
        assert new_report["usage"]["tool_calls"] == 4
        assert [item["tool_calls"] for item in new_report["usage_history"]] == [4, 0, 0]
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


@pytest.mark.asyncio
async def test_a_stopped_follow_up_leaves_a_conversation_that_can_go_on(settings):
    """Stopping a follow-up used to end the conversation: every later message was RUN_BUSY."""
    from deepresearch.contracts import ResearchError

    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("local-demo", CreateResearch(query="Compare databases"), "stopped-follow-up")
        run_id = run["run_id"]
        await wait_status(service, run_id, {"AWAITING_PLAN_CONFIRMATION"})
        await service.decision(run_id, 1, "approve")
        report = (await settled(service, run_id))["report"]
        await service.message(run_id, "Explain the report", "answer-1")
        await service.cancel(run_id)
        stopped = await service.store.get(run_id)
        assert stopped["status"] == "CANCELLED" and stopped["report"] == report
        await service.message(run_id, "Explain it again", "answer-2")
        answered = await settled(service, run_id)
        assert answered["status"] == "COMPLETED" and not answered.get("cancel_requested")
        assert answered["conversation"][-1]["role"] == "assistant" and answered["report"] == report

        # A run stopped before any report has nothing to follow up on, and says so.
        other = await service.create("local-demo", CreateResearch(query="Compare caches"), "stopped-early")
        await wait_status(service, other["run_id"], {"AWAITING_PLAN_CONFIRMATION"})
        await service.cancel(other["run_id"])
        with pytest.raises(ResearchError) as refused:
            await service.message(other["run_id"], "continue", "after-stop")
        assert refused.value.code == "RUN_STOPPED"
    finally:
        await service.stop()
