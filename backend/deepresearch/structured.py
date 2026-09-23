"""Convert a native role's answer into the workflow's validated contract.

The research agent is free to use normal Chat Completions and its native tool
loop. Only this boundary asks for a data object, using ordinary text messages;
it never sends response_format or invokes with_structured_output.
"""

import asyncio
import json

from .contracts import ResearchError, ResearchRequest
from .evidence import digest
from .models import complete, engine_config, merged_overrides, model_for, node_output_cap, node_overrides, node_thinking, private_config, session_overrides, with_thinking
from .native import execute_role, node_of
from .output import parse_contract, visible_text
from .secrets import trace_secrets
from .trace import LocalTrace, model_callbacks

TRUNCATED_RETRY = "Your previous reply was cut off at the output limit before the object was complete. Return a shorter object: fewer items (keep the most important), each text field in one or two sentences, no repetition."


def structured_task(payload, schema):
    """The task a native role receives: the payload plus our output contract.

    A task that opens with its instructions opens with what never changes; the
    contract is just as constant and joins it there, ahead of anything a prompt
    cache has not seen. A long task that keeps its instructions next to the
    point of generation keeps the contract there too.
    """
    contract = schema.model_json_schema()
    if next(iter(payload), None) == "instructions":
        return {"instructions": payload["instructions"], "output_schema": contract, **payload}
    return {**payload, "output_schema": contract}


async def run_structured(runner, run, skill_name, payload, schema, tools=None, agent=None, *, validator=None, repair=None, task_id=None):
    from .trace import current_context

    spec, configured = await runner._agent_config(skill_name)
    context = current_context()
    # Ask the native role for our output contract up front. This is ordinary
    # prompt text, not provider JSON mode; conversion remains a fallback.
    native_payload = structured_task(payload, schema)
    kwargs = {"task_id": task_id} if task_id else {}
    execution = await execute_role(runner.settings, runner.store, run, skill_name, native_payload, tools, agent or configured, context, **kwargs)
    return await convert_answer(runner, run, skill_name, payload, schema, execution.answer, context, validator=validator, repair=repair)


def role_model(settings, spec, configured, node=None):
    """Model for a direct call on behalf of a role, mirroring its native execution."""
    return model_for(settings, spec, configured, node)


def _create_model(settings, model_name, max_tokens, node=None, session=None):
    from deerflow.config import get_app_config
    from deerflow.models import create_chat_model

    app_config = engine_config(get_app_config(), settings)
    profile = app_config.get_model_config(model_name)
    # The engine replaces whole fields, so dictionary fields are merged with the
    # profile's own here (a node's extra_body keeps the provider's switches).
    overrides = {"max_tokens": max_tokens, **merged_overrides(profile, node_overrides(settings, node), session_overrides(settings, model_name, session))}
    # A direct call asks for a model with thinking off like every other research
    # call; a node that wants reasoning carries the switch on its own profile,
    # which is where the factory reads it (see with_thinking).
    reasoning = with_thinking(profile, node_thinking(settings, node), overrides.get("extra_body"))
    if reasoning is not profile:
        app_config = private_config(app_config, models=[reasoning if item.name == model_name else item for item in app_config.models])
    model = create_chat_model(name=model_name, thinking_enabled=False, app_config=app_config, attach_tracing=False, model_overrides=overrides)
    if node and settings.node(node).json_mode:
        # Optional: the contract is always requested in prompt text as well, so
        # a gateway without response_format support simply leaves this off.
        model = model.bind(response_format={"type": "json_object"})
    return model


async def _contract_call(runner, run, *, model_name, messages, schema, span, scope, timeout, context, validated, max_tokens, node=None):
    """One direct model exchange with bounded, feedback-driven retries."""
    settings = runner.settings
    tuning = settings.node(node) if node else None
    retries = tuning.output_retries if tuning is not None and tuning.output_retries is not None else settings.output_retries
    timeout = (tuning.timeout_seconds if tuning is not None else None) or timeout
    # One direct call and its bounded retries share a prefix: that is the conversation.
    session = "dr-" + digest([run["run_id"], run.get("cycle", 0), span, scope.get("unit_id")])[:48]
    model = await asyncio.to_thread(_create_model, settings, model_name, max_tokens, node, session)
    trace = LocalTrace(runner.store, run["run_id"], settings, trace_secrets(settings, context))
    last_text = ""
    if node:
        scope = {**scope, "config_node": node}
    async with trace.span(span, "conversion") as output:
        callbacks = model_callbacks(trace, model_name=model_name, scope=scope, output_cap=max_tokens)
        for attempt in range(retries + 1):
            try:
                response = await asyncio.wait_for(complete(model, messages, config={"callbacks": [callbacks]}), timeout=timeout)
            except Exception:
                if getattr(callbacks, "provider_error", None):
                    raise callbacks.provider_error from None
                raise
            last_text = visible_text(response.content)
            truncated = (getattr(response, "response_metadata", None) or {}).get("finish_reason") == "length"
            try:
                value = validated(last_text, final=attempt == retries)
                output.update(attempts=attempt + 1, contract=value.model_dump(mode="json"))
                return value, last_text
            except ValueError as error:
                await runner.store.event(run["run_id"], "research.output.retry", {"skill": scope.get("skill"), "contract": schema.__name__, "attempt": attempt + 1, "truncated": truncated})
                if truncated:
                    # The object was cut off at the output cap. Repeating the
                    # same request would be cut off again, and the broken half
                    # is no use as context: ask for a shorter object instead.
                    messages = messages[:2] + [{"role": "user", "content": TRUNCATED_RETRY + "\n" + settings.prompts.converter_retry}]
                    continue
                # Keep one failed answer, not an ever-growing retry transcript.
                messages = messages[:2] + [
                    {"role": "assistant", "content": last_text},
                    {"role": "user", "content": str(error)[:2000] + "\n" + settings.prompts.converter_retry},
                ]
        output.update(attempts=retries + 1, failed=True)
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
    # nodes.conversion.model, then the older extraction_model, then the role's own model.
    model_name = settings.node("conversion").model or settings.extraction_model or role_model(settings, spec, configured, node_of(skill_name, payload))
    messages = [
        {"role": "system", "content": settings.prompts.converter_system + " Schema:\n" + json.dumps(schema.model_json_schema())},
        # The task opens with what every step of the run shares; the answer is
        # unique to this call, so it goes last and the shared part stays a
        # reusable prompt prefix.
        {"role": "user", "content": json.dumps({"task": payload, "answer": answer}, ensure_ascii=False)},
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
        max_tokens=node_output_cap(settings, "conversion"),
        node="conversion",
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
    model_name = settings.node("rewrite").model or settings.rewrite_model or role_model(settings, spec, configured)
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
        max_tokens=settings.node("rewrite").max_tokens or min(settings.max_output_tokens, 4096),
        node="rewrite",
    )
    if value is not None:
        return value
    prose = text.strip()
    if prose.startswith(("{", "```")):
        # A broken object is not a request: planning on raw JSON text sent the
        # braces and field names to every researcher. Keep only its user_query.
        import re

        found = re.search(r'"user_query"\s*:\s*"((?:[^"\\]|\\.)*)', prose, re.S)
        prose = ""
        if found:
            try:
                prose = str(json.loads('"' + found.group(1) + '"')).strip()
            except ValueError:
                prose = found.group(1).strip()
        if len(prose) < 3:
            prose = " ".join(str(payload.get("latest_user_message") or run.get("query") or "").split())
    if len(prose) >= 3:
        await runner.store.event(run["run_id"], "research.request.prose", {"characters": len(prose)})
        return ResearchRequest(user_query=prose[:12000])
    raise ResearchError("OUTPUT_SCHEMA", "The research request could not be rewritten; inspect the local trace")
