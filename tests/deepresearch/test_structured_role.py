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

    created = []

    def create(**kwargs):
        created.append(kwargs)
        return Model()

    from deepresearch.config import ModelSpec

    settings.models = [ModelSpec(name="local", model="qwen", base_url="https://gateway.example/v1", session_param="user", extra={"extra_body": {"enable_thinking": False}})]
    settings.default_model = "local"
    monkeypatch.setattr("deerflow.models.create_chat_model", create)
    runner = SimpleNamespace(settings=settings, store=store, _agent_config=agent_config)
    result = await convert_answer(runner, {"run_id": "r"}, "technical-route", {"unit": {"id": "R1"}}, Answer, "ordinary notes", {}, validator=validate)
    assert result.evidence_id == "known"
    assert len(captured) == 2
    assert "Unknown evidence ID: invented" in captured[1][-1]["content"]
    # The task leads and the answer ends the request, and the retry keeps both:
    # what conversions of one run share stays a reusable prompt prefix.
    assert captured[0][1]["content"].startswith('{"task"') and captured[1][:2] == captured[0][:2]
    # The call and its retry are one conversation for a gateway that routes by
    # id; the id joins the profile's request fields instead of replacing them.
    body = created[0]["model_overrides"]["extra_body"]
    assert body["enable_thinking"] is False and body["user"].startswith("dr-") and len(body["user"]) <= 64


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


@pytest.mark.asyncio
async def test_streaming_only_providers_still_produce_a_contract(settings, tmp_path, monkeypatch):
    """Many local servers answer only in streaming mode.

    A non-streaming request there returns an empty message, which used to look
    like a model that refused the contract. Direct research calls must consume
    the stream the way the agent loop already does.
    """
    from langchain_core.messages import AIMessageChunk

    class Answer(BaseModel):
        value: int

    store = Store(tmp_path / "streaming.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "key", "hash")
    calls = []

    class StreamingOnlyModel:
        async def ainvoke(self, messages, **kwargs):
            calls.append("ainvoke")
            return AIMessageChunk(content="")  # what a streaming-only server returns

        async def astream(self, messages, **kwargs):
            calls.append("astream")
            for piece in ('{"value"', ": 11", "}"):
                yield AIMessageChunk(content=piece)

    async def agent_config(name):
        return settings.skills[name], SimpleNamespace(model="local")

    monkeypatch.setattr("deerflow.models.create_chat_model", lambda **kwargs: StreamingOnlyModel())
    runner = SimpleNamespace(settings=settings, store=store, _agent_config=agent_config)
    result = await convert_answer(runner, {"run_id": "r"}, "technical-route", {}, Answer, "ordinary notes", {})
    assert result.value == 11
    assert calls == ["astream"]


@pytest.mark.asyncio
async def test_a_provider_that_refuses_streaming_still_answers(settings, tmp_path, monkeypatch):
    """The opposite deployment: a gateway that rejects stream=true."""
    from types import SimpleNamespace as Namespace

    class Answer(BaseModel):
        value: int

    store = Store(tmp_path / "no-stream.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u"}, "key", "hash")
    calls = []

    class NoStreamingModel:
        async def ainvoke(self, messages, **kwargs):
            calls.append("ainvoke")
            return Namespace(content='{"value": 3}')

        async def astream(self, messages, **kwargs):
            calls.append("astream")
            raise RuntimeError("streaming is not supported by this deployment")
            yield  # pragma: no cover - generator marker

    async def agent_config(name):
        return settings.skills[name], Namespace(model="local")

    monkeypatch.setattr("deerflow.models.create_chat_model", lambda **kwargs: NoStreamingModel())
    runner = SimpleNamespace(settings=settings, store=store, _agent_config=agent_config)
    result = await convert_answer(runner, {"run_id": "r"}, "technical-route", {}, Answer, "notes", {})
    assert result.value == 3
    assert calls == ["astream", "ainvoke"]
