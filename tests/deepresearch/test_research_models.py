"""Research models are DeepResearch configuration placed on the engine's model factory."""

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from deepresearch.config import ModelSpec
from deepresearch.contracts import ResearchError
from deepresearch.models import compaction_config, engine_config, engine_model, model_for
from deepresearch.native import model_budget_config
from deepresearch.secrets import SECRETS


def host():
    from deerflow.config.app_config import AppConfig

    return AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "models": [{"name": "host-chat", "model": "host", "use": "langchain_openai:ChatOpenAI", "max_tokens": 32768}, {"name": "shared", "model": "host-shared", "use": "langchain_openai:ChatOpenAI"}],
        }
    )


@dataclass
class Role:
    name: str = "deepresearch-technical-route"
    model: str = "inherit"


def test_research_models_come_first_without_touching_the_host_config(settings, monkeypatch):
    monkeypatch.setenv("RESEARCH_TEST_KEY", "sk-research-test")
    settings.models = [
        ModelSpec(name="flash", provider="deepseek", model="deepseek-v4-flash", api_key="$RESEARCH_TEST_KEY", max_tokens=8192, context_window=128000, supports_thinking=True),
        ModelSpec(name="shared", provider="openai", model="gpt-local", base_url="http://127.0.0.1:8000/v1", api_key="$RESEARCH_TEST_KEY", temperature=0.2),
    ]
    original = host()
    config = engine_config(original, settings)
    flash = config.get_model_config("flash")
    assert flash.use == "deerflow.models.patched_deepseek:PatchedChatDeepSeek" and flash.model == "deepseek-v4-flash"
    assert flash.model_extra["api_key"] == "sk-research-test" and flash.model_extra["max_tokens"] == 8192
    assert flash.when_thinking_enabled == {"extra_body": {"thinking": {"type": "enabled"}}}
    # A research model shadows a host model of the same name in the private copy only.
    assert config.get_model_config("shared").model == "gpt-local" and config.get_model_config("shared").model_extra["base_url"] == "http://127.0.0.1:8000/v1"
    assert original.get_model_config("shared").model == "host-shared" and original.get_model_config("flash") is None
    assert config.get_model_config("host-chat") is not None and config.models[0].name == "flash"


def test_credentials_are_references_and_missing_ones_fail_clearly(monkeypatch):
    with pytest.raises(ValidationError, match="api_key must reference"):
        ModelSpec(name="m", model="x", api_key="sk-literal")
    with pytest.raises(ValidationError, match="class path"):
        ModelSpec(name="m", provider="custom", model="x")
    monkeypatch.delenv("MISSING_RESEARCH_KEY", raising=False)
    with pytest.raises(ResearchError, match="MISSING_RESEARCH_KEY") as error:
        engine_model(ModelSpec(name="m", model="x", api_key="$MISSING_RESEARCH_KEY"))
    assert error.value.code == "MODEL_AUTH_REQUIRED"
    SECRETS.set("research-test", "saved-value")
    try:
        assert engine_model(ModelSpec(name="m", model="x", api_key="secret:research-test")).model_extra["api_key"] == "saved-value"
    finally:
        SECRETS.set("research-test", None)


def test_role_model_precedence(settings):
    settings.models = [ModelSpec(name="first", model="a"), ModelSpec(name="second", model="b")]
    settings.default_model = None
    spec = settings.skills["technical-route"]
    assert model_for(settings, spec, Role()) == "first"
    settings.default_model = "second"
    assert model_for(settings, spec, Role()) == "second"
    spec.model = "first"
    assert model_for(settings, spec, Role()) == "first"


def test_output_ceiling_reaches_the_profile_the_engine_looks_up():
    config, name = model_budget_config(host(), Role(model="host-chat"), 4096)
    # Before rebuilding the name index the copy still returned the 32768 profile.
    assert name == "host-chat" and config.get_model_config("host-chat").model_extra["max_tokens"] == 4096


def test_compaction_is_research_configuration_not_the_host_chat_thresholds(settings):
    """Research decides when a role's context is compacted, from its own model's window."""
    settings.models = [ModelSpec(name="flash", provider="deepseek", model="deepseek-v4-flash", context_window=128000), ModelSpec(name="small", model="tiny")]
    settings.compaction = settings.compaction.model_copy(update={"trigger_fraction": 0.5, "keep_fraction": 0.25, "max_summary_input_tokens": 30000, "model": "flash"})
    config = engine_config(host(), settings)
    summarization = compaction_config(config, settings, "flash")
    assert summarization.enabled and summarization.model_name == "flash"
    assert [(item.type, item.value) for item in summarization.trigger] == [("tokens", 64000)]
    # Retention is a share of the trigger, so the tail left behind can never
    # re-trigger compaction on the very next turn.
    assert (summarization.keep.type, summarization.keep.value) == ("tokens", 16000)
    assert summarization.trim_tokens_to_summarize == 30000
    assert "{messages}" in summarization.summary_prompt and "verbatim quote" in summarization.summary_prompt
    # A model without a declared window falls back to the absolute trigger.
    fallback = compaction_config(config, settings, "small")
    assert [(item.type, item.value) for item in fallback.trigger] == [("tokens", settings.compaction.fallback_trigger_tokens)]
    # The host's own summarization settings are never consulted, and off means off.
    settings.compaction = settings.compaction.model_copy(update={"enabled": False})
    assert compaction_config(config, settings, "flash").enabled is False


def test_role_execution_config_carries_the_research_compaction_policy(settings):
    settings.models = [ModelSpec(name="flash", model="deepseek-v4-flash", context_window=200000)]
    settings.default_model = "flash"
    settings.compaction = settings.compaction.model_copy(update={"trigger_fraction": 0.75, "keep_fraction": 0.4})
    config, name = model_budget_config(engine_config(host(), settings), Role(model="flash"), 4096, settings=settings)
    assert name == "flash"
    assert [(item.type, item.value) for item in config.summarization.trigger] == [("tokens", 150000)]
    assert config.summarization.keep.value == 60000 and config.summarization.enabled


def test_a_conversation_id_reaches_the_gateway_only_where_the_model_names_it(settings):
    """Prefix caches live on one replica; a gateway needs the conversation's id to route by it."""
    from pydantic import ValidationError

    from deepresearch.models import session_overrides

    settings.models = [
        ModelSpec(name="gateway", model="qwen", base_url="https://gateway.example/v1", session_param="user", session_header="x-session-affinity", extra={"extra_body": {"enable_thinking": False}, "default_headers": {"x-team": "research"}}),
        ModelSpec(name="strict", model="qwen", base_url="https://strict.example/v1"),
        ModelSpec(name="official", model="gpt-5"),
        ModelSpec(name="claude", provider="anthropic", model="claude-sonnet-5", session_param="user", session_header="x-session-id"),
    ]
    settings.default_model = "gateway"
    config, _ = model_budget_config(engine_config(host(), settings), Role(model="gateway"), 4096, settings=settings, overrides={"extra_body": {"repetition_penalty": 1.05}}, session="dr-thread-1")
    extras = config.get_model_config("gateway").model_extra
    # The id joins the profile's and the node's own request fields.
    assert extras["extra_body"] == {"enable_thinking": False, "repetition_penalty": 1.05, "user": "dr-thread-1"}
    assert extras["default_headers"] == {"x-team": "research", "x-session-affinity": "dr-thread-1"}
    # A gateway that was not asked gets nothing new; OpenAI's endpoint gets its cache key.
    assert session_overrides(settings, "strict", "dr-thread-1") == {}
    assert session_overrides(settings, "official", "dr-thread-1") == {"extra_body": {"prompt_cache_key": "dr-thread-1"}}
    # A client without extra_body support only gets the header.
    assert session_overrides(settings, "claude", "dr-thread-1") == {"default_headers": {"x-session-id": "dr-thread-1"}}
    assert session_overrides(settings, "gateway", None) == {}
    # The id can never stand in for a credential or a field research sends itself.
    for bad in ({"session_header": "Authorization"}, {"session_header": "cookie"}, {"session_param": "messages"}, {"session_header": "x bad"}):
        with pytest.raises(ValidationError):
            ModelSpec(name="bad", model="m", **bad)


@pytest.mark.asyncio
async def test_direct_calls_consume_the_stream_and_a_tool_call_counts_as_an_answer():
    """Streaming-only servers return an empty message for a plain request.

    A streamed answer is used as-is — including a reply whose only content is a
    tool call — and a deployment that refuses streaming still gets a plain call.
    """
    from langchain_core.messages import AIMessage, AIMessageChunk

    from deepresearch.models import complete

    calls = []

    class Model:
        def __init__(self, chunks, plain):
            self.chunks, self.plain = chunks, plain

        async def astream(self, messages, **kwargs):
            calls.append("astream")
            if self.chunks is None:
                raise RuntimeError("streaming is disabled on this deployment")
            for chunk in self.chunks:
                yield chunk

        async def ainvoke(self, messages, **kwargs):
            calls.append("ainvoke")
            return self.plain

    text = await complete(Model([AIMessageChunk(content="rea"), AIMessageChunk(content="dy")], AIMessage(content="")), "hi")
    assert text.content == "ready" and calls == ["astream"]

    calls.clear()
    tool_chunk = AIMessageChunk(content="", tool_call_chunks=[{"name": "lookup", "args": '{"query": "x"}', "id": "c1", "index": 0}])
    answered = await complete(Model([tool_chunk], AIMessage(content="")), "hi")
    assert answered.tool_calls and calls == ["astream"]

    calls.clear()
    plain = await complete(Model(None, AIMessage(content="from a plain request")), "hi")
    assert plain.content == "from a plain request" and calls == ["astream", "ainvoke"]
