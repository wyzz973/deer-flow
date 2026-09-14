import asyncio
from pathlib import Path

import pytest

pytest.importorskip("langgraph", reason="Install backend/deepresearch/requirements.txt for workflow integration tests")
pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from deepresearch.contracts import CreateResearch, ResearchError
from deepresearch.runner import DemoRunner
from deepresearch.service import ResearchService


async def settle(service, run_id):
    for _ in range(1000):
        task = service.tasks.get(run_id)
        if task is None:
            return await service.store.get(run_id)
        await asyncio.sleep(0.01)
    raise AssertionError("workflow did not settle")


@pytest.mark.asyncio
async def test_review_edit_approve_persistence_and_secret_exclusion(settings):
    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("u1", CreateResearch(query="比较技术路线与行业趋势"), "key", {"cookie": "SECRET-MARKER-NEVER-PERSIST"})
        run = await settle(service, run["run_id"])
        assert run["status"] == "AWAITING_PLAN_CONFIRMATION"
        assert run["usage"]["tool_calls"] == 0
        assert run["plan"]["plan_version"] == 1
        updated = dict(run["plan"], goal="用户编辑后的目标")
        await service.decision(run["run_id"], 1, "edit", updated)
        run = await settle(service, run["run_id"])
        assert run["status"] == "AWAITING_PLAN_CONFIRMATION" and run["plan"]["plan_version"] == 2
        with pytest.raises(ResearchError):
            await service.decision(run["run_id"], 1, "approve")
        await service.decision(run["run_id"], 2, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert run["usage"]["tool_calls"] == 4
        assert len(run["report"]["citations"]) == 4
        assert run["report"]["demo"]
        assert not await service.store.list("another-user")
        saved_id = run["run_id"]
    finally:
        await service.stop()
    for path in Path(settings.data_dir).glob("*.sqlite3*"):
        assert b"SECRET-MARKER-NEVER-PERSIST" not in path.read_bytes()
    again = ResearchService(settings)
    await again.start()
    try:
        assert (await again.store.get(saved_id))["status"] == "COMPLETED"
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_retry_reuses_successful_sibling(settings):
    service = ResearchService(settings)

    class Flaky(DemoRunner):
        calls = {}

        async def research(self, run, unit, dependencies):
            self.calls[unit.id] = self.calls.get(unit.id, 0) + 1
            if unit.id == "R2" and self.calls[unit.id] == 1:
                raise RuntimeError("SECRET-UPSTREAM-ERROR")
            return await super().research(run, unit, dependencies)

    runner = Flaky(settings, service.store)
    service.runner = runner
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="测试局部失败恢复"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "FAILED"
        assert "SECRET" not in str(await service.store.events(run["run_id"]))
        await service.retry(run["run_id"])
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert runner.calls == {"R1": 1, "R2": 2}
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_restart_awaiting_plan_and_cancel(settings):
    service = ResearchService(settings)
    await service.start()
    run = await service.create("u", CreateResearch(query="测试人工确认的持久化"), "k")
    run = await settle(service, run["run_id"])
    await service.stop()
    service = ResearchService(settings)
    await service.start()
    try:
        assert (await service.store.get(run["run_id"]))["status"] == "AWAITING_PLAN_CONFIRMATION"
        await service.cancel(run["run_id"])
        assert (await service.store.get(run["run_id"]))["status"] == "CANCELLED"
        with pytest.raises(ResearchError):
            await service.retry(run["run_id"])
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_gap_stops_without_fabricated_report(settings):
    service = ResearchService(settings)

    class Missing(DemoRunner):
        async def research(self, run, unit, dependencies):
            value = await super().research(run, unit, dependencies)
            value.raw_evidences = [e for e in value.raw_evidences if e.origin == "internal"]
            value.findings[0].raw_evidence_refs = [e.raw_id for e in value.raw_evidences]
            return value

    service.runner = Missing(settings, service.store)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="测试双源缺失时停止"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "FAILED" and run["error"]["code"] == "RESEARCH_GAPS"
        assert run["report"] is None and run["iteration"] <= 2
    finally:
        await service.stop()
