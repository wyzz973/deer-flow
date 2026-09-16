"""Deterministic gates; they do not claim to prove semantic entailment."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from .contracts import ResearchGap, ResearchPlan, ResearchUnit, StructuredReport
from .evidence import digest
from .report_policy import report_character_limit, source_allowed


def research_gaps(plan: ResearchPlan, units, findings, pool, results=()):
    gaps = []
    # A supplement can satisfy its original objective; it is not a new user objective.
    for original in plan.research_units:
        family = {original.id}
        family |= {u["id"] for u in units if (u.get("parent_gap_id") or "").startswith(original.id + "-")}
        relevant = [f for f in findings if f["unit_id"] in family]
        evidence_ids = {eid for f in relevant for eid in f["evidence_ids"]}
        evidences = [pool[eid] for eid in evidence_ids if eid in pool]
        problems = []
        latest = next((result for result in reversed(results) if result["unit_id"] in family), None)
        if latest and latest.get("open_questions"):
            problems.append(("open-questions", "研究 Agent 尚未解决：" + "；".join(latest["open_questions"])))
        if not relevant or not evidences:
            problems.append(("coverage", "该研究目标尚无可引用的发现"))
        if any(not f["evidence_ids"] for f in relevant):
            problems.append(("unsupported", "存在未绑定证据的结论，需要补证或删除"))
        origins = {e["origin"] for e in evidences}
        for origin in original.source_strategy.required_origins:
            if origin not in origins:
                problems.append(("missing-" + origin, f"缺少 {origin} 的有效证据，不可用另一来源回退替代"))
        # Native opaque ToolMessages have no mandatory publication-date schema.
        # The researcher evaluates date constraints and reports uncertainty as
        # open_questions. Only explicitly supplied document metadata can be
        # checked mechanically; do not manufacture a date from a tool payload.
        if original.source_strategy.not_before and evidences and all(e.get("provenance", "document") == "document" for e in evidences):
            cutoff = original.source_strategy.not_before
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=UTC)

            def recent_enough(e):
                if not e["published_at"]:
                    return False
                stamp = datetime.fromisoformat(e["published_at"])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                return stamp >= cutoff

            recent = [e for e in evidences if recent_enough(e)]
            if not recent:
                problems.append(("date", "缺少满足日期要求的来源；发布日期不明不能算作满足时效"))
        for finding in relevant:
            if finding.get("high_risk"):
                matching_ids = {eid for f in relevant if " ".join(f["claim"].split()).casefold() == " ".join(finding["claim"].split()).casefold() for eid in f["evidence_ids"]}
                matched = [pool[eid] for eid in matching_ids if eid in pool]
                documents = all(e.get("provenance", "document") == "document" for e in matched)
                publishers = {e["publisher"] if documents else e["source_name"] for e in matched}
                if len(publishers) < 2:
                    requirement = "独立发布方" if documents else "不同配置来源的调用记录（不是原文独立性证明）"
                    problems.append(("independence", "高风险结论缺少两个" + requirement + "；补证时复用此结论原文：" + finding["claim"]))
        for code, text in dict(problems).items():
            gaps.append(ResearchGap(gap_id=original.id + "-" + code, unit_id=original.id, code=code, description=text).model_dump())
    return gaps


def supplemental_units(plan, gaps, iteration, remaining):
    originals = {u.id: u for u in plan.research_units}
    grouped = {}
    for gap in gaps:
        grouped.setdefault(gap["unit_id"], []).append(gap)
    result = []
    for uid, missing in grouped.items():
        original = originals[uid]
        result.append(
            ResearchUnit(
                **{
                    **original.model_dump(),
                    "id": f"S{iteration}-{digest(uid)[:8]}",
                    "objective": original.objective + "\n只补充以下缺口：" + "；".join(g["description"] for g in missing),
                    "parent_gap_id": missing[0]["gap_id"],
                    "depends_on": [uid],
                }
            ).model_dump(mode="json")
        )
    return result[:remaining]


def validate_report(report: StructuredReport, plan, pool, limited=False):
    errors = []
    expected = {u.id for u in plan.research_units}
    covered = {uid for section in report.sections for uid in section.unit_ids}
    if not expected.issubset(covered):
        errors.append("缺少研究维度: " + ",".join(sorted(expected - covered)))
    for segment in report.segments():
        if segment.segment_type == "fact" and not segment.evidence_ids:
            errors.append("事实段落必须有 evidence_ids")
        if not set(segment.evidence_ids).issubset(pool):
            errors.append("使用了不存在的 evidence_id")
        if any(not source_allowed(pool[eid], plan.source_policy) for eid in segment.evidence_ids if eid in pool):
            errors.append("引用不符合计划中的来源范围或原文读取要求")
        if re.search(r"\[(?:\d+|E\d+)\]|https?://", segment.text):
            errors.append("段落不得自带引用编号或 URL；引用只能由 Binder 生成")
        # Technical placeholders such as <name>_docsize are ordinary text.
        # Renderers escape markup rather than confusing it with bad evidence.
    limit = report_character_limit(plan.report_style)
    if sum(len(segment.text) for segment in report.segments()) > limit:
        errors.append(f"正文超过 {plan.report_style} 报告的 {limit} 字符上限；去重并压缩，不要删去关键证据或限制")
    # Coverage is structural, not proof that a segment answers the objective.
    return list(dict.fromkeys(errors))
