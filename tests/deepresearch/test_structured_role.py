"""Native structured roles see their contract without provider JSON mode."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from deepresearch.observations import NativeExecution
from deepresearch.store import Store
from deepresearch.structured import convert_answer, run_structured


@pytest.mark.asyncio
async def test_native_role_receives_schema_and_valid_output_needs_no_conversion_model(settings, monkeypatch):
    class Answer(BaseModel):
        value: int

    async def agent_config(_name):
        return settings.skills["deepresearch"], SimpleNamespace(model="inherit")

    captured = {}

    async def native(*args):
        captured.update(args[4])
        return NativeExecution(answer='{"value":7}', execution_id="native")

    def unexpected_model(**kwargs):
        pytest.fail("A valid native contract must not trigger another model request")

    monkeypatch.setattr("langgraph.runtime.get_runtime", lambda: SimpleNamespace(context={}))
    monkeypatch.setattr("deepresearch.structured.execute_role", native)
    monkeypatch.setattr("deerflow.models.create_chat_model", unexpected_model)
    runner = SimpleNamespace(settings=settings, store=None, _agent_config=agent_config)
    payload = {"goal": "structured role"}
    result = await run_structured(runner, {}, "deepresearch", payload, Answer)
    assert result.value == 7
    assert captured["output_schema"] == Answer.model_json_schema()
    assert "output_schema" not in payload


@pytest.mark.asyncio
async def test_reference_validation_retries_conversion_with_precise_feedback(settings, tmp_path, monkeypatch):
    class Answer(BaseModel):
        evidence_id: str

    store = Store(tmp_path / "repair.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "key", "hash")
    captured = []

    class Model:
        async def ainvoke(self, messages, **kwargs):
            captured.append(list(messages))
            return SimpleNamespace(content='{"evidence_id":"' + ("invented" if len(captured) == 1 else "known") + '"}')

    async def agent_config(name):
        return settings.skills[name], SimpleNamespace(model="local")

    def validate(value):
        if value.evidence_id != "known":
            raise ValueError("Unknown evidence ID: " + value.evidence_id)

    monkeypatch.setattr("deerflow.models.create_chat_model", lambda **kwargs: Model())
    runner = SimpleNamespace(settings=settings, store=store, _agent_config=agent_config)
    result = await convert_answer(runner, {"run_id": "r"}, "technical-route", {}, Answer, "ordinary notes", {}, validator=validate)
    assert result.evidence_id == "known"
    assert len(captured) == 2
    assert "Unknown evidence ID: invented" in captured[1][-1]["content"]


@pytest.mark.asyncio
async def test_reference_validation_never_accepts_persistent_fabrication(settings, tmp_path, monkeypatch):
    from deepresearch.contracts import ResearchError

    class Answer(BaseModel):
        evidence_id: str

    store = Store(tmp_path / "reject.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "key", "hash")
    calls = []

    class Model:
        async def ainvoke(self, messages, **kwargs):
            calls.append(1)
            return SimpleNamespace(content='{"evidence_id":"invented"}')

    async def agent_config(name):
        return settings.skills[name], SimpleNamespace(model="local")

    def reject(value):
        raise ValueError("Unknown evidence ID")

    monkeypatch.setattr("deerflow.models.create_chat_model", lambda **kwargs: Model())
    runner = SimpleNamespace(settings=settings, store=store, _agent_config=agent_config)
    with pytest.raises(ResearchError, match="bounded retries"):
        await convert_answer(runner, {"run_id": "r"}, "technical-route", {}, Answer, "notes", {}, validator=reject)
    assert len(calls) == settings.output_retries + 1


@pytest.mark.asyncio
async def test_final_conversion_attempt_prunes_unverifiable_references_without_inventing(settings, tmp_path, monkeypatch):
    class Answer(BaseModel):
        evidence_ids: list[str]

    store = Store(tmp_path / "prune.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "key", "hash")
    calls = []

    class Model:
        async def ainvoke(self, messages, **kwargs):
            calls.append(1)
            return SimpleNamespace(content='{"evidence_ids":["known","invented"]}')

    async def agent_config(name):
        return settings.skills[name], SimpleNamespace(model="local")

    def validate(value):
        if set(value.evidence_ids) - {"known"}:
            raise ValueError("Unknown evidence ID")

    def prune(value):
        return value.model_copy(update={"evidence_ids": [item for item in value.evidence_ids if item == "known"]})

    monkeypatch.setattr("deerflow.models.create_chat_model", lambda **kwargs: Model())
    runner = SimpleNamespace(settings=settings, store=store, _agent_config=agent_config)
    result = await convert_answer(runner, {"run_id": "r"}, "technical-route", {}, Answer, "notes", {}, validator=validate, repair=prune)
    assert result.evidence_ids == ["known"]
    assert len(calls) == settings.output_retries + 1
