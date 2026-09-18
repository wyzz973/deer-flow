"""Convert a native role's answer into the workflow's validated contract.

The research agent is free to use normal Chat Completions and its native tool
loop. Only this boundary asks for a data object, using ordinary text messages;
it never sends response_format or invokes with_structured_output.
"""

import asyncio
import json

from .contracts import ResearchError, ResearchRequest
from .models import complete, engine_config, model_for
from .native import execute_role
from .output import parse_contract, visible_text
from .secrets import trace_secrets
from .trace import LocalTrace, model_callbacks


async def run_structured(runner, run, skill_name, payload, schema, tools=None, agent=None, *, validator=None, repair=None, task_id=None):
    from .trace import current_context

    spec, configured = await runner._agent_config(skill_name)
    context = current_context()
    # Ask the native role for our output contract up front. This is ordinary
    # prompt text, not provider JSON mode; conversion remains a fallback.
    native_payload = {**payload, "output_schema": schema.model_json_schema()}
    kwargs = {"task_id": task_id} if task_id else {}
    execution = await execute_role(runner.settings, runner.store, run, skill_name, native_payload, tools, agent or configured, context, **kwargs)
    return await convert_answer(runner, run, skill_name, payload, schema, execution.answer, context, validator=validator, repair=repair)


def role_model(settings, spec, configured):
    """Model for a direct call on behalf of a role, mirroring its native execution."""
    return model_for(settings, spec, configured)


def _create_model(settings, model_name, max_tokens):
    from deerflow.config import get_app_config
    from deerflow.models import create_chat_model

    return create_chat_model(name=model_name, thinking_enabled=False, app_config=engine_config(get_app_config(), settings), attach_tracing=False, model_overrides={"max_tokens": max_tokens})


async def _contract_call(runner, run, *, model_name, messages, schema, span, scope, timeout, context, validated, max_tokens):
    """One direct model exchange with bounded, feedback-driven retries."""
    settings = runner.settings
    model = await asyncio.to_thread(_create_model, settings, model_name, max_tokens)
    trace = LocalTrace(runner.store, run["run_id"], settings, trace_secrets(settings, context))
    last_text = ""
    async with trace.span(span, "conversion") as output:
        callbacks = model_callbacks(trace, model_name=model_name, scope=scope)
        for attempt in range(settings.output_retries + 1):
            try:
                response = await asyncio.wait_for(complete(model, messages, config={"callbacks": [callbacks]}), timeout=timeout)
            except Exception:
                if getattr(callbacks, "provider_error", None):
                    raise callbacks.provider_error from None
                raise
            last_text = visible_text(response.content)
            try:
                value = validated(last_text, final=attempt == settings.output_retries)
                output.update(attempts=attempt + 1, contract=value.model_dump(mode="json"))
                return value, last_text
            except ValueError as error:
                await runner.store.event(run["run_id"], "research.output.retry", {"skill": scope.get("skill"), "contract": schema.__name__, "attempt": attempt + 1})
                # Keep one failed answer, not an ever-growing retry transcript.
                messages = messages[:2] + [
                    {"role": "assistant", "content": last_text},
                    {"role": "user", "content": str(error)[:2000] + "\n" + settings.prompts.converter_retry},
                ]
        output.update(attempts=settings.output_retries + 1, failed=True)
    return None, last_text


async def convert_answer(runner, run, skill_name, payload, schema, answer, context, *, validator=None, repair=None):
    """Validate our own output contract; never normalize a tool's return value.

    ``repair`` may only remove what the validator rejects on the final bounded
    attempt (for example unverifiable references). It must never add data.
    """
    spec, configured = await runner._agent_config(skill_name)

    def validated(text, final=False):
        value = parse_contract(text, schema)
        if validator is not None:
            try:
                validator(value)
            except ValueError:
                if not (final and repair is not None):
                    raise
                value = repair(value)
                validator(value)
        return value

    try:
        return validated(answer)
    except ValueError:
        pass
    settings = runner.settings
    model_name = settings.extraction_model or role_model(settings, spec, configured)
    messages = [
        {"role": "system", "content": settings.prompts.converter_system + " Schema:\n" + json.dumps(schema.model_json_schema())},
        {"role": "user", "content": json.dumps({"answer": answer, "task": payload}, ensure_ascii=False)},
    ]
    unit = payload.get("unit") if isinstance(payload.get("unit"), dict) else {}
    scope = {"purpose": "conversion", "skill": skill_name, "contract": schema.__name__, **({"unit_id": unit["id"]} if unit.get("id") else {})}
    value, _ = await _contract_call(
        runner,
        run,
        model_name=model_name,
        messages=messages,
        schema=schema,
        span="contract:" + schema.__name__,
        scope=scope,
        timeout=spec.timeout_seconds,
        context=context,
        validated=validated,
        max_tokens=settings.max_output_tokens,
    )
    if value is None:
        raise ResearchError("OUTPUT_SCHEMA", "Output conversion failed after bounded retries; inspect the local trace")
    return value


async def rewrite_request(runner, run, payload):
    """Rewrite the conversation into a complete research request.

    A reply that never becomes the JSON contract still carries the rewritten
    request as prose, so its text is used rather than failing the run.
    """
    from .trace import current_context

    settings = runner.settings
    spec, configured = await runner._agent_config("deepresearch")
    context = current_context()
    model_name = settings.rewrite_model or role_model(settings, spec, configured)
    messages = [
        {"role": "system", "content": settings.prompts.rewrite},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    value, text = await _contract_call(
        runner,
        run,
        model_name=model_name,
        messages=messages,
        schema=ResearchRequest,
        span="rewrite:ResearchRequest",
        scope={"purpose": "rewrite", "skill": "deepresearch", "contract": "ResearchRequest"},
        timeout=spec.timeout_seconds,
        context=context,
        validated=lambda reply, final=False: parse_contract(reply, ResearchRequest),
        max_tokens=min(settings.max_output_tokens, 4096),
    )
    if value is not None:
        return value
    prose = text.strip()
    if len(prose) >= 3:
        await runner.store.event(run["run_id"], "research.request.prose", {"characters": len(prose)})
        return ResearchRequest(user_query=prose[:12000])
    raise ResearchError("OUTPUT_SCHEMA", "The research request could not be rewritten; inspect the local trace")
