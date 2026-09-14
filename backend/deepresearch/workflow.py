"""Explicit LangGraph control flow. Side effects before interrupts are avoided."""
from __future__ import annotations

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .contracts import ResearchError, ResearchPlan, ResearchResult, ResearchUnit, StructuredReport
from .evidence import digest, merge_results
from .render import bind_citations, citation_metadata, html_report, markdown
from .validators import research_gaps, supplemental_units, validate_report


class ResearchState(TypedDict, total=False):
    run: dict
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
        run = s["run"]
        await stage(s, "PLANNING")
        proposed = s.get("decision", {}).get("plan")
        version = s.get("plan", {}).get("plan_version", 0) + 1
        key = "plan:" + digest([version, proposed])
        cached = await store.cached(run["run_id"], key)
        plan = ResearchPlan.model_validate(cached) if cached else await runner.plan(run, proposed)
        plan.plan_version = version
        settings.check_plan(plan, type("Budget", (), run["budget"])(), run["source_names"])
        body = plan.model_dump(mode="json")
        await store.cache(run["run_id"], key, body)
        await store.patch(run["run_id"], plan=body)
        await store.event(run["run_id"], "plan.updated" if version > 1 else "plan.created", {"plan_version": version}, key=f"plan-{version}")
        return {"plan": body, "units": body["research_units"], "decision": {}, "status": "PLANNING"}

    async def review(s):
        # The node restarts on resume; everything before interrupt is pure.
        decision = interrupt({"type": "plan_review", "plan": s["plan"]})
        if not isinstance(decision, dict) or decision.get("action") not in {"approve", "edit", "reject"}:
            raise ResearchError("PLAN_DECISION", "无效的计划确认操作")
        return {"decision": decision}

    async def rejected(s):
        await stage(s, "CANCELLED", "run.cancelled")
        return {"status": "CANCELLED"}

    async def dispatch(s):
        await stage(s, "RESEARCHING")
        run, units = s["run"], [ResearchUnit.model_validate(u) for u in s["units"]]
        results = {}
        for unit in units:
            cached = await store.unit(run["run_id"], unit.id, digest(unit.model_dump(mode="json")))
            if cached is not None:
                results[unit.id] = cached
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
                    await store.save_unit(run["run_id"], unit.id, digest(unit.model_dump(mode="json")), body)
                    await store.mutate(run["run_id"], lambda r: r["unit_statuses"].update({unit.id: "COMPLETED"}))
                    await store.event(run["run_id"], "research.unit.completed", {"unit_id": unit.id, "skill_name": unit.skill}, key="unit-done-" + unit.id)
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
        await store.event(s["run"]["run_id"], "evidence.pool.updated", {"count": len(pool)}, key="pool-" + digest(pool))
        return {"evidence_pool": pool, "findings": findings, "lineage": lineage}

    async def validator(s):
        await stage(s, "VALIDATING")
        plan = ResearchPlan.model_validate(s["plan"])
        gaps = research_gaps(plan, s["units"], s["findings"], s["evidence_pool"])
        run_id = s["run"]["run_id"]
        await store.save_pool(run_id, s["evidence_pool"], gaps)
        await store.patch(run_id, gaps=gaps)
        if not gaps:
            await stage(s, "RESEARCH_COMPLETE", "validator.passed")
            return {"gaps": [], "limitations": [], "status": "RESEARCH_COMPLETE"}
        exhausted = s.get("iteration", 0) >= s["run"]["budget"]["max_iterations"] or len(s["units"]) >= s["run"]["budget"]["max_units"]
        signature = digest([(g["unit_id"], g["code"]) for g in gaps])
        saturated = signature == s.get("previous_gap_signature") and len(s["evidence_pool"]) <= s.get("previous_pool_size", -1)
        if exhausted or saturated:
            if not settings.allow_limited_report:
                raise ResearchError("RESEARCH_GAPS", "研究仍有缺口且达到补研停止条件；未生成伪完整报告", recoverable=False)
            limitations = [g["description"] for g in gaps]
            await store.patch(run_id, limitations=limitations)
            return {"gaps": gaps, "limitations": limitations, "status": "RESEARCH_COMPLETE"}
        await stage(s, "GAP_FOUND", "validator.gap_found", {"gaps": gaps})
        return {"gaps": gaps, "previous_gap_signature": signature, "previous_pool_size": len(s["evidence_pool"]), "status": "GAP_FOUND"}

    async def supplement(s):
        iteration = s.get("iteration", 0) + 1
        extra = supplemental_units(ResearchPlan.model_validate(s["plan"]), s["gaps"], iteration,
                                   s["run"]["budget"]["max_units"] - len(s["units"]))
        if not extra:
            raise ResearchError("UNIT_BUDGET", "没有可用的补研单元预算", recoverable=False)
        await store.patch(s["run"]["run_id"], iteration=iteration, units=s["units"] + extra)
        return {"units": s["units"] + extra, "iteration": iteration}

    async def synthesis(s):
        await stage(s, "SYNTHESIZING", "report.synthesizing")
        key = "synthesis:" + digest([s["plan"], s["findings"], s.get("final_errors", []), s.get("synthesis_repairs", 0)])
        cached = await store.cached(s["run"]["run_id"], key)
        report = StructuredReport.model_validate(cached) if cached else await runner.synthesize(
            s["run"], ResearchPlan.model_validate(s["plan"]), s["findings"], s["evidence_pool"], s.get("final_errors", []))
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
            raise ResearchError("FINAL_VALIDATION", "报告未通过引用/结构校验，已停止输出", recoverable=False)
        return {"final_errors": errors, "synthesis_repairs": repairs + (1 if errors else 0)}

    async def render(s):
        await stage(s, "RENDERING")
        report, pool, mapping = StructuredReport.model_validate(s["structured_report"]), s["evidence_pool"], s["citation_map"]
        limited, demo = s.get("limitations", []), settings.runner == "demo"
        text = markdown(report, mapping, pool, limited, demo)
        body = {"version": 1 + s.get("synthesis_repairs", 0), "report": report.model_dump(mode="json"), "markdown": text,
                "html": html_report(report, mapping, pool, limited, demo), "citations": citation_metadata(mapping, pool),
                "citation_map": mapping, "limitations": limited, "demo": demo}
        await store.save_report(s["run"]["run_id"], body["version"], body)
        await store.patch(s["run"]["run_id"], report=body, status="COMPLETED")
        await store.event(s["run"]["run_id"], "report.completed", {"version": body["version"], "limitations": limited}, key="report-completed")
        return {"rendered_report": text, "status": "COMPLETED"}

    graph = StateGraph(ResearchState, context_schema=dict)
    for name, node in [("planner", planner), ("plan_review", review), ("rejected", rejected), ("dispatch", dispatch),
                       ("evidence_merge", merge), ("validator", validator), ("supplement", supplement),
                       ("synthesis", synthesis), ("citation_binder", binder), ("final_validator", final_validate), ("renderer", render)]:
        graph.add_node(name, node)
    graph.add_edge(START, "planner")
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
