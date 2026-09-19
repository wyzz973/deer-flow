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
from .models import compaction_config, engine_config, model_for, node_output_cap, node_overrides, private_config, session_overrides, with_node
from .observations import NativeExecution, derived_receipts
from .prompts import PromptSet
from .secrets import trace_secrets
from .store import RESEARCH_TURN_OUTPUT, research_tokens_left
from .trace import METRIC_KEYS, LocalTrace, metric_scope, model_callbacks, redact, redact_content


def native_thread_id(run, skill_name, unit_id):
    """Bound host identifiers without truncation collisions or cycle reuse."""
    from deerflow.utils.thread_id import validate_thread_id

    identity = [run["thread_id"], run.get("cycle", 0), skill_name, unit_id]
    return validate_thread_id("dr-" + digest(identity)[:48])


def model_budget_config(app_config, role, max_output_tokens, *, run=None, researcher=False, settings=None, concurrency=None, overrides=None, session=None):
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
    # The node's sampling parameters live on the same private profile, so the
    # engine's own factory applies them and metrics keep the model's real name.
    bounded = with_node(profile.model_copy(update=updates), overrides, session_overrides(settings, name, session) if settings is not None else None)
    config_updates = {"models": [bounded if item.name == name else item for item in app_config.models]}
    if settings is not None:
        # Research owns when a role's context is compacted and what the summary keeps.
        config_updates["summarization"] = compaction_config(app_config, settings, name)
        verification = getattr(app_config, "verification", None)
        if verification is not None and not settings.tool_receipt_ledger:
            # The engine renders the receipt ledger right after the system
            # prompt and rewrites it on every turn, which leaves no reusable
            # prompt prefix in an agent loop. Its switch covers stamping too,
            # so the receipts evidence refers to are derived from the same
            # tool messages once the execution ends (derived_receipts).
            config_updates["verification"] = verification.model_copy(update={"receipts_enabled": False})
    if researcher and run and run.get("budget") and run["budget"].get("max_model_tokens") is not None:
        # A step's share of what research may still spend, enforced by the
        # native middleware: a warning at half tells the model to wrap up, and
        # the stop strips tool calls so the step ends with its notes. Without
        # it the shared ledger refused a turn mid-step and the step failed,
        # losing everything it had read. The ledger stays the backstop.
        from deerflow.config.subagents_config import SubagentOverrideConfig

        compaction = config_updates.get("summarization") or app_config.summarization
        policy = app_config.subagents.get_token_budget_for(role.name, summarization_enabled=getattr(compaction, "enabled", False))
        # The run's budget belongs to research, so its share applies even where
        # the host turned its own backstop off for research roles; host caps
        # that are on can only tighten it.
        host = [item for item in (policy, app_config.token_budget) if item.enabled]
        statuses = run.get("unit_statuses") or {}
        unfinished = [unit for unit in run.get("units") or [] if statuses.get(unit.get("id") if isinstance(unit, dict) else unit) not in {"COMPLETED", "FAILED"}]
        sharing = max(1, min(concurrency or len(unfinished), len(unfinished)))
        # The margin pays for converting the notes into findings.
        share = max(1000, (research_tokens_left(run) or 0) // sharing - 2 * RESEARCH_TURN_OUTPUT)
        limits = {
            "enabled": True,
            "max_tokens": min([share, *(item.max_tokens for item in host)]),
            "warn_threshold": min([0.5, *(item.warn_threshold for item in host)]),
            "hard_stop_threshold": min([1.0, *(item.hard_stop_threshold for item in host)]),
        }
        for field in ("max_input_tokens", "max_output_tokens"):
            caps = [getattr(item, field) for item in host if getattr(item, field) is not None]
            limits[field] = min(caps) if caps else None
        limits["warn_threshold"] = min(limits["warn_threshold"], limits["hard_stop_threshold"])
        override = app_config.subagents.agents.get(role.name) or SubagentOverrideConfig()
        overrides = {**app_config.subagents.agents, role.name: override.model_copy(update={"token_budget": policy.model_copy(update=limits)})}
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


def node_of(skill_name, payload):
    """The workflow node an execution belongs to, from its role and task."""
    if skill_name == "report-synthesis":
        return {"outline": "outline", "write_section": "section", "write_summary": "summary", "revise_report": "revision"}.get(payload.get("task"), "section")
    if skill_name == "deepresearch":
        return "follow_up" if "message" in payload and "research_request" not in payload else "plan"
    return "research"


async def execute_role(settings, store, run, skill_name, payload, tools, agent, context, *, task_id=None, node=None, timeout_seconds=None):
    from deerflow.config import get_app_config

    node = node or node_of(skill_name, payload)
    tuning = settings.node(node)
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
        model=model_for(settings, spec, agent, node) or agent.model,
        max_turns=min(spec.max_turns, agent.max_turns) if spec.max_turns is not None else agent.max_turns,
        # The node's timeout replaces the role's; a caller's deadline (what is
        # left of the run's research time) can only shorten it.
        timeout_seconds=max(5, min(filter(None, [tuning.timeout_seconds or min(spec.timeout_seconds, agent.timeout_seconds), timeout_seconds]))),
    )
    current_run = await store.get(run["run_id"])
    output_cap = node_output_cap(settings, node)
    # Parallel writer tasks need distinct native threads; research units keep
    # their unit identity.
    unit_id = task_id or payload.get("unit", {}).get("id", skill_name)
    child_thread = native_thread_id(run, skill_name, unit_id)
    app_config, model_name = model_budget_config(
        app_config,
        role,
        output_cap,
        run=current_run,
        researcher=skill_name not in {"deepresearch", "report-synthesis"},
        settings=settings,
        concurrency=settings.max_concurrency,
        overrides=node_overrides(settings, node),
        # The native thread is the conversation a gateway may pin to one replica.
        session=child_thread,
    )
    trace = LocalTrace(store, run["run_id"], settings, trace_secrets(settings, context))
    scope = {"cycle": run.get("cycle", 0), **(metric_scope.get() or {}), "unit_id": unit_id, "agent_name": role.name, "skill": skill_name, "purpose": "agent", "config_node": node}
    metrics = {"started_at": utcnow(), "started": time.monotonic(), "status": "failed"}
    try:
        return await _execute(settings, store, run, role, payload, candidates, context, trace, scope, metrics, model_name, child_thread, app_config, output_cap)
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


async def _execute(settings, store, run, role, payload, candidates, context, trace, scope, metrics, model_name, child_thread, app_config, output_cap=None):
    from deerflow.subagents.executor import (
        SubagentExecutor,
        SubagentStatus,
        cleanup_background_task,
        get_background_task_result,
        request_cancel_background_task,
    )

    unit_id = scope["unit_id"]
    async with trace.span(role.name, "agent", {"unit_id": unit_id, "thread_id": child_thread, "task": payload, "tools": list(candidates)}) as output:
        callbacks = model_callbacks(trace, metered_tools=list(candidates.values()), model_name=model_name, scope=scope, output_cap=output_cap)
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
            output.update(execution_id=execution_id, result=result.result, stop_reason=result.stop_reason)
            # Capture host envelopes only after the native model/tool loop has
            # finished. Redaction/bounding affects archival copies, never the
            # ToolMessage delivered to the researcher.
            messages = []
            for message in getattr(result, "ai_messages", None) or []:
                # Keep only message-envelope fields needed for research
                # provenance. Provider response metadata may contain headers.
                safe = {key: message[key] for key in ("type", "id", "name", "tool_call_id", "status", "content", "tool_calls", "additional_kwargs", "artifact") if key in message}
                if "content" in safe:
                    safe["content"] = redact_content(safe["content"], trace.secrets, 20000)
                if "additional_kwargs" in safe:
                    safe["additional_kwargs"] = redact(safe["additional_kwargs"], trace.secrets, 20000)
                if "artifact" in safe:
                    # Source artifacts carry the records evidence is derived from
                    # (up to 30 records of 4,000 characters). Past the bound they
                    # would turn into a preview object and lose every record.
                    artifact = safe["artifact"]
                    if isinstance(artifact, dict):
                        artifact = {key: value for key, value in artifact.items() if key != "raw_preview"}
                    safe["artifact"] = redact(artifact, trace.secrets, 400000)
                if "tool_calls" in safe:
                    safe["tool_calls"] = [{**call, "args": redact(call.get("args"), trace.secrets, 20000)} for call in safe["tool_calls"]]
                messages.append(safe)
            # Trace display limits must not change research semantics. Preserve
            # the complete visible answer while removing known credentials.
            answer = result.result or ""
            archived_answer = redact(answer, trace.secrets, max(20000, len(answer) * 8 + 256))
            if not isinstance(archived_answer, str):
                archived_answer = json.dumps(archived_answer, ensure_ascii=False)
            receipts = result.snapshot_tool_receipts() or derived_receipts(messages)
            output["tool_receipts"] = receipts
            metrics.update(status="completed", stop_reason=result.stop_reason, receipts=len(receipts))
            return NativeExecution(answer=archived_answer, execution_id=execution_id, messages=messages, receipts=receipts, stop_reason=result.stop_reason)
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
