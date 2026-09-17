"""Deterministic research gates; they do not claim to prove semantic entailment."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlsplit

from .contracts import ResearchGap, ResearchPlan, ResearchUnit
from .evidence import digest

# Supplementary research is spent where it changes the report most.
GAP_PRIORITY = {"coverage": 0, "unsupported": 1, "missing-internal": 2, "missing-external": 2, "date": 3, "open-questions": 4}


def research_gaps(plan: ResearchPlan, units, findings, pool, results=(), citable_ids=None):
    """Gaps that more public research can plausibly close.

    Questions about the user's own organization are recorded by researchers as
    assumptions, not open questions, so they never trigger supplementation.
    Evidence that cannot be cited (for example search result snippets) does not
    count as coverage when ``citable_ids`` is supplied.
    """
    gaps = []
    # A supplement can satisfy its original objective; it is not a new user objective.
    for original in plan.research_units:
        family = {original.id}
        family |= {u["id"] for u in units if (u.get("parent_gap_id") or "").startswith(original.id + "-")}
        relevant = [f for f in findings if f["unit_id"] in family]
        evidence_ids = {eid for f in relevant for eid in f["evidence_ids"] if eid in pool and (citable_ids is None or eid in citable_ids)}
        evidences = [pool[eid] for eid in evidence_ids]
        problems = []
        latest = next((result for result in reversed(results) if result["unit_id"] in family), None)
        if latest and latest.get("open_questions"):
            problems.append(("open-questions", "仍可通过公开资料补充：" + "；".join(latest["open_questions"][:6])))
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

            if not [e for e in evidences if recent_enough(e)]:
                problems.append(("date", "缺少满足日期要求的来源；发布日期不明不能算作满足时效"))
        for code, text in dict(problems).items():
            gaps.append(ResearchGap(gap_id=original.id + "-" + code, unit_id=original.id, code=code, description=text).model_dump())
    return gaps


def supplemental_units(plan, gaps, iteration, remaining):
    """Create at most ``remaining`` supplements, most severe gaps first.

    The caller records gaps whose units were deferred so a truncated round is
    visible in the activity log and the report's limitations.
    """
    originals = {u.id: u for u in plan.research_units}
    order = {u.id: index for index, u in enumerate(plan.research_units)}
    grouped = {}
    for gap in gaps:
        grouped.setdefault(gap["unit_id"], []).append(gap)
    ranked = sorted(grouped.items(), key=lambda item: (min(GAP_PRIORITY.get(g["code"], 9) for g in item[1]), order.get(item[0], 999)))
    result = []
    for uid, missing in ranked:
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
    return result[: max(0, remaining)]


def single_source(finding, pool, eligible):
    """A high-risk claim whose citable support comes from one site only.

    This is a writing instruction (hedge or attribute the claim), not a gap:
    two URLs on one domain are not independent confirmation.
    """
    if not finding.get("high_risk"):
        return False
    sites = set()
    for eid in finding.get("evidence_ids", []):
        if eid in eligible and eid in pool:
            evidence = pool[eid]
            sites.add((urlsplit(evidence.get("url") or evidence.get("canonical_url") or "").hostname or "").removeprefix("www.") or evidence.get("source_name"))
    return len(sites) < 2
