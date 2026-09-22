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
        # A supplement names the step it extends. Matching gap-id prefixes took
        # "market-size" for a supplement of "market" and closed the wrong gap.
        family = {original.id}
        family |= {u["id"] for u in units if u.get("parent_gap_id") and (u.get("depends_on") or [None])[0] == original.id}
        relevant = [f for f in findings if f["unit_id"] in family]
        evidence_ids = {eid for f in relevant for eid in f["evidence_ids"] if eid in pool and (citable_ids is None or eid in citable_ids)}
        evidences = [pool[eid] for eid in evidence_ids]
        problems = []
        latest = next((result for result in reversed(results) if result["unit_id"] in family), None)
        # Open questions earn one more round. A researcher can always think of
        # another question, so asking again after a supplement never converged:
        # two planned steps ran as six with max_iterations 2 and as eighteen with 8.
        supplemented = len(family) > 1
        if latest and latest.get("open_questions") and not supplemented:
            problems.append(("open-questions", "仍可通过公开资料补充：" + "；".join(latest["open_questions"][:6])))
        if not relevant or not evidences:
            problems.append(("coverage", "该研究目标尚无可引用的发现"))
        if any(not f["evidence_ids"] for f in relevant):
            problems.append(("unsupported", "存在未绑定证据的结论，需要补证或删除"))
        origins = {e["origin"] for e in evidences}
        for origin in original.source_strategy.required_origins:
            if origin not in origins:
                problems.append(("missing-" + origin, f"缺少 {origin} 的有效证据，不可用另一来源回退替代"))
        # A date is never manufactured from a tool payload: it is only ever what
        # a source stated about its own page. So a date requirement is enforced
        # exactly where it is provable — some evidence is dated and all of it
        # predates the cutoff. Evidence nobody dated stays the researcher's
        # judgement, reported as an open question: no supplement can make a
        # provider send dates it does not have.
        if original.source_strategy.not_before and evidences:
            cutoff = original.source_strategy.not_before
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=UTC)

            def stated(e):
                if not e.get("published_at"):
                    return None
                try:
                    stamp = datetime.fromisoformat(str(e["published_at"]))
                except ValueError:
                    return None
                return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)

            dates = [stamp for stamp in map(stated, evidences) if stamp]
            if dates and not [stamp for stamp in dates if stamp >= cutoff]:
                problems.append(("date", f"已知发布日期的来源都早于 {cutoff.date()}，需要更新的资料；日期不明的来源不能算作满足时效"))
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
                    # The objective has a length limit; a long list of open questions must not make the supplement invalid.
                    "objective": (original.objective[:2400] + "\n只补充以下缺口：" + "；".join(g["description"] for g in missing))[:3900],
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
