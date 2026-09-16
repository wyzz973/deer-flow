"""Only an operator-unbounded deployment may accept unbounded resource use."""

import asyncio

import pytest

from deepresearch.contracts import CreateResearch, ResearchBudget, ResearchError
from deepresearch.service import ResearchService


def unlimited():
    return ResearchBudget(max_model_tokens=None, max_tool_calls=None, max_elapsed_seconds=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["max_model_tokens", "max_tool_calls", "max_elapsed_seconds"])
async def test_client_cannot_disable_an_operator_ceiling(settings, field):
    request = CreateResearch(query="Test admission", budget=ResearchBudget(**{field: None}))
    service = ResearchService(settings)
    await service.store.start()
    with pytest.raises(ResearchError, match=field):
        await service.create("owner", request, "key")
    assert await service.store.list() == []


@pytest.mark.asyncio
async def test_unbounded_demo_runs_through_report_and_keeps_usage(settings):
    settings.budget_ceiling = unlimited()
    settings.plan_countdown_seconds = 0.05
    service = ResearchService(settings)
    await service.start()
    try:
        run = await service.create("owner", CreateResearch(query="Test complete research", budget=unlimited()), "key")
        async with asyncio.timeout(20):
            while True:
                current = await service.store.get(run["run_id"])
                if current["status"] in {"COMPLETED", "FAILED"} and run["run_id"] not in service.tasks:
                    break
                await asyncio.sleep(0.02)
        assert current["status"] == "COMPLETED", current.get("error")
        assert current["report"] is not None
        assert current["usage"]["elapsed_seconds"] > 0
        assert current["budget"]["max_model_tokens"] is None
    finally:
        await service.stop()
