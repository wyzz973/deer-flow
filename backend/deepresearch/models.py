"""Research models on the engine's model factory.

Research model profiles become engine ``ModelConfig`` entries inside a private
copy of the host AppConfig, ahead of any host model with the same name. The
engine then creates providers, applies thinking switches and streams usage as
usual; the host configuration object is never modified.
"""

from __future__ import annotations

from .config import MODEL_PROVIDERS
from .contracts import ResearchError
from .secrets import SECRETS

# Engine request parameters that switch reasoning on; the engine sends the
# matching "off" switch because research roles run with thinking disabled.
THINKING = {
    "deepseek": {"extra_body": {"thinking": {"type": "enabled"}}},
    "vllm": {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}},
    "anthropic": {"thinking": {"type": "enabled", "budget_tokens": 2048}},
}
CHAT_COMPLETIONS = "deepresearch.chat_completions:ChatCompletionsModel"


def legacy_token_param(spec):
    """Whether this model is sent ``max_tokens`` instead of ``max_completion_tokens``."""
    if spec.provider != "openai":
        return False
    if spec.max_tokens_param is not None:
        return spec.max_tokens_param == "max_tokens"
    from urllib.parse import urlsplit

    host = (urlsplit(spec.base_url or "").hostname or "").lower()
    return bool(host) and not host.endswith("openai.com") and not host.endswith("openai.azure.com")


# Providers whose client understands ``stream_usage`` (OpenAI-compatible chat completions).
STREAM_USAGE_PROVIDERS = {"openai", "vllm", "deepseek"}


def engine_model(spec):
    from deerflow.config.model_config import ModelConfig

    body = {
        **spec.extra,
        "name": spec.name,
        "display_name": spec.display_name or spec.name,
        "use": spec.use if spec.provider == "custom" else CHAT_COMPLETIONS if legacy_token_param(spec) else MODEL_PROVIDERS[spec.provider],
        "model": spec.model,
        "supports_thinking": spec.supports_thinking,
        "timeout": spec.timeout_seconds,
        "max_retries": spec.max_retries,
    }
    key = SECRETS.resolve(spec.api_key)
    if spec.api_key and not key:
        raise ResearchError("MODEL_AUTH_REQUIRED", f"研究模型 {spec.name} 的 API Key 未设置（{spec.api_key}）", recoverable=False)
    if key:
        body["api_key"] = key
    for field, value in (("base_url", spec.base_url), ("max_tokens", spec.max_tokens), ("context_window", spec.context_window), ("temperature", spec.temperature), ("top_p", spec.top_p)):
        if value is not None:
            body[field] = value
    # LangChain's OpenAI client stops asking for usage in streamed answers as
    # soon as base_url is set, and research streams every call: a model
    # gateway would then report no tokens at all, every call would be charged at
    # its full output cap and cost metrics would stay empty.
    if spec.stream_usage is not None or (spec.provider in STREAM_USAGE_PROVIDERS and "stream_usage" not in body):
        body["stream_usage"] = True if spec.stream_usage is None else spec.stream_usage
    if spec.supports_thinking and spec.provider in THINKING:
        body.setdefault("when_thinking_enabled", THINKING[spec.provider])
    return ModelConfig.model_validate(body)


def private_config(app_config, **updates):
    """Copy an AppConfig with updates and rebuild its name indexes.

    ``model_copy`` keeps the original private lookup tables, so without the
    rebuild a replaced or added model profile would never be found.
    """
    copy = app_config.model_copy(update=updates)
    copy._build_name_indexes()
    return copy


def engine_config(app_config, settings):
    """The host engine configuration with research models placed first."""
    if not settings.models:
        return app_config
    research = [engine_model(spec) for spec in settings.models]
    names = {model.name for model in research}
    return private_config(app_config, models=[*research, *[model for model in app_config.models if model.name not in names]])


def context_window(profile):
    """The model's declared context window, whether typed or an extra field."""
    if profile is None:
        return None
    window = getattr(profile, "context_window", None) or (profile.model_extra or {}).get("context_window")
    return window if isinstance(window, int) and window > 0 else None


def compaction_config(app_config, settings, model_name):
    """Engine summarization settings for one role, from the research profile.

    The host's own chat thresholds never apply to research: a researcher's
    context is mostly opened pages, and the summary must keep their URLs,
    quotes and dates. The trigger follows the role model's declared window so
    a large-context model is not compacted at a small model's threshold.
    """
    from deerflow.config.summarization_config import ContextSize

    spec = settings.compaction
    base = app_config.summarization
    if not spec.enabled:
        return base.model_copy(update={"enabled": False})
    window = context_window(app_config.get_model_config(model_name))
    threshold = max(1000, int(window * spec.trigger_fraction) if window else spec.fallback_trigger_tokens)
    return base.model_copy(
        update={
            "enabled": True,
            "model_name": spec.model,
            "trigger": [ContextSize(type="tokens", value=threshold)],
            "keep": ContextSize(type="tokens", value=max(500, int(threshold * spec.keep_fraction))),
            "trim_tokens_to_summarize": spec.max_summary_input_tokens,
            "summary_prompt": settings.prompts.compaction,
        }
    )


async def complete(model, messages, *, config=None):
    """One direct model exchange, consuming the stream the agent loop also uses.

    Many local servers (vLLM, SGLang and similar OpenAI-compatible deployments)
    answer only in streaming mode and return an empty message for a plain
    request; treating that as a refused contract failed research on those
    models. A deployment whose gateway rejects ``stream=true`` still works: the
    streamed attempt falls back to a plain request once.
    """
    kwargs = {"config": config} if config is not None else {}
    merged = None
    try:
        async for chunk in model.astream(messages, **kwargs):
            merged = chunk if merged is None else merged + chunk
    except (NotImplementedError, AttributeError):
        merged = None
    except Exception:  # A gateway that refuses streaming; the plain call reports the real error.
        merged = None
    if merged is not None and _has_answer(merged):
        return merged
    return await model.ainvoke(messages, **kwargs)


def _has_answer(message) -> bool:
    """Whether a response carries text or a tool call; a tool call alone is an answer."""
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return True
    if isinstance(content, list) and any(str(part.get("text", "") if isinstance(part, dict) else part).strip() for part in content):
        return True
    return bool(getattr(message, "tool_calls", None) or getattr(message, "tool_call_chunks", None))


def model_for(settings, spec=None, agent=None, node=None):
    """The model a call uses.

    A researcher's own model is the most specific choice, then the node's. For
    the fixed roles a node (plan, outline, section ...) is more specific than
    the role, which serves several nodes. Then the research default, a legacy
    host binding and the first research model.
    """
    agent_model = getattr(agent, "model", None)
    node_model = settings.node(node).model if node else None
    role_model = spec.model if spec else None
    chosen = (role_model or node_model) if node == "research" else (node_model or role_model)
    return chosen or settings.default_model or (agent_model if agent_model and agent_model != "inherit" else None) or (settings.models[0].name if settings.models else None)


def node_overrides(settings, node):
    """Sampling and request parameters a node adds to its model profile."""
    spec = settings.node(node) if node else None
    if spec is None:
        return {}
    updates = {name: value for name, value in (("temperature", spec.temperature), ("top_p", spec.top_p)) if value is not None}
    if spec.extra_body:
        updates["extra_body"] = dict(spec.extra_body)
    return updates


def node_output_cap(settings, node):
    """Output tokens one call of this node may write."""
    spec = settings.node(node) if node else None
    return (spec.max_tokens if spec and spec.max_tokens else None) or settings.max_output_tokens


def node_thinking(settings, node):
    """Whether this node asks its model to reason before it answers."""
    spec = settings.node(node) if node else None
    return bool(spec.thinking) if spec is not None else False


def thinking_switch(profile):
    """The request fields that switch this model's reasoning on, or None.

    Research reads them itself instead of letting the engine apply them: the
    engine's switch is off on every research path, and it replaces whole
    request fields when it applies either switch, which would drop the node's
    own body and the gateway's session key.
    """
    if not profile.supports_thinking:
        return None
    switch = dict(profile.when_thinking_enabled or {})
    if profile.thinking:
        switch["thinking"] = {**(switch.get("thinking") or {}), **profile.thinking}
    return switch or None


def with_thinking(profile, on, body=None):
    """A profile copy whose reasoning follows the node instead of the engine.

    Every research model is created with the engine's thinking switch off — the
    native subagent executor hardcodes it and the direct calls match it — and
    on that path the factory sends ``when_thinking_disabled`` verbatim, ahead of
    everything else. That makes it the one field a node's own choice can reach,
    so a node asking for reasoning puts the model's "on" switch there. Whole
    request fields are replaced from it, so the switch is folded into the
    finished request body (``body``, else the profile's own) rather than
    replacing the node's parameters and the gateway's session key.
    """
    switch = thinking_switch(profile) if on and profile is not None else None
    if not switch:
        return profile
    disabled = {**(profile.when_thinking_disabled or {}), **switch}
    current = body if body is not None else (profile.model_extra or {}).get("extra_body")
    if isinstance(current, dict):
        disabled["extra_body"] = {**current, **(disabled.get("extra_body") or {})}
    return profile.model_copy(update={"when_thinking_disabled": disabled})


# Request fields that are dictionaries: an override adds keys to the profile's
# own instead of replacing them (for example the provider's thinking switch).
MERGED_FIELDS = ("extra_body", "default_headers")


def merged_overrides(profile, *overrides):
    """Several override sets as one, with dictionary fields merged key by key."""
    extras = (profile.model_extra or {}) if profile is not None else {}
    updates = {}
    for item in overrides:
        for key, value in (item or {}).items():
            if key in MERGED_FIELDS:
                current = updates.get(key, extras.get(key))
                value = {**(current if isinstance(current, dict) else {}), **value}
            updates[key] = value
    return updates


def with_node(profile, *overrides):
    """A copy of an engine model profile with a node's parameters applied."""
    updates = merged_overrides(profile, *overrides)
    return profile.model_copy(update=updates) if updates else profile


def official_openai(spec):
    from urllib.parse import urlsplit

    host = (urlsplit(spec.base_url or "").hostname or "").lower()
    return spec.provider == "openai" and (not host or host.endswith("openai.com"))


def session_overrides(settings, model_name, session):
    """Request additions that let a gateway keep one conversation on one replica.

    ``session`` identifies a thread of requests that share a growing prefix: a
    role execution, or the bounded retries of one direct call. It is a digest,
    never a user, run or credential value.
    """
    spec = next((model for model in settings.models if model.name == model_name), None)
    if spec is None or not session:
        return {}
    param = spec.session_param or ("prompt_cache_key" if official_openai(spec) else None)
    updates = {}
    # Only OpenAI-compatible clients take extra_body; others get the header alone.
    if param and spec.provider in STREAM_USAGE_PROVIDERS | {"custom"}:
        updates["extra_body"] = {param: session}
    if spec.session_header:
        updates["default_headers"] = {spec.session_header: session}
    return updates
