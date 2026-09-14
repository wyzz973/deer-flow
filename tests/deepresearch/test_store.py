import asyncio
import pytest

from deepresearch.contracts import ResearchError
from deepresearch.store import ProcessLock, Store


@pytest.mark.asyncio
async def test_idempotency_events_and_atomic_budget(tmp_path):
    store = Store(tmp_path / "data.sqlite")
    await store.start()
    run = {"run_id": "run1", "owner": "u1", "usage": {"tool_calls": 0, "model_tokens": 0}, "budget": {"max_tool_calls": 2, "max_model_tokens": 100}}
    one, created = await store.create(run, "key", "hash")
    two, duplicate = await store.create(run, "key", "hash")
    assert created and not duplicate and one == two
    with pytest.raises(ResearchError): await store.create(run, "key", "different")
    outcomes = await asyncio.gather(*(store.reserve("run1", tool_calls=1) for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(x, ResearchError) for x in outcomes) == 3
    assert (await store.get("run1"))["usage"]["tool_calls"] == 2
    await asyncio.gather(*(store.event("run1", "same", key="same") for _ in range(10)))
    assert len(await store.events("run1")) == 1
    await asyncio.gather(*(store.event("run1", "different") for _ in range(10)))
    events = await store.events("run1", after=1)
    assert [e["seq"] for e in events] == list(range(2, 12))
    await store.cache("run1", "tool", [])
    assert await store.cached("run1", "tool") == []
    await store.save_unit("run1", "R1", "input1", {"success": True})
    assert await store.unit("run1", "R1", "input1") == {"success": True}
    with pytest.raises(ResearchError): await store.unit("run1", "R1", "changed")


def test_single_process_guard(tmp_path):
    first, second = ProcessLock(tmp_path / "worker.lock"), ProcessLock(tmp_path / "worker.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError): second.acquire()
    finally: first.release()
    second.acquire(); second.release()
