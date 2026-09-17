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
                # A run-level provider state stops research instead of degrading it.
                raise ResearchError("MODEL_BILLING_REQUIRED", "模型服务要求处理余额或计费状态")
            return await super().research(run, unit, dependencies)

    runner = Flaky(settings, service.store)
    service.runner = runner
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="测试局部失败恢复"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "FAILED" and run["error"]["code"] == "MODEL_BILLING_REQUIRED"
        await service.retry(run["run_id"])
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert runner.calls == {"R1": 1, "R2": 2}
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_an_isolated_step_failure_becomes_a_disclosed_gap_not_a_failed_run(settings):
    service = ResearchService(settings)

    class OneTimeout(DemoRunner):
        calls = []

        async def research(self, run, unit, dependencies):
            self.calls.append(unit.id)
            if unit.id == "R2":
                raise RuntimeError("SECRET-UPSTREAM-ERROR")
            return await super().research(run, unit, dependencies)

    runner = OneTimeout(settings, service.store)
    service.runner = runner
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="测试单个步骤失败"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert run["unit_statuses"]["R2"] == "FAILED" and run["unit_failures"] == {"R2": "EXECUTION_FAILED"}
        # The missing step was supplemented as a coverage gap, then disclosed.
        assert any(uid.startswith("S1-") for uid in runner.calls)
        assert any("未能完成（EXECUTION_FAILED）" in text for text in run["report"]["audit"]["limitations"])
        events = await service.store.events(run["run_id"], limit=1000)
        assert [e["data"]["code"] for e in events if e["type"] == "research.unit.failed"] == ["EXECUTION_FAILED"]
        assert "SECRET" not in str(events) and "SECRET" not in str(run)
        activity = await service.store.activity_events(run["run_id"])
        assert any(e["type"] == "research.unit.failed" for e in activity)
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_when_every_step_fails_the_run_fails_for_a_later_retry(settings):
    service = ResearchService(settings)

    class Down(DemoRunner):
        async def research(self, run, unit, dependencies):
            raise ResearchError("MODEL_UNAVAILABLE", "模型服务暂时不可用，可从检查点重试")

    service.runner = Down(settings, service.store)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="测试全部步骤失败"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "FAILED" and run["error"] == {"code": "MODEL_UNAVAILABLE", "message": "模型服务暂时不可用，可从检查点重试", "recoverable": True}
        assert not run.get("unit_failures")
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


class MissingExternal(DemoRunner):
    async def research(self, run, unit, dependencies):
        value = await super().research(run, unit, dependencies)
        value.raw_evidences = [e for e in value.raw_evidences if e.origin == "internal"]
        value.findings[0].raw_evidence_refs = [e.raw_id for e in value.raw_evidences]
        return value


@pytest.mark.asyncio
async def test_exhausted_gaps_write_a_limited_report_without_repeating_research(settings):
    service = ResearchService(settings)
    service.runner = MissingExternal(settings, service.store)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="测试双源缺失时如实说明"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert run["iteration"] <= 2 and run["report"]["limitations"]
        assert run["report"]["format"] == "markdown-v2" and run["report"]["audit"]["gaps"]
        events = await service.store.events(run["run_id"], limit=1000)
        assert [e["data"]["consent"] for e in events if e["type"] == "report.limitations.auto"] == ["settings"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_strict_deployments_still_require_owner_consent_for_gaps(settings):
    settings.allow_limited_report = False
    service = ResearchService(settings)
    service.runner = MissingExternal(settings, service.store)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="测试双源缺失时停止"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "FAILED" and run["error"]["code"] == "RESEARCH_GAPS"
        assert run["report"] is None and run["iteration"] <= 2
        calls = run["usage"]["tool_calls"]
        await service.retry(run["run_id"], allow_limited_report=True)
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert run["report"]["limitations"]
        assert run["usage"]["tool_calls"] == calls
        events = await service.store.events(run["run_id"], limit=1000)
        assert any(e["type"] == "report.limitations.policy" for e in events)
        assert [e["data"]["consent"] for e in events if e["type"] == "report.limitations.auto"] == ["owner"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_research_without_citable_evidence_fails_instead_of_writing(settings):
    service = ResearchService(settings)

    class DiscoveryOnly(DemoRunner):
        async def research(self, run, unit, dependencies):
            value = await super().research(run, unit, dependencies)
            for evidence in value.raw_evidences:
                evidence.provenance = "observed_source"
            return value

    service.runner = DiscoveryOnly(settings, service.store)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="只有搜索发现的链接"), "k")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["status"] == "FAILED" and run["error"]["code"] == "NO_EVIDENCE"
        assert run["report"] is None
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_final_validation_retry_does_not_reuse_rejected_draft(settings):
    service = ResearchService(settings)

    class Writer(DemoRunner):
        bad = True
        drafts = 0

        async def write_report(self, *args, **kwargs):
            self.drafts += 1
            value = await super().write_report(*args, **kwargs)
            if self.bad:
                value["document"] += "\n伪造引用。[[E999]]\n"
            return value

    runner = Writer(settings, service.store)
    service.runner = runner
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="Validate report recovery"), "retry-report")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        assert run["error"]["code"] == "FINAL_VALIDATION"
        calls, drafts = run["usage"]["tool_calls"], runner.drafts
        runner.bad = False
        await service.retry(run["run_id"])
        run = await settle(service, run["run_id"])
        assert run["status"] == "COMPLETED", run["error"]
        assert runner.drafts == drafts + 1
        assert run["usage"]["tool_calls"] == calls
        assert "E999" not in run["report"]["document"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_published_report_is_markdown_with_toc_citations_and_stats(settings):
    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("u", CreateResearch(query="比较技术路线与行业趋势"), "v2")
        run = await settle(service, run["run_id"])
        await service.decision(run["run_id"], 1, "approve")
        run = await settle(service, run["run_id"])
        report = run["report"]
        assert report["format"] == "markdown-v2" and report["title"].startswith("演示研究报告")
        assert report["toc"][0] == {"level": 2, "text": "执行摘要"}
        assert "(#citation-E" in report["display_markdown"] and "[[E" not in report["display_markdown"]
        assert "## 参考资料" in report["markdown"] and "<sup>" in report["html"]
        assert report["stats"]["citations"] == len(report["citations"]) == 4
        assert report["stats"]["elapsed_seconds"] is not None
        assert run["conversation"][-1] == {**run["conversation"][-1], "kind": "report", "text": report["title"]}
    finally:
        await service.stop()
