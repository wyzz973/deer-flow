"""Explicit LangGraph control flow. Side effects before interrupts are avoided."""

from __future__ import annotations

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .contracts import FollowupResult, ResearchError, ResearchPlan, ResearchResult, ResearchUnit, StructuredReport, utcnow
from .evidence import digest, merge_results
from .render import bind_citations, citation_metadata, html_report, markdown
from .trace import LocalTrace
from .validators import research_gaps, supplemental_units, validate_report


class ResearchState(TypedDict, total=False):
    operation_id: str
    run: dict
    input_mode: str
    followup_action: str
    plan: dict
    decision: dict
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
    structured_report: dict
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
        proposed = s.get("decision", {}).get("plan")
        if s.get("decision", {}).get("revision"):
            proposed = {"plan": proposed, "revision": s["decision"]["revision"]}
        version = s.get("plan", {}).get("plan_version", 0) + 1
        key = "plan:" + digest([version, proposed, run.get("conversation", [])])
        cached = await store.cached(run["run_id"], key)
        plan = ResearchPlan.model_validate(cached) if cached else await runner.plan(run, proposed)
        plan.plan_version = version
        settings.check_plan(plan, type("Budget", (), run["budget"])(), run["source_names"])
        body = plan.model_dump(mode="json")
        await store.cache(run["run_id"], key, body)
        await store.patch(run["run_id"], plan=body)
        questions = body.get("clarification_questions", [])
        await store.append_message(
            run["run_id"],
            {"id": f"plan-{version}", "role": "assistant", "kind": "clarification" if questions else "plan", "text": "\n\n".join(questions) if questions else body["goal"], "plan": body, "cycle": run.get("cycle", 0), "at": utcnow()},
        )
        await store.event(run["run_id"], "plan.updated" if version > 1 else "plan.created", {"plan_version": version}, key=f"plan-{version}")
        return {"run": run, "plan": body, "units": body["research_units"], "decision": {}, "status": "PLANNING"}

    async def review(s):
        # The node restarts on resume; everything before interrupt is pure.
        decision = interrupt({"type": "plan_review", "plan": s["plan"]})
        if not isinstance(decision, dict) or decision.get("action") not in {"approve", "edit", "reject"}:
            raise ResearchError("PLAN_DECISION", "无效的计划确认操作")
        return {"decision": decision, "operation_id": decision.get("operation_id", s.get("operation_id", ""))}

    async def rejected(s):
        await stage(s, "CANCELLED", "run.cancelled")
        return {"status": "CANCELLED"}

    async def dispatch(s):
        await stage(s, "RESEARCHING")
        run, units = s["run"], [ResearchUnit.model_validate(u) for u in s["units"]]
        results = {}
        for unit in units:
            cached = await store.unit(run["run_id"], f"{run.get('cycle', 0)}:{unit.id}", digest(unit.model_dump(mode="json")))
            if cached is not None:
                results[unit.id] = cached
                # The result can commit just before a crash in status/event
                # publication. Reconcile the projection without rerunning tools.
                await store.mutate(run["run_id"], lambda r, uid=unit.id: r["unit_statuses"].update({uid: "COMPLETED"}))
                await store.event(run["run_id"], "research.unit.completed", {"unit_id": unit.id, "skill_name": unit.skill, "cycle": run.get("cycle", 0)}, key=f"unit-done-{run.get('cycle', 0)}-{unit.id}")
        semaphore = asyncio.Semaphore(settings.max_concurrency)

        async def execute(unit):
            async with semaphore:
                await store.event(run["run_id"], "research.unit.started", {"unit_id": unit.id, "skill_name": unit.skill})
                await store.mutate(run["run_id"], lambda r: r["unit_statuses"].update({unit.id: "RESEARCHING"}))
                try:
                    deps = {uid: results[uid]["findings"] for uid in unit.depends_on}
                    result = await runner.research(run, unit, deps)
                    if result.unit_id != unit.id:
                        raise ResearchError("UNIT_MISMATCH", "ResearchResult 的单元 ID 不匹配")
                    body = result.model_dump(mode="json")
                    await store.save_unit(run["run_id"], f"{run.get('cycle', 0)}:{unit.id}", digest(unit.model_dump(mode="json")), body)
                    await store.mutate(run["run_id"], lambda r: r["unit_statuses"].update({unit.id: "COMPLETED"}))
                    await store.event(run["run_id"], "research.unit.completed", {"unit_id": unit.id, "skill_name": unit.skill, "cycle": run.get("cycle", 0)}, key=f"unit-done-{run.get('cycle', 0)}-{unit.id}")
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
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    failures.append(outcome)
                else:
                    uid, result = outcome
                    results[uid] = result
            if failures:
                # All successful siblings are already durable; retry dispatch reuses them.
                raise failures[0]
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
        gaps = research_gaps(plan, s["units"], s["findings"], s["evidence_pool"], s["results"])
        run_id = s["run"]["run_id"]
        await store.save_pool(run_id, s["evidence_pool"], gaps)
        await store.patch(run_id, gaps=gaps)
        known_limits = list(dict.fromkeys(limit for result in s["results"] for limit in result.get("limitations", [])))
        if not gaps:
            await store.patch(run_id, limitations=known_limits)
            await stage(s, "RESEARCH_COMPLETE", "validator.passed")
            return {"gaps": [], "limitations": known_limits, "status": "RESEARCH_COMPLETE"}
        exhausted = s.get("iteration", 0) >= s["run"]["budget"]["max_iterations"] or len(s["units"]) >= s["run"]["budget"]["max_units"]
        signature = digest([(g["unit_id"], g["code"]) for g in gaps])
        saturated = signature == s.get("previous_gap_signature") and len(s["evidence_pool"]) <= s.get("previous_pool_size", -1)
        if exhausted or saturated:
            current = await store.get(run_id)
            if not (settings.allow_limited_report or current.get("allow_limited_report", False)):
                raise ResearchError("RESEARCH_GAPS", "研究仍有缺口且达到补研停止条件；未生成伪完整报告", recoverable=False)
            limitations = list(dict.fromkeys([*known_limits, *[g["description"] for g in gaps]]))
            await store.patch(run_id, limitations=limitations)
            return {"gaps": gaps, "limitations": limitations, "status": "RESEARCH_COMPLETE"}
        await stage(s, "GAP_FOUND", "validator.gap_found", {"gaps": gaps})
        return {"gaps": gaps, "previous_gap_signature": signature, "previous_pool_size": len(s["evidence_pool"]), "status": "GAP_FOUND"}

    async def supplement(s):
        iteration = s.get("iteration", 0) + 1
        extra = supplemental_units(ResearchPlan.model_validate(s["plan"]), s["gaps"], iteration, s["run"]["budget"]["max_units"] - len(s["units"]))
        if not extra:
            raise ResearchError("UNIT_BUDGET", "没有可用的补研单元预算", recoverable=False)
        await store.patch(s["run"]["run_id"], iteration=iteration, units=s["units"] + extra)
        return {"units": s["units"] + extra, "iteration": iteration}

    async def synthesis(s):
        await stage(s, "SYNTHESIZING", "report.synthesizing")
        current = await store.get(s["run"]["run_id"])
        key = "synthesis:" + digest([current.get("conversation", []), s["plan"], s["findings"], s.get("final_errors", []), s.get("synthesis_repairs", 0), current.get("limitations", [])])
        key += f":retry-{current.get('report_retry_generation', 0)}"
        cached = await store.cached(s["run"]["run_id"], key)
        writer_run = current
        if s.get("input_mode") == "follow_up" and s.get("followup_action") == "revise":
            # A conversational edit needs the existing AST, not just its title.
            # Do not seed a fresh research cycle with an unrelated old report.
            writer_run = {**current, "revision_report": (current.get("report") or {}).get("report")}
        report = StructuredReport.model_validate(cached) if cached else await runner.synthesize(writer_run, ResearchPlan.model_validate(s["plan"]), s["findings"], s["evidence_pool"], s.get("final_errors", []))
        await store.cache(s["run"]["run_id"], key, report.model_dump(mode="json"))
        return {"structured_report": report.model_dump(mode="json")}

    async def binder(s):
        await stage(s, "CITATION_BINDING")
        try:
            mapping = bind_citations(StructuredReport.model_validate(s["structured_report"]), s["evidence_pool"])
        except ValueError:
            mapping = {}  # Final validator routes a bounded local rewrite.
        await store.event(s["run"]["run_id"], "citation.bound", {"count": len(mapping)})
        return {"citation_map": mapping}

    async def final_validate(s):
        await stage(s, "FINAL_VALIDATING")
        errors = validate_report(StructuredReport.model_validate(s["structured_report"]), ResearchPlan.model_validate(s["plan"]), s["evidence_pool"])
        repairs = s.get("synthesis_repairs", 0)
        if errors and repairs >= settings.max_synthesis_repairs:
            raise ResearchError("FINAL_VALIDATION", "报告未通过引用/结构校验，已停止输出；可修正后从检查点重试", recoverable=True)
        return {"final_errors": errors, "synthesis_repairs": repairs + (1 if errors else 0)}

    async def render(s):
        await stage(s, "RENDERING")
        report, pool, mapping = StructuredReport.model_validate(s["structured_report"]), s["evidence_pool"], s["citation_map"]
        limited, demo = s.get("limitations", []), settings.runner == "demo"
        if any(e.get("provenance") == "tool_output" for e in pool.values()):
            limited = [*limited, "引用关联原生工具调用记录；代码未验证原文发布日期、发布方独立性或语义支持度，相关判断来自研究 Agent，重要结论需内容复核。"]
        text = markdown(report, mapping, pool, limited, demo)
        body = {
            "report": report.model_dump(mode="json"),
            "markdown": text,
            "html": html_report(report, mapping, pool, limited, demo),
            "citations": citation_metadata(mapping, pool),
            "citation_map": mapping,
            "limitations": limited,
            "demo": demo,
        }
        current = await store.get(s["run"]["run_id"])
        latest_user = next((m["id"] for m in reversed(current.get("conversation", [])) if m["role"] == "user"), "initial")
        publication_key = digest([current.get("cycle", 0), latest_user, body])
        await store.publish_report(s["run"]["run_id"], publication_key, body)
        return {"rendered_report": text, "status": "COMPLETED"}

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
                current.update(cycle=current.get("cycle", 0) + 1, research_cycle_message_id=user["id"], units=[], unit_statuses={}, iteration=0, gaps=[], evidence_count=0, report=None, limitations=[])

        run = await store.mutate(run["run_id"], begin_cycle)
        return {
            "run": run,
            "followup_action": "research",
            "input_mode": "plan",
            "decision": {},
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
            "structured_report": {},
            "citation_map": {},
            "final_errors": [],
            "synthesis_repairs": 0,
        }

    def traced(name, node):
        async def invoke(state):
            from langgraph.runtime import get_runtime

            context = get_runtime().context or {}
            trace = LocalTrace(store, state["run"]["run_id"], settings, (context.get("secrets") or {}).values())
            async with trace.span(name, "node", {k: v for k, v in state.items() if k != "run"}) as output:
                result = await node(state)
                output.update(result)
                return result

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
