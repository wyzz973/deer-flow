"""Explicit LangGraph control flow. Side effects before interrupts are avoided."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from . import activity
from . import report as documents
from .contracts import FollowupResult, ResearchError, ResearchPlan, ResearchResult, ResearchUnit, utcnow
from .evidence import digest, merge_results
from .report_policy import eligible_evidence
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


class ResearchState(TypedDict, total=False):
    operation_id: str
    run: dict
    input_mode: str
    followup_action: str
    plan: dict
    decision: dict
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


def build_workflow(settings, store, runner, checkpointer):
    async def stage(s, status, event=None, data=None):
        await store.patch(s["run"]["run_id"], status=status)
        if event:
            await store.event(s["run"]["run_id"], event, data)

    async def planner(s):
        run = await store.get(s["run"]["run_id"])
        await stage(s, "PLANNING")
        decision = s.get("decision", {})
        proposed = decision.get("plan")
        if decision.get("revision"):
            proposed = {"plan": proposed, "revision": decision["revision"]}
        version = s.get("plan", {}).get("plan_version", 0) + 1
        key = "plan:" + digest([version, proposed, run.get("conversation", [])])
        cached = await store.cached(run["run_id"], key)
        if cached:
            await store.event(run["run_id"], "metrics.cache_hit", {"kind": "plan", "plan_version": version})
        plan = ResearchPlan.model_validate(cached) if cached else await runner.plan(run, proposed)
        plan.plan_version = version
        if not decision.get("revision"):
            plan.acknowledgement = ""
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
        semaphore = asyncio.Semaphore(settings.max_concurrency)

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

        pending = [u for u in units if u.id not in results]
        while pending:
            ready = sorted([u for u in pending if set(u.depends_on).issubset(results)], key=lambda u: (u.priority, u.id))
            if not ready:
                raise ResearchError("DEPENDENCY_BLOCKED", "研究依赖无法继续", recoverable=False)
            outcomes = await asyncio.gather(*(execute(unit) for unit in ready), return_exceptions=True)
            failures = []
            for unit, outcome in zip(ready, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    failures.append((unit, outcome))
                else:
                    uid, result = outcome
                    results[uid] = result
            if failures:
                errors = [error for _, error in failures]

                def fatal_error(error):
                    # Storage failures are infrastructure, not a research step.
                    if not isinstance(error, Exception) or isinstance(error, (OSError, sqlite3.Error)):
                        return True
                    return isinstance(error, ResearchError) and error.code in FATAL_UNIT_ERRORS

                fatal = next((error for error in errors if fatal_error(error)), None)
                if fatal is not None or len(failures) == len(ready):
                    # All successful siblings are already durable; retry dispatch reuses them.
                    raise fatal or errors[0]
                cycle = run.get("cycle", 0)
                for unit, error in failures:
                    committed = await store.unit(run["run_id"], f"{cycle}:{unit.id}", digest(unit.model_dump(mode="json")))
                    if committed is not None:
                        # The research committed before a later bookkeeping error.
                        results[unit.id] = committed
                        await store.mutate(run["run_id"], lambda r, uid=unit.id: r["unit_statuses"].update({uid: "COMPLETED"}))
                        await store.event(run["run_id"], "research.unit.completed", completion(run, unit, committed), key=f"unit-done-{cycle}-{unit.id}")
                        continue
                    # Never persist a free-form exception text: it may echo credentials.
                    code = error.code if isinstance(error, ResearchError) else "EXECUTION_FAILED"
                    placeholder = ResearchResult(unit_id=unit.id, confidence=0, limitations=[f"研究步骤“{unit.title or unit.id}”未能完成（{code}），相关内容可能不完整。"]).model_dump(mode="json")
                    await store.mutate(run["run_id"], lambda r, uid=unit.id, value=code: r.setdefault("unit_failures", {}).update({uid: value}))
                    await store.save_unit(run["run_id"], f"{cycle}:{unit.id}", digest(unit.model_dump(mode="json")), placeholder)
                    await store.event(run["run_id"], "research.unit.failed", {"unit_id": unit.id, "title": unit.title, "code": code, "cycle": cycle}, key=f"unit-failed-{cycle}-{unit.id}")
                    results[unit.id] = placeholder
            pending = [u for u in pending if u.id not in results]
        return {"results": [results[u.id] for u in units], "status": "RESEARCHING"}

    async def merge(s):
        results = [ResearchResult.model_validate(r) for r in s["results"]]
        pool, findings, lineage = await asyncio.to_thread(merge_results, results, s.get("evidence_pool"))
        await store.save_pool(s["run"]["run_id"], pool, [])
        await store.patch(s["run"]["run_id"], evidence_count=len(pool))
        await store.event(s["run"]["run_id"], "evidence.pool.updated", {"count": len(pool)}, key=f"pool-{s['run'].get('cycle', 0)}-" + digest(pool))
        return {"evidence_pool": pool, "findings": findings, "lineage": lineage}

    async def validator(s):
        await stage(s, "VALIDATING")
        plan = ResearchPlan.model_validate(s["plan"])
        pool = s["evidence_pool"]
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
        exhausted = s.get("iteration", 0) >= s["run"]["budget"]["max_iterations"] or len(s["units"]) >= s["run"]["budget"]["max_units"]
        signature = digest([(g["unit_id"], g["code"]) for g in gaps])
        saturated = signature == s.get("previous_gap_signature") and len(pool) <= s.get("previous_pool_size", -1)
        if exhausted or saturated:
            if not eligible:
                raise ResearchError("NO_EVIDENCE", "研究没有取得可引用的原文证据，无法生成报告；请检查搜索与网页读取工具后从检查点重试")
            current = await store.get(run_id)
            if not (settings.allow_limited_report or current.get("allow_limited_report", False)):
                raise ResearchError("RESEARCH_GAPS", "研究仍有缺口且达到补研停止条件；未生成伪完整报告", recoverable=False)
            limitations = list(dict.fromkeys([*known_limits, *[g["description"] for g in gaps]]))
            await store.patch(run_id, limitations=limitations)
            consent = "owner" if current.get("allow_limited_report") else "settings"
            await store.event(run_id, "report.limitations.auto", {"gaps": [g["gap_id"] for g in gaps], "consent": consent}, key=f"limited-{s['run'].get('cycle', 0)}-{signature[:24]}")
            return {"gaps": gaps, "limitations": limitations, "status": "RESEARCH_COMPLETE"}
        await stage(s, "GAP_FOUND", "validator.gap_found", {"gaps": gaps})
        return {"gaps": gaps, "previous_gap_signature": signature, "previous_pool_size": len(pool), "status": "GAP_FOUND"}

    async def supplement(s):
        iteration = s.get("iteration", 0) + 1
        remaining = s["run"]["budget"]["max_units"] - len(s["units"])
        extra = supplemental_units(ResearchPlan.model_validate(s["plan"]), s["gaps"], iteration, remaining)
        if not extra:
            raise ResearchError("UNIT_BUDGET", "没有可用的补研单元预算", recoverable=False)
        deferred = sorted({g["unit_id"] for g in s["gaps"]} - {unit["depends_on"][0] for unit in extra})
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
        demo = settings.runner == "demo"
        document = draft["document"]
        citations = documents.citations(mapping, pool)
        timeline = activity.build(current, await store.activity_events(run_id), await store.calls(run_id), activity.tool_roles(settings))
        body = {
            "format": "markdown-v2",
            "title": draft["title"],
            "report": {"title": draft["title"]},
            "document": document,
            "display_markdown": documents.display_markdown(document, mapping),
            "markdown": documents.export_markdown(document, mapping, pool, lang, demo),
            "html": documents.html_document(document, mapping, pool, lang, demo),
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
            trace = LocalTrace(store, state["run"]["run_id"], settings, (context.get("secrets") or {}).values())
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
    graph.add_conditional_edges(START, lambda state: "follow_up" if state.get("input_mode") == "follow_up" else "planner")
    graph.add_conditional_edges("follow_up", lambda state: state["followup_action"], {"answer": END, "revise": "synthesis", "research": "planner"})
    graph.add_edge("planner", "plan_review")
    graph.add_conditional_edges("plan_review", lambda s: s["decision"]["action"], {"edit": "planner", "approve": "dispatch", "reject": "rejected"})
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
