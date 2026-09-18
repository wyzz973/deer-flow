"""Bridge research roles to DeerFlow's native subagent lifecycle.

Do not assemble a second middleware chain here. The native executor owns
authorization, skill activation, sandbox leases, provider compatibility,
compaction, loop guards, extension lifecycle and cancellation.
"""

import asyncio
import json
import logging
import time
from dataclasses import replace
from uuid import uuid4

from .config import ENGINE_TOOLS, FIXED_ROLES
from .contracts import ResearchError, utcnow
from .evidence import digest
from .models import compaction_config, engine_config, model_for, private_config
from .observations import NativeExecution
from .prompts import PromptSet
from .secrets import trace_secrets
from .trace import METRIC_KEYS, LocalTrace, metric_scope, model_callbacks, redact


def native_thread_id(run, skill_name, unit_id):
    """Bound host identifiers without truncation collisions or cycle reuse."""
    from deerflow.utils.thread_id import validate_thread_id

    identity = [run["thread_id"], run.get("cycle", 0), skill_name, unit_id]
    return validate_thread_id("dr-" + digest(identity)[:48])


def model_budget_config(app_config, role, max_output_tokens, *, run=None, researcher=False, settings=None):
    """Apply the research output ceiling and compaction policy to a private config.

    The native executor still creates the provider through its normal factory.
    Neither the operator's profile nor unrelated models are mutated.
    """
    from deerflow.subagents.config import resolve_subagent_model_name

    name = resolve_subagent_model_name(role, None, app_config=app_config)
    profile = app_config.get_model_config(name)
    if profile is None:
        raise ResearchError("MODEL_NOT_CONFIGURED", "Research model is not configured", recoverable=False)
    extras = profile.model_extra or {}
    key = "max_completion_tokens" if "max_completion_tokens" in extras else "max_tokens"
    configured = extras.get(key)
    limit = min(configured, max_output_tokens) if isinstance(configured, int) and configured > 0 else max_output_tokens
    updates = {key: limit}
    if profile.when_thinking_disabled is not None:
        disabled = dict(profile.when_thinking_disabled)
        if isinstance(disabled.get(key), int) and disabled[key] > 0:
            limit = min(limit, disabled[key])
        disabled[key] = limit
        updates["when_thinking_disabled"] = disabled
    bounded = profile.model_copy(update=updates)
    config_updates = {"models": [bounded if item.name == name else item for item in app_config.models]}
    if settings is not None:
        # Research owns when a role's context is compacted and what the summary keeps.
        config_updates["summarization"] = compaction_config(app_config, settings, name)
    if researcher and run and run.get("budget") and run["budget"].get("max_model_tokens") is not None:
        # Reuse the native middleware's soft wrap-up warning. The shared run
        # cap is still enforced separately; a warning is not a per-unit quota.
        # Reserve room for siblings, conversion, and report synthesis.
        from deerflow.config.subagents_config import SubagentOverrideConfig

        compaction = config_updates.get("summarization") or app_config.summarization
        policy = app_config.subagents.get_token_budget_for(role.name, summarization_enabled=getattr(compaction, "enabled", False))
        if not policy.enabled:
            return private_config(app_config, **config_updates), name
        lead_policy = app_config.token_budget
        total = run["budget"]["max_model_tokens"]
        remaining = max(0, total - run.get("usage", {}).get("model_tokens", 0))
        units = max(1, len(run.get("units") or []))
        ceiling = max(1000, min(total, policy.max_tokens, lead_policy.max_tokens if lead_policy.enabled else total))
        warning_at = (remaining / (units + 2)) * 0.5
        settings = {
            "enabled": True,
            "max_tokens": ceiling,
            "warn_threshold": min(policy.warn_threshold, lead_policy.warn_threshold if lead_policy.enabled else 1.0, warning_at / ceiling),
            "hard_stop_threshold": min(policy.hard_stop_threshold, lead_policy.hard_stop_threshold if lead_policy.enabled else 1.0),
        }
        for field in ("max_input_tokens", "max_output_tokens"):
            caps = [getattr(item, field) for item in (policy, lead_policy) if item.enabled and getattr(item, field) is not None]
            settings[field] = min(caps) if caps else None
        settings["warn_threshold"] = min(settings["warn_threshold"], settings["hard_stop_threshold"])
        override = app_config.subagents.agents.get(role.name) or SubagentOverrideConfig()
        overrides = {**app_config.subagents.agents, role.name: override.model_copy(update={"token_budget": policy.model_copy(update=settings)})}
        config_updates["subagents"] = app_config.subagents.model_copy(update={"agents": overrides})
    return private_config(app_config, **config_updates), name


def engine_tools(names):
    """Engine tool objects by model-facing name, without the host tool list."""
    from deerflow.reflection import resolve_variable

    return [resolve_variable(ENGINE_TOOLS[name]) for name in sorted(names) if name in ENGINE_TOOLS]


def output_instruction(skill_name, payload, prompts=None):
    prompts = prompts or PromptSet()
    if "output_schema" in payload:
        return prompts.schema_output
    if skill_name in FIXED_ROLES:
        return prompts.writer_output
    # Name the language: English skill methodology otherwise pulls progress
    # sentences into English even when the task payload says otherwise.
    language = payload.get("language") or "the language of the user's request"
    return prompts.researcher_output.replace("{language}", language)


async def execute_role(settings, store, run, skill_name, payload, tools, agent, context, *, task_id=None):
    from deerflow.config import get_app_config

    spec = settings.skills[skill_name]
    prompts = settings.prompts
    # The engine configuration with the research models in front of host models.
    app_config = engine_config(await asyncio.to_thread(get_app_config), settings)
    source_tools = list(tools or [])
    # The research methodology is injected below, so the native Skill index
    # would only make roles spend a turn reading the same file again. Native
    # skills unrelated to research stay available to agents that declare them.
    declared = getattr(agent, "skills", None)
    native_skills = None if declared is None else [name for name in declared if name not in settings.skills]
    has_native_skills = native_skills is None or bool(native_skills)
    # Candidates are the run's own source tools plus the engine tools research
    # settings allow. The host's tool list (shell, browsers, file writes) is
    # not inherited. Planner and writer never search.
    allowlist = set(spec.tools) if spec.tools is not None else None
    wanted = set() if skill_name in FIXED_ROLES else set(settings.engine_tools)
    wanted |= (allowlist or set()) & set(ENGINE_TOOLS)
    if has_native_skills and spec.agent is not None:
        wanted.add("read_file")
    if settings.native_tools is not None:
        wanted &= set(settings.native_tools)
    candidates = {t.name: t for t in engine_tools(wanted) + source_tools}
    if allowlist is not None:
        candidates = {name: tool for name, tool in candidates.items() if name in allowlist or (name == "read_file" and has_native_skills and spec.agent is not None)}
    skill = await asyncio.to_thread(settings.methodology, skill_name)
    role = replace(
        agent,
        system_prompt="\n\n".join(
            filter(
                None,
                [
                    agent.system_prompt,
                    spec.system_prompt,
                    skill,
                    prompts.role_guard,
                    output_instruction(skill_name, payload, prompts),
                    prompts.skill_files if has_native_skills else None,
                ],
            )
        ),
        skills=native_skills,
        model=model_for(settings, spec, agent) or agent.model,
        max_turns=min(spec.max_turns, agent.max_turns) if spec.max_turns is not None else agent.max_turns,
        timeout_seconds=min(spec.timeout_seconds, agent.timeout_seconds),
    )
    current_run = await store.get(run["run_id"])
    app_config, model_name = model_budget_config(
        app_config,
        role,
        settings.max_output_tokens,
        run=current_run,
        researcher=skill_name not in {"deepresearch", "report-synthesis"},
        settings=settings,
    )
    # Parallel writer tasks need distinct native threads; research units keep
    # their unit identity.
    unit_id = task_id or payload.get("unit", {}).get("id", skill_name)
    child_thread = native_thread_id(run, skill_name, unit_id)
    trace = LocalTrace(store, run["run_id"], settings, trace_secrets(settings, context))
    scope = {"cycle": run.get("cycle", 0), **(metric_scope.get() or {}), "unit_id": unit_id, "agent_name": role.name, "skill": skill_name, "purpose": "agent"}
    metrics = {"started_at": utcnow(), "started": time.monotonic(), "status": "failed"}
    try:
        return await _execute(settings, store, run, role, payload, candidates, context, trace, scope, metrics, model_name, child_thread, app_config)
    except asyncio.CancelledError:
        metrics["status"] = "cancelled"
        raise
    except BaseException as exc:
        metrics.update(status="failed", error_code=getattr(exc, "code", type(exc).__name__))
        raise
    finally:
        await _record_agent_run(store, run, scope, metrics, model_name, child_thread)


async def _record_agent_run(store, run, scope, metrics, model_name, thread_id):
    """One subagent execution: duration, outcome, model and tool usage."""
    try:
        totals = getattr(metrics.get("callbacks"), "totals", None) or {}
        body = {
            **{key: scope[key] for key in METRIC_KEYS if scope.get(key) is not None},
            "model": model_name,
            "thread_id": thread_id,
            "started_at": metrics["started_at"],
            "ended_at": utcnow(),
            "duration_ms": round((time.monotonic() - metrics["started"]) * 1000),
            "status": metrics["status"],
            "error_code": metrics.get("error_code"),
            "stop_reason": metrics.get("stop_reason"),
            "receipts": metrics.get("receipts", 0),
            **totals,
        }
        await store.record_agent_run(run["run_id"], scope.get("execution_id") or "unsubmitted-" + uuid4().hex, body)
    except Exception as exc:  # Metrics never change the research outcome.
        logging.getLogger("deepresearch.audit").warning(json.dumps({"event": "agent_metrics_failed", "error": type(exc).__name__}))


async def _execute(settings, store, run, role, payload, candidates, context, trace, scope, metrics, model_name, child_thread, app_config):
    from deerflow.subagents.executor import (
        SubagentExecutor,
        SubagentStatus,
        cleanup_background_task,
        get_background_task_result,
        request_cancel_background_task,
    )

    unit_id = scope["unit_id"]
    async with trace.span(role.name, "agent", {"unit_id": unit_id, "thread_id": child_thread, "task": payload, "tools": list(candidates)}) as output:
        callbacks = model_callbacks(trace, metered_tools=list(candidates.values()), model_name=model_name, scope=scope)
        metrics["callbacks"] = callbacks
        executor = SubagentExecutor(
            config=role,
            tools=list(candidates.values()),
            app_config=app_config,
            thread_id=child_thread,
            trace_id=run["run_id"],
            deerflow_trace_id=run["run_id"],
            run_id=run["run_id"],
            user_id=run["owner"],
            user_role=context.get("user_role"),
            is_internal=context.get("is_internal", False),
            request_secrets=context.get("secrets"),
            execution_callbacks=[callbacks],
        )
        execution_id = executor.execute_async(json.dumps(payload, ensure_ascii=False))
        scope["execution_id"] = execution_id
        try:
            # From the first await after submission, every exit must cancel and
            # drain the native worker, including an audit-store write failure.
            await store.event(run["run_id"], "research.agent.started", {"execution_id": execution_id, "agent": role.name, "unit_id": unit_id, "thread_id": child_thread, "parent_thread_id": run["thread_id"], "cycle": run.get("cycle", 0)})
            while True:
                result = get_background_task_result(execution_id)
                if result is None:
                    raise ResearchError("AGENT_LOST", "Native agent execution is no longer available")
                if result.status.is_terminal:
                    break
                await asyncio.sleep(0.1)
            if callbacks.budget_error:
                raise callbacks.budget_error
            if result.status != SubagentStatus.COMPLETED:
                # Provider exception strings may echo credentials. Persist only
                # typed metadata; callbacks classify known provider failures.
                output["failure"] = {"status": getattr(result.status, "value", "failed"), "stop_reason": getattr(result, "stop_reason", None)}
                if getattr(callbacks, "provider_error", None):
                    raise callbacks.provider_error
                if getattr(result.status, "value", None) == "timed_out":
                    raise ResearchError("NATIVE_AGENT_TIMEOUT", "原生 Agent 执行超时，可从检查点重试")
                raise ResearchError("NATIVE_AGENT_FAILED", "Native agent failed; inspect the local trace")
            output.update(execution_id=execution_id, result=result.result, stop_reason=result.stop_reason, tool_receipts=result.snapshot_tool_receipts())
            metrics.update(status="completed", stop_reason=result.stop_reason, receipts=len(result.snapshot_tool_receipts() or []))
            # Capture host envelopes only after the native model/tool loop has
            # finished. Redaction/bounding affects archival copies, never the
            # ToolMessage delivered to the researcher.
            messages = []
            for message in getattr(result, "ai_messages", None) or []:
                # Keep only message-envelope fields needed for research
                # provenance. Provider response metadata may contain headers.
                safe = {key: message[key] for key in ("type", "id", "name", "tool_call_id", "status", "content", "tool_calls", "additional_kwargs", "artifact") if key in message}
                for key in ("content", "additional_kwargs", "artifact"):
                    if key in safe:
                        safe[key] = redact(safe[key], trace.secrets, 20000)
                if "tool_calls" in safe:
                    safe["tool_calls"] = [{**call, "args": redact(call.get("args"), trace.secrets, 20000)} for call in safe["tool_calls"]]
                messages.append(safe)
            # Trace display limits must not change research semantics. Preserve
            # the complete visible answer while removing known credentials.
            answer = result.result or ""
            archived_answer = redact(answer, trace.secrets, max(20000, len(answer) * 8 + 256))
            if not isinstance(archived_answer, str):
                archived_answer = json.dumps(archived_answer, ensure_ascii=False)
            return NativeExecution(answer=archived_answer, execution_id=execution_id, messages=messages, receipts=result.snapshot_tool_receipts() or [], stop_reason=result.stop_reason)
        finally:
            result = get_background_task_result(execution_id)
            if result is not None and not result.status.is_terminal:
                request_cancel_background_task(execution_id)

            # Keep credential-bearing tool closures alive until the native
            # worker unwinds. Only then may missing terminal callbacks be
            # recorded as interrupted; never pretend in-flight I/O has stopped.
            async def drain():
                while (current := get_background_task_result(execution_id)) is not None and not current.status.is_terminal:
                    await asyncio.sleep(0.1)
                try:
                    cleanup_background_task(execution_id)
                finally:
                    await callbacks.close()

            cleanup = asyncio.create_task(drain())
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    interrupted = True
            cleanup.result()
            if interrupted:
                raise asyncio.CancelledError
