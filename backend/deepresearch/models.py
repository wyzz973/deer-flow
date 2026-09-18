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


def engine_model(spec):
    from deerflow.config.model_config import ModelConfig

    body = {
        **spec.extra,
        "name": spec.name,
        "display_name": spec.display_name or spec.name,
        "use": spec.use if spec.provider == "custom" else MODEL_PROVIDERS[spec.provider],
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
    for field, value in (("base_url", spec.base_url), ("max_tokens", spec.max_tokens), ("context_window", spec.context_window), ("temperature", spec.temperature)):
        if value is not None:
            body[field] = value
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


def model_for(settings, spec=None, agent=None):
    """The model a role uses: its own, the research default, a legacy host binding, then the first research model."""
    agent_model = getattr(agent, "model", None)
    return (spec.model if spec else None) or settings.default_model or (agent_model if agent_model and agent_model != "inherit" else None) or (settings.models[0].name if settings.models else None)
