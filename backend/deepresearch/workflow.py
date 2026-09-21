"""Explicit LangGraph control flow. Side effects before interrupts are avoided."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import activity
from . import report as documents
from .config import active_settings
from .contracts import FollowupResult, ResearchError, ResearchPlan, ResearchRequest, ResearchResult, ResearchUnit, utcnow
from .evidence import digest, merge_results, valid_result
from .report_policy import eligible_evidence, source_roles
from .secrets import trace_secrets
from .store import research_spent, research_time_spent
from .trace import LocalTrace, metric_scope
from .validators import research_gaps, supplemental_units

# Failures that make more research pointless or unsafe fail the run. Anything
# else (a timeout, a malformed answer, one provider hiccup) becomes an explicit
# missing step that gap review and the report's limitations handle.
FATAL_UNIT_ERRORS = {
    "MODEL_AUTH_REQUIRED",
    "MODEL_ACCESS_DENIED",
    "MODEL_BILLING_REQUIRED",
    "MODEL_NOT_CONFIGURED",
    "BUDGET_EXHAUSTED",
    "TIME_BUDGET",
    "RUN_CANCELLED",
    "AGENT_NOT_CONFIGURED",
    "SKILL_DENIED",
    "TOOL_DENIED",
    "NATIVE_TOOL_MISSING",
    "MCP_TOOL_MISSING",
    "DEPENDENCY_EVIDENCE",
    "UNIT_MISMATCH",
}


# A step that could not start because research has no tokens or no time left.
# The run is winding down, not broken: what was gathered still becomes a report.
WIND_DOWN_ERRORS = {"RESEARCH_BUDGET_SPENT", "RESEARCH_TIME_SPENT"}


class ResearchState(TypedDict, total=False):
    operation_id: str
    run: dict
    input_mode: str
    followup_action: str
    plan: dict
    decision: dict
    # The conversation rewritten into one complete research request.
    request: dict
    auto_start: bool
    units: list[dict]
    results: list[dict]
    evidence_pool: dict
    findings: list[dict]
    lineage: dict
    gaps: list[dict]
    iteration: int
    previous_gap_signature: str
    previous_pool_size: int
    limitations: list[str]
    report_draft: dict
    citation_map: dict
    final_errors: list[str]
    synthesis_repairs: int
    rendered_report: str
    status: str


def accepts(function, name):
    """Custom runners (``runner_factory``) may implement the earlier protocol."""
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(item.name == name or item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters)


def build_workflow(settings, store, runner, checkpointer):
    def run_settings():
        # The executing run's configuration snapshot, bound by the service.
        return active_settings(settings)

    async def stage(s, status, event=None, data=None):
        await store.patch(s["run"]["run_id"], status=status)
        if event:
            await store.event(s["run"]["run_id"], event, data)

    async def rewrite(s):
        """Rewrite the conversation into one complete research request.

        ChatGPT's conversation model does this before a research session and
        again, merging the change into the previous request, whenever the user
        revises the plan. A structured plan edit keeps the current request.
        """
        run = await store.get(s["run"]["run_id"])
        await stage(s, "PLANNING")
        decision = s.get("decision", {})
        previous = (s.get("request") or {}).get("user_query") or (s.get("plan") or {}).get("brief") or None
        if (decision.get("plan") is not None and not decision.get("revision") and s.get("request")) or not hasattr(runner, "rewrite"):
            return {"request": s.get("request") or {}}
        users = [m for m in run.get("conversation", []) if m["role"] == "user"]
        message = decision.get("revision") or (users[-1]["text"] if users else run["query"])
        fresh = previous is None or (not decision.get("revision") and len(users) <= 1)
        version = s.get("plan", {}).get("plan_version", 0) + 1
        key = "rewrite:" + digest([run.get("cycle", 0), version, message, None if fresh else previous, run.get("conversation", [])])
        cached = await store.cached(run["run_id"], key)
        if cached:
            await store.event(run["run_id"], "metrics.cache_hit", {"kind": "rewrite", "plan_version": version})
        request = ResearchRequest.model_validate(cached) if cached else await runner.rewrite(run, message, None if fresh else previous)
        body = request.model_dump(mode="json")
        await store.cache(run["run_id"], key, body)
        await store.patch(run["run_id"], request=body)
        await store.append_message(
            run["run_id"],
            {
                "id": f"request-{version}",
                "role": "assistant",
                "kind": "rewrite",
                "text": body["user_query"],
                "rewrite": {"user_query": body["user_query"], "revision": not fresh, "clarification_questions": body["clarification_questions"]},
                "cycle": run.get("cycle", 0),
                "at": utcnow(),
            },
        )
        await store.event(run["run_id"], "research.request.rewritten", {"plan_version": version, "revision": not fresh, "characters": len(body["user_query"])}, key=f"rewrite-{run.get('cycle', 0)}-{version}")
        return {"request": body}

    async def planner(s):
        run = await store.get(s["run"]["run_id"])
        await stage(s, "PLANNING")
        settings = run_settings()
        decision = s.get("decision", {})
        request = s.get("request") or {}
        proposed = decision.get("plan")
        if decision.get("revision"):
            proposed = {"plan": proposed, "revision": decision["revision"]}
        version = s.get("plan", {}).get("plan_version", 0) + 1
        key = "plan:" + digest([version, proposed, run.get("conversation", []), request])
        cached = await store.cached(run["run_id"], key)
        if cached:
            await store.event(run["run_id"], "metrics.cache_hit", {"kind": "plan", "plan_version": version})
        if cached:
            plan = ResearchPlan.model_validate(cached)
        else:
            plan = await (runner.plan(run, proposed, request=request) if accepts(runner.plan, "request") else runner.plan(run, proposed))
        plan.plan_version = version
        # The rewritten request is the shared research brief; the conversation
        # model, not the planner, confirms a revision.
        if request.get("user_query"):
            plan.brief = request["user_query"]
        plan.acknowledgement = (request.get("acknowledgement") or plan.acknowledgement) if decision.get("revision") else ""
        if request.get("clarification_questions") and not plan.clarification_questions:
            plan.clarification_questions = request["clarification_questions"][:3]
        settings.fit_origins(plan)
        settings.check_plan(plan, type("Budget", (), run["budget"])(), run["source_names"])
        body = plan.model_dump(mode="json")
        await store.cache(run["run_id"], key, body)
        # A revision that starts immediately never reaches the review interrupt,
        # so the run snapshot must take the revised units here.
        await store.patch(run["run_id"], plan=body, units=body["research_units"])
        questions = body.get("clarification_questions", [])
        if body["acknowledgement"]:
            await store.append_message(run["run_id"], {"id": f"ack-{version}", "role": "assistant", "kind": "text", "text": body["acknowledgement"], "cycle": run.get("cycle", 0), "at": utcnow()})
        await store.append_message(
            run["run_id"],
            {
                "id": f"plan-{version}",
                "role": "assistant",
                "kind": "clarification" if questions else "plan",
                "text": "\n\n".join(questions) if questions else body["title"] or body["goal"],
                "plan": body,
                "cycle": run.get("cycle", 0),
                "at": utcnow(),
            },
        )
        await store.event(run["run_id"], "plan.updated" if version > 1 else "plan.created", {"plan_version": version}, key=f"plan-{version}")
        # A conversational revision is the owner's approval: the revised plan
        # starts immediately, like ChatGPT. Clarification still waits.
        return {"run": run, "plan": body, "units": body["research_units"], "decision": {}, "auto_start": bool(decision.get("start")) and not questions, "status": "PLANNING"}

    async def review(s):
        if s.get("auto_start"):
            version = s["plan"]["plan_version"]
            await store.event(s["run"]["run_id"], "plan.auto_started", {"plan_version": version, "source": "revision"}, key=f"auto-revision-{version}")
            return {"decision": {"action": "approve"}, "auto_start": False, "operation_id": s.get("operation_id", "")}
        # The node restarts on resume; everything before interrupt is pure.
        decision = interrupt({"type": "plan_review", "plan": s["plan"]})
        if not isinstance(decision, dict) or decision.get("action") not in {"approve", "edit", "reject"}:
            raise ResearchError("PLAN_DECISION", "无效的计划确认操作")
        return {"decision": decision, "operation_id": decision.get("operation_id", s.get("operation_id", ""))}

    async def rejected(s):
        await stage(s, "CANCELLED", "run.cancelled")
        return {"status": "CANCELLED"}

    def completion(run, unit, result):
        return {"unit_id": unit.id, "skill_name": unit.skill, "cycle": run.get("cycle", 0), "title": unit.title, "summary": (result.get("summary") or "")[:400], "findings": len(result.get("findings", []))}

    async def dispatch(s):
        await stage(s, "RESEARCHING")
        run, units = s["run"], [ResearchUnit.model_validate(u) for u in s["units"]]
        results = {}
        failed_before = (await store.get(run["run_id"])).get("unit_failures", {})
        for unit in units:
            cached = await store.unit(run["run_id"], f"{run.get('cycle', 0)}:{unit.id}", digest(unit.model_dump(mode="json")))
            if cached is not None:
                results[unit.id] = cached
                await store.event(run["run_id"], "metrics.cache_hit", {"kind": "unit-result", "unit_id": unit.id})
                # The result can commit just before a crash in status/event
                # publication. Reconcile the projection without rerunning tools.
                state = "FAILED" if unit.id in failed_before else "COMPLETED"
                await store.mutate(run["run_id"], lambda r, uid=unit.id, value=state: r["unit_statuses"].update({uid: value}))
                if state == "COMPLETED":
                    await store.event(run["run_id"], "research.unit.completed", completion(run, unit, cached), key=f"unit-done-{run.get('cycle', 0)}-{unit.id}")
        semaphore = asyncio.Semaphore(run_settings().max_concurrency)

        async def execute(unit):
            queued = time.monotonic()
            async with semaphore:
                queued_ms = round((time.monotonic() - queued) * 1000)
                await store.event(run["run_id"], "research.unit.started", {"unit_id": unit.id, "skill_name": unit.skill, "title": unit.title, "queued_ms": queued_ms})
                await store.mutate(run["run_id"], lambda r: r["unit_statuses"].update({unit.id: "RESEARCHING"}))
                try:
                    deps = {uid: results[uid]["findings"] for uid in unit.depends_on}
                    result = await runner.research(run, unit, deps)
                    if result.unit_id != unit.id:
                        raise ResearchError("UNIT_MISMATCH", "ResearchResult 的单元 ID 不匹配")
                    body = result.model_dump(mode="json")
                    await store.save_unit(run["run_id"], f"{run.get('cycle', 0)}:{unit.id}", digest(unit.model_dump(mode="json")), body)
                    await store.mutate(run["run_id"], lambda r: r["unit_statuses"].update({unit.id: "COMPLETED"}))
                    await store.event(run["run_id"], "research.unit.completed", completion(run, unit, body), key=f"unit-done-{run.get('cycle', 0)}-{unit.id}")
                    return unit.id, body
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await store.mutate(run["run_id"], lambda r: r["unit_statuses"].update({unit.id: "FAILED"}))
                    raise

        def fatal_error(error):
            # Storage failures are infrastructure, not a research step.
            if not isinstance(error, Exception) or isinstance(error, (OSError, sqlite3.Error)):
                return True
            return isinstance(error, ResearchError) and error.code in FATAL_UNIT_ERRORS

        async def disclose(unit, error):
            """A failed step becomes a stated gap, so steps that build on it and the report can go on."""
            cycle = run.get("cycle", 0)
            committed = await store.unit(run["run_id"], f"{cycle}:{unit.id}", digest(unit.model_dump(mode="json")))
            if committed is not None:
                # The research committed before a later bookkeeping error.
                results[unit.id] = committed
                await store.mutate(run["run_id"], lambda r, uid=unit.id: r["unit_statuses"].update({uid: "COMPLETED"}))
                await store.event(run["run_id"], "research.unit.completed", completion(run, unit, committed), key=f"unit-done-{cycle}-{unit.id}")
                return
            # Never persist a free-form exception text: it may echo credentials.
            code = error.code if isinstance(error, ResearchError) else "EXECUTION_FAILED"
            placeholder = ResearchResult(unit_id=unit.id, confidence=0, limitations=[f"研究步骤“{unit.title or unit.id}”未能完成（{code}），相关内容可能不完整。"]).model_dump(mode="json")
            await store.mutate(run["run_id"], lambda r, uid=unit.id, value=code: r.setdefault("unit_failures", {}).update({uid: value}))
            await store.save_unit(run["run_id"], f"{cycle}:{unit.id}", digest(unit.model_dump(mode="json")), placeholder)
            await store.event(run["run_id"], "research.unit.failed", {"unit_id": unit.id, "title": unit.title, "code": code, "cycle": cycle}, key=f"unit-failed-{cycle}-{unit.id}")
            results[unit.id] = placeholder

        # A step starts as soon as what it needs is done and a slot is free. Steps
        # used to run in waves (everything ready now, then everything that became
        # ready): with three slots and five steps, two of them depending on steps
        # that had finished, both slots idled until the slowest sibling ended.
        pending = [u for u in units if u.id not in results]
        running, undecided, succeeded, fatal = {}, [], False, None
        try:
            while pending or running or undecided or fatal is not None:
                if fatal is None:
                    ready = sorted([u for u in pending if set(u.depends_on).issubset(results)], key=lambda u: (u.priority, u.id))
                    for unit in ready:
                        running[asyncio.create_task(execute(unit))] = unit
                    pending = [u for u in pending if u not in ready]
                if not running:
                    if fatal is not None:
                        # All successful siblings are already durable; retry dispatch reuses them.
                        raise fatal
                    if undecided:
                        # Everything that could run has failed and nothing was learned. Losing
                        # every step to a spent research budget is a wind-down, not a broken
                        # run: earlier evidence still becomes a report. Anything else fails
                        # the run, and a later retry runs the steps again.
                        if not all(isinstance(error, ResearchError) and error.code in WIND_DOWN_ERRORS for _, error in undecided):
                            raise undecided[0][1]
                        for unit, error in undecided:
                            await disclose(unit, error)
                        undecided = []
                        continue
                    raise ResearchError("DEPENDENCY_BLOCKED", "研究依赖无法继续", recoverable=False)
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    unit = running.pop(task)
                    error = asyncio.CancelledError() if task.cancelled() else task.exception()
                    if error is None:
                        uid, result = task.result()
                        results[uid] = result
                        succeeded = True
                    elif fatal_error(error):
                        # Nothing new starts; steps under way finish and stay durable.
                        fatal = fatal or error
                    else:
                        undecided.append((unit, error))
                # One failed step among working ones is a disclosed gap. While every
                # step so far has failed the decision waits: steps that build on a
                # failed one stay blocked until it is known whether the run goes on.
                if fatal is None and undecided and (succeeded or any(result.get("findings") for result in results.values())):
                    for unit, error in undecided:
                        await disclose(unit, error)
                    undecided = []
        finally:
            # Leaving early (cancelled, stopped, a storage error) must not leave steps
            # running behind the workflow's back; wait until they have really ended.
            for task in running:
                task.cancel()
            if running:
                await asyncio.gather(*running, return_exceptions=True)
        return {"results": [results[u.id] for u in units], "status": "RESEARCHING"}

    async def merge(s):
        results = []
        for body in s["results"]:
            result, dropped = valid_result(body)
            results.append(result)
            if dropped:
                # A record the contract cannot keep (an unusable locator, empty
                # text, a field a later rule bounds) must not discard a whole
                # completed research step.
                await store.event(
                    s["run"]["run_id"],
                    "research.evidence.dropped",
                    {"unit_id": result.unit_id, "count": len(dropped), "records": dropped[:20], "cycle": s["run"].get("cycle", 0)},
                    key=f"evidence-dropped-{s['run'].get('cycle', 0)}-{result.unit_id}",
                )
        pool, findings, lineage = await asyncio.to_thread(merge_results, results, s.get("evidence_pool"))
        await store.save_pool(s["run"]["run_id"], pool, [])
        await store.patch(s["run"]["run_id"], evidence_count=len(pool))
        await store.event(s["run"]["run_id"], "evidence.pool.updated", {"count": len(pool)}, key=f"pool-{s['run'].get('cycle', 0)}-" + digest(pool))
        return {"evidence_pool": pool, "findings": findings, "lineage": lineage}

    async def validator(s):
        await stage(s, "VALIDATING")
        plan = ResearchPlan.model_validate(s["plan"])
        pool = s["evidence_pool"]
        settings = run_settings()
        eligible = eligible_evidence(pool, plan.source_policy, settings)
        gaps = research_gaps(plan, s["units"], s["findings"], pool, s["results"], eligible)
        run_id = s["run"]["run_id"]
        await store.save_pool(run_id, pool, gaps)
        await store.patch(run_id, gaps=gaps)
        known_limits = list(dict.fromkeys(limit for result in s["results"] for limit in result.get("limitations", [])))
        if not gaps:
            await store.patch(run_id, limitations=known_limits)
            await stage(s, "RESEARCH_COMPLETE", "validator.passed")
            return {"gaps": [], "limitations": known_limits, "status": "RESEARCH_COMPLETE"}
        current_run = await store.get(run_id) or s["run"]
        # Only the gap kinds the deployment wants to chase start another round
        # (supplement_gap_codes); the others go straight into the limitations.
        actionable = [gap for gap in gaps if gap["code"] in settings.supplement_gap_codes]
        exhausted = (
            not actionable
            or s.get("iteration", 0) >= s["run"]["budget"]["max_iterations"]
            or len(s["units"]) >= s["run"]["budget"]["max_units"]
            # Supplementing on a spent research budget only repeats the failure.
            or research_spent(current_run)
            # Time is a budget too: keep what is left for writing the report.
            or research_time_spent(current_run, settings)
        )
        signature = digest([(g["unit_id"], g["code"]) for g in gaps])
        # What the report can actually stand on: citable evidence that a finding
        # refers to. The pool also holds discovery links and uncited records; it
        # keeps growing while research goes nowhere, and a pool with citable
        # evidence that no finding uses cannot be written up either (that case
        # used to supplement to the limit, write the report twice and then fail
        # final validation).
        cited = {eid for finding in s["findings"] for eid in finding["evidence_ids"] if eid in eligible}
        saturated = signature == s.get("previous_gap_signature") and len(cited) <= s.get("previous_pool_size", -1)
        if exhausted or saturated:
            if not cited:
                if research_spent(current_run) and not (current_run.get("usage") or {}).get("tool_calls"):
                    # A budget smaller than a researcher's first request makes the
                    # engine remove every tool call: nothing was searched, and the
                    # tools are not at fault. A retry keeps the budget and the spent
                    # balance, so it would only fail again.
                    raise ResearchError("NO_EVIDENCE", "研究步骤还没有开始检索就用完了分到的 Token 预算（工具调用 0 次），与检索工具无关；重试不会改变预算，请调大 max_model_tokens（或不限）后新建研究", recoverable=False)
                raise ResearchError("NO_EVIDENCE", "研究没有取得任何有可引用证据支持的发现，无法生成报告；请检查检索/读取工具与模型输出后从检查点重试")
            current = await store.get(run_id)
            if not (settings.allow_limited_report or current.get("allow_limited_report", False)):
                raise ResearchError("RESEARCH_GAPS", "研究仍有缺口且达到补研停止条件；未生成伪完整报告", recoverable=False)
            limitations = list(dict.fromkeys([*known_limits, *[g["description"] for g in gaps]]))
            await store.patch(run_id, limitations=limitations)
            consent = "owner" if current.get("allow_limited_report") else "settings"
            await store.event(run_id, "report.limitations.auto", {"gaps": [g["gap_id"] for g in gaps], "consent": consent}, key=f"limited-{s['run'].get('cycle', 0)}-{signature[:24]}")
            return {"gaps": gaps, "limitations": limitations, "status": "RESEARCH_COMPLETE"}
        await stage(s, "GAP_FOUND", "validator.gap_found", {"gaps": gaps})
        return {"gaps": gaps, "previous_gap_signature": signature, "previous_pool_size": len(cited), "status": "GAP_FOUND"}

    async def supplement(s):
        iteration = s.get("iteration", 0) + 1
        remaining = s["run"]["budget"]["max_units"] - len(s["units"])
        chased = [gap for gap in s["gaps"] if gap["code"] in run_settings().supplement_gap_codes]
        extra = supplemental_units(ResearchPlan.model_validate(s["plan"]), chased, iteration, remaining)
        if not extra:
            raise ResearchError("UNIT_BUDGET", "没有可用的补研单元预算", recoverable=False)
        deferred = sorted({g["unit_id"] for g in chased} - {unit["depends_on"][0] for unit in extra})
        if deferred:
            # Remaining gaps surface again at validation and become stated
            # limitations; the truncation itself is never silent.
            await store.event(s["run"]["run_id"], "research.supplement.deferred", {"iteration": iteration, "unit_ids": deferred}, key=f"deferred-{s['run'].get('cycle', 0)}-{iteration}")
        await store.patch(s["run"]["run_id"], iteration=iteration, units=s["units"] + extra)
        return {"units": s["units"] + extra, "iteration": iteration}

    async def synthesis(s):
        await stage(s, "SYNTHESIZING", "report.synthesizing")
        run_id = s["run"]["run_id"]
        current = await store.get(run_id)
        plan = ResearchPlan.model_validate(s["plan"])
        revise = s.get("input_mode") == "follow_up" and s.get("followup_action") == "revise"
        latest = next((m["text"] for m in reversed(current.get("conversation", [])) if m["role"] == "user"), "") if revise else None
        key = "synthesis:" + digest([current.get("conversation", []), s["plan"], s["findings"], s.get("final_errors", []), s.get("synthesis_repairs", 0), current.get("limitations", []), revise])
        key += f":retry-{current.get('report_retry_generation', 0)}"
        draft = await store.cached(run_id, key)
        if draft is not None:
            await store.event(run_id, "metrics.cache_hit", {"kind": "synthesis"})
        if draft is None:
            previous = current.get("report") or {}
            if revise and previous.get("document"):
                # A conversational edit changes the existing document; it does
                # not restart writing from research notes.
                draft = await runner.revise_report(current, plan, previous, latest, s["findings"], s["evidence_pool"])
            else:
                draft = await runner.write_report(current, plan, s.get("results", []), s["findings"], s["evidence_pool"], current.get("limitations", []), s.get("final_errors", []), instruction=latest)
            await store.cache(run_id, key, draft)
        return {"report_draft": draft}

    async def binder(s):
        await stage(s, "CITATION_BINDING")
        try:
            mapping = documents.bind(s["report_draft"]["document"], s["evidence_pool"])
        except ValueError:
            mapping = {}  # Final validator routes a bounded rewrite.
        await store.event(s["run"]["run_id"], "citation.bound", {"count": len(mapping)})
        return {"citation_map": mapping}

    async def final_validate(s):
        await stage(s, "FINAL_VALIDATING")
        plan = ResearchPlan.model_validate(s["plan"])
        settings = run_settings()
        eligible = eligible_evidence(s["evidence_pool"], plan.source_policy, settings)
        errors = documents.validate(s["report_draft"]["document"], s["evidence_pool"], eligible)
        repairs = s.get("synthesis_repairs", 0)
        if errors and repairs >= settings.max_synthesis_repairs:
            raise ResearchError("FINAL_VALIDATION", "报告未通过引用/结构校验，已停止输出；可修正后从检查点重试", recoverable=True)
        return {"final_errors": errors, "synthesis_repairs": repairs + (1 if errors else 0)}

    async def render(s):
        await stage(s, "RENDERING")
        run_id = s["run"]["run_id"]
        draft, pool, mapping = s["report_draft"], s["evidence_pool"], s["citation_map"]
        current = await store.get(run_id)
        lang = documents.language(current.get("query", ""))
        settings = run_settings()
        demo = settings.runner == "demo"
        document = draft["document"]
        roles = source_roles(settings)
        citations = documents.citations(mapping, pool, roles)
        timeline = activity.build(current, await store.activity_events(run_id), await store.calls(run_id), activity.tool_roles(settings))
        body = {
            "format": "markdown-v2",
            "title": draft["title"],
            "report": {"title": draft["title"]},
            "document": document,
            "display_markdown": documents.display_markdown(document, mapping),
            "markdown": documents.export_markdown(document, mapping, pool, lang, demo, roles),
            "html": documents.html_document(document, mapping, pool, lang, demo, roles),
            "toc": documents.toc(document),
            "citations": citations,
            "citation_map": mapping,
            "limitations": draft.get("limitations", []),
            "assumptions": draft.get("assumptions", []),
            # Complete raw records stay auditable without crowding the report.
            "audit": {"limitations": s.get("limitations", []), "gaps": [gap["gap_id"] for gap in s.get("gaps", [])], **draft.get("audit", {})},
            "stats": {"elapsed_seconds": timeline["elapsed_seconds"], "searches": timeline["counts"]["searches"], "pages_read": timeline["counts"]["pages_read"], "citations": len(citations)},
            "demo": demo,
        }
        latest_user = next((m["id"] for m in reversed(current.get("conversation", [])) if m["role"] == "user"), "initial")
        # Stats are observational; the fence depends only on published content.
        publication_key = digest([current.get("cycle", 0), latest_user, {k: v for k, v in body.items() if k != "stats"}])
        await store.publish_report(run_id, publication_key, body)
        return {"rendered_report": body["markdown"], "status": "COMPLETED"}

    async def follow_up(s):
        run = await store.get(s["run"]["run_id"])
        await stage(s, "RESPONDING")
        user = next(m for m in reversed(run["conversation"]) if m["role"] == "user")
        reply_key = "followup:" + user["id"]
        cached = await store.cached(run["run_id"], reply_key)
        reply = FollowupResult.model_validate(cached) if cached else await runner.respond(run, user["text"])
        await store.cache(run["run_id"], reply_key, reply.model_dump(mode="json"))
        if reply.action == "answer":
            await store.append_message(run["run_id"], {"id": "answer-" + user["id"], "role": "assistant", "kind": "text", "text": reply.text, "at": utcnow()})
            await store.patch(run["run_id"], status="COMPLETED")
            await store.event(run["run_id"], "conversation.answered", {"message_id": user["id"]})
            return {"followup_action": "answer", "status": "COMPLETED"}
        if reply.action == "revise":
            return {"run": run, "followup_action": "revise", "final_errors": [], "synthesis_repairs": 0}

        def begin_cycle(current):
            if current.get("research_cycle_message_id") != user["id"]:
                current.update(cycle=current.get("cycle", 0) + 1, research_cycle_message_id=user["id"], units=[], unit_statuses={}, unit_failures={}, iteration=0, gaps=[], evidence_count=0, report=None, limitations=[], steering=[])

        run = await store.mutate(run["run_id"], begin_cycle)
        await store.event(run["run_id"], "research.cycle.started", {"cycle": run["cycle"]}, key=f"cycle-{run['cycle']}")
        return {
            "run": run,
            "followup_action": "research",
            "input_mode": "plan",
            "decision": {},
            "auto_start": False,
            "units": [],
            "results": [],
            "evidence_pool": {},
            "findings": [],
            "lineage": {},
            "gaps": [],
            "iteration": 0,
            "previous_gap_signature": "",
            "previous_pool_size": -1,
            "limitations": [],
            "report_draft": {},
            "citation_map": {},
            "final_errors": [],
            "synthesis_repairs": 0,
        }

    def traced(name, node):
        async def invoke(state):
            from langgraph.runtime import get_runtime

            context = get_runtime().context or {}
            trace = LocalTrace(store, state["run"]["run_id"], run_settings(), trace_secrets(run_settings(), context))
            # Model, tool and agent metrics inherit the phase they ran in.
            token = metric_scope.set({"phase": name, "cycle": state["run"].get("cycle", 0)})
            try:
                async with trace.span(name, "node", {k: v for k, v in state.items() if k != "run"}) as output:
                    result = await node(state)
                    output.update(result)
                    return result
            finally:
                metric_scope.reset(token)

        return invoke

    graph = StateGraph(ResearchState, context_schema=dict)
    for name, node in [
        ("rewrite", rewrite),
        ("planner", planner),
        ("follow_up", follow_up),
        ("plan_review", review),
        ("rejected", rejected),
        ("dispatch", dispatch),
        ("evidence_merge", merge),
        ("validator", validator),
        ("supplement", supplement),
        ("synthesis", synthesis),
        ("citation_binder", binder),
        ("final_validator", final_validate),
        ("renderer", render),
    ]:
        graph.add_node(name, traced(name, node))
    graph.add_conditional_edges(START, lambda state: "follow_up" if state.get("input_mode") == "follow_up" else "rewrite")
    graph.add_conditional_edges("follow_up", lambda state: state["followup_action"], {"answer": END, "revise": "synthesis", "research": "rewrite"})
    graph.add_edge("rewrite", "planner")
    graph.add_edge("planner", "plan_review")
    graph.add_conditional_edges("plan_review", lambda s: s["decision"]["action"], {"edit": "rewrite", "approve": "dispatch", "reject": "rejected"})
    graph.add_edge("rejected", END)
    graph.add_edge("dispatch", "evidence_merge")
    graph.add_edge("evidence_merge", "validator")
    graph.add_conditional_edges("validator", lambda s: "supplement" if s["status"] == "GAP_FOUND" else "synthesis")
    graph.add_edge("supplement", "dispatch")
    graph.add_edge("synthesis", "citation_binder")
    graph.add_edge("citation_binder", "final_validator")
    graph.add_conditional_edges("final_validator", lambda s: "synthesis" if s["final_errors"] else "renderer")
    graph.add_edge("renderer", END)
    return graph.compile(checkpointer=checkpointer)
