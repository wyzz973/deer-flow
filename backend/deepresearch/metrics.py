"""Quantify the cost and efficiency of research runs from durable records.

Inputs are the store's metric tables (model calls, tool calls, subagent runs),
lifecycle events and ended workflow/node spans. Nothing here calls a model or
reads tool payloads; prices come only from operator configuration.

Run as a module to compare runs offline::

    python -m deepresearch.metrics --data-dir .deerflow/deepresearch/research --format table
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import TERMINAL, utcnow

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens", "total_tokens")
PHASE_ORDER = ["planner", "plan_review", "dispatch", "evidence_merge", "validator", "supplement", "synthesis", "citation_binder", "final_validator", "renderer", "follow_up", "rejected"]
TABLE_ROW = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)


def _time(value):
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _round(value, digits=3):
    return None if value is None else round(value, digits)


def _ratio(numerator, denominator, digits=3):
    return round(numerator / denominator, digits) if numerator is not None and denominator else None


def percentiles(values):
    """Nearest-rank latency summary in milliseconds."""
    values = sorted(value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool))
    if not values:
        return {"count": 0, "avg": None, "p50": None, "p95": None, "max": None}

    def rank(quantile):
        return values[min(len(values) - 1, max(0, math.ceil(quantile * len(values)) - 1))]

    return {"count": len(values), "avg": round(sum(values) / len(values)), "p50": rank(0.5), "p95": rank(0.95), "max": values[-1]}


def _price(pricing, call):
    price = (pricing or {}).get(call.get("model")) or (pricing or {}).get(call.get("response_model"))
    if price is None:
        return None
    return price.model_dump() if hasattr(price, "model_dump") else dict(price)


def call_cost(call, pricing):
    """(currency, amount) for one model call; None when unpriced or usage unknown."""
    price = _price(pricing, call)
    if price is None or not call.get("usage_reported"):
        return None
    input_tokens = call.get("input_tokens") or 0
    cached = min(call.get("cache_read_tokens") or 0, input_tokens)
    cached_price = price.get("cached_input_per_million")
    cached_price = price["input_per_million"] if cached_price is None else cached_price
    amount = ((input_tokens - cached) * price["input_per_million"] + cached * cached_price + (call.get("output_tokens") or 0) * price["output_per_million"]) / 1_000_000
    return price.get("currency", "USD"), amount


def _cost_totals(model_calls, pricing):
    by_currency, priced, unpriced = defaultdict(float), 0, set()
    for call in model_calls:
        cost = call_cost(call, pricing)
        if cost is None:
            if call.get("usage_reported") and _price(pricing, call) is None:
                unpriced.add(call.get("model") or "unknown")
            continue
        by_currency[cost[0]] += cost[1]
        priced += 1
    currencies = sorted(by_currency)
    return {
        "currency": currencies[0] if len(currencies) == 1 else ("MIXED" if currencies else None),
        "total": _round(by_currency[currencies[0]], 6) if len(currencies) == 1 else None,
        "by_currency": {currency: _round(amount, 6) for currency, amount in by_currency.items()},
        "priced_calls": priced,
        "unpriced_models": sorted(unpriced),
    }


def _domain(url):
    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:
        return None
    return host.removeprefix("www.") or None


def _key(record, key):
    value = record.get(key)
    return "unknown" if value is None or value == "" else str(value)


def _group(model_calls, tool_calls, key, pricing, order=None):
    groups = defaultdict(lambda: {"model_calls": 0, "model_errors": 0, **dict.fromkeys(TOKEN_FIELDS, 0), "model_ms": 0, "cost": 0.0, "priced_calls": 0, "tool_calls": 0, "tool_errors": 0, "tool_ms": 0})
    for call in model_calls:
        group = groups[_key(call, key)]
        group["model_calls"] += 1
        group["model_errors"] += call.get("status") not in {"ok", "running"}
        for field in TOKEN_FIELDS:
            group[field] += call.get(field) or 0
        group["model_ms"] += call.get("duration_ms") or 0
        if cost := call_cost(call, pricing):
            group["cost"] += cost[1]
            group["priced_calls"] += 1
    for call in tool_calls:
        group = groups[_key(call, key)]
        group["tool_calls"] += 1
        group["tool_errors"] += call.get("status") == "error"
        group["tool_ms"] += call.get("duration_ms") or 0
    rows = [{"key": name, **values, "cost": _round(values["cost"], 6) if values["priced_calls"] else None} for name, values in groups.items()]
    rank = {name: index for index, name in enumerate(order or [])}
    return sorted(rows, key=lambda row: (rank.get(row["key"], len(rank)), -row["total_tokens"], row["key"]))


def _max_parallel(runs):
    edges = []
    for run in runs:
        start, end = _time(run.get("started_at")), _time(run.get("ended_at"))
        if start and end:
            edges += [(start, 1), (end, -1)]
    current = peak = 0
    for _, delta in sorted(edges, key=lambda edge: (edge[0], edge[1])):
        current += delta
        peak = max(peak, current)
    return peak


def summarize(run, *, model_calls=(), tool_calls=(), agent_runs=(), events=(), spans=(), unit_results=(), pricing=None, now=None):
    """A cost/efficiency profile of one research run across all its cycles."""
    now = now or datetime.now(UTC)
    model_calls, tool_calls, agent_runs, events, spans = list(model_calls), list(tool_calls), list(agent_runs), list(events), list(spans)
    status = run.get("status")
    by_type = defaultdict(list)
    for event in events:
        by_type[event["type"]].append(event)

    # Time: wall clock includes user decisions and downtime; active is compute.
    created = _time(run.get("created_at"))
    completed = [_time(event["at"]) for event in by_type["report.completed"]]
    finished = max(completed) if status == "COMPLETED" and completed else _time(run.get("updated_at")) if status in TERMINAL else now
    wall = (finished - created).total_seconds() if created and finished else None
    live = status not in TERMINAL

    def open_elapsed(kind):
        # Only the latest open span of a live run is still running; older open
        # spans belong to interrupted attempts and never ended.
        opened = [span for span in spans if span.get("kind") == kind and span.get("duration_ms") is None and span.get("status") == "running"]
        latest = max(opened, key=lambda span: span.get("at") or "", default=None)
        start = _time(latest.get("at")) if latest and live else None
        return (latest, max(0.0, (now - start).total_seconds())) if start else (None, 0.0)

    active = sum(span.get("duration_ms") or 0 for span in spans if span.get("kind") == "workflow") / 1000 + open_elapsed("workflow")[1]
    phases = defaultdict(lambda: {"seconds": 0.0, "runs": 0, "errors": 0})
    for span in spans:
        if span.get("kind") == "node" and span.get("duration_ms") is not None:
            phase = phases[span.get("name") or "unknown"]
            phase["seconds"] += span["duration_ms"] / 1000
            phase["runs"] += 1
            phase["errors"] += span.get("status") == "error"
    current_node, current_seconds = open_elapsed("node")
    if current_node:
        phase = phases[current_node.get("name") or "unknown"]
        phase["seconds"] += current_seconds
        phase["runs"] += 1
    rank = {name: index for index, name in enumerate(PHASE_ORDER)}
    queued_ms = sum(event["data"].get("queued_ms") or 0 for kind in ("research.unit.started", "report.section.started") for event in by_type[kind])
    time_summary = {
        "wall_seconds": _round(wall),
        "active_seconds": _round(active),
        "waiting_seconds": _round(max(0.0, wall - active)) if wall is not None else None,
        "model_seconds": _round(sum(call.get("duration_ms") or 0 for call in model_calls) / 1000),
        "tool_seconds": _round(sum(call.get("duration_ms") or 0 for call in tool_calls) / 1000),
        "queue_seconds": _round(queued_ms / 1000),
        "in_progress": live,
        "phases": [{"phase": name, **{**values, "seconds": _round(values["seconds"])}} for name, values in sorted(phases.items(), key=lambda item: rank.get(item[0], len(rank)))],
    }

    # Tokens and model calls.
    tokens = {field.removesuffix("_tokens"): sum(call.get(field) or 0 for call in model_calls) for field in TOKEN_FIELDS}
    unreported = [call for call in model_calls if call.get("status") != "running" and not call.get("usage_reported")]
    tokens.update(
        unreported_calls=len(unreported),
        estimated_unreported=sum(call.get("estimated_tokens") or 0 for call in unreported),
        cache_read_ratio=_ratio(tokens["cache_read"], tokens["input"]),
    )
    cost = _cost_totals(model_calls, pricing)
    model_summary = {
        "count": len(model_calls),
        "running": sum(call.get("status") == "running" for call in model_calls),
        "errors": sum(call.get("status") not in {"ok", "running"} for call in model_calls),
        "error_codes": dict(Counter(call.get("error_code") for call in model_calls if call.get("error_code"))),
        "finish_reasons": dict(Counter(call.get("finish_reason") for call in model_calls if call.get("finish_reason"))),
        "latency_ms": percentiles(call.get("duration_ms") for call in model_calls if call.get("status") == "ok"),
        "max_input_tokens": max((call.get("input_tokens") or 0 for call in model_calls), default=0),
    }
    # Runs from before per-call metering have a budget ledger but no call
    # records: keep the ledger total and report what was not measured as unknown.
    ledger = run.get("usage") or {}
    ledger_tokens = ledger.get("reported_model_tokens") or ledger.get("model_tokens") or 0
    metered = bool(model_calls) or not ledger_tokens
    if not metered:
        tokens = {**dict.fromkeys(tokens), "total": ledger_tokens}
        model_summary.update(count=None, running=None, errors=None, max_input_tokens=None)
        time_summary.update(model_seconds=None, queue_seconds=None)

    # Tools. A repeat is an identical request (same tool and arguments) after
    # one that already succeeded: within one agent the result is usually still
    # in its context; across agents a shared cache could have served it.
    per_tool = defaultdict(lambda: {"count": 0, "errors": 0, "durations": [], "output_chars": 0, "repeats": 0, "error_types": Counter()})
    succeeded, succeeded_by_agent, repeats_same_agent = set(), set(), 0
    # Runs recorded before request keys existed cannot be judged: report None, not 0.
    keyed = any(call.get("request_key") for call in tool_calls)
    for call in sorted(tool_calls, key=lambda item: item.get("started_at") or ""):
        name = call.get("tool_name") or "tool"
        tool = per_tool[(name, call.get("role"))]
        tool["count"] += 1
        tool["durations"].append(call.get("duration_ms"))
        tool["output_chars"] += call.get("output_chars") or 0
        if call.get("status") == "error":
            tool["errors"] += 1
            tool["error_types"][call.get("error_type") or "ToolReturnedError"] += 1
        if request := call.get("request_key"):
            agent = call.get("execution_id") or call.get("unit_id")
            if (name, request) in succeeded:
                tool["repeats"] += 1
                repeats_same_agent += (name, request, agent) in succeeded_by_agent
            if call.get("status") == "success":
                succeeded.add((name, request))
                succeeded_by_agent.add((name, request, agent))
    reads = [call for call in tool_calls if call.get("role") == "read"]
    pages_read = {call.get("url") or call["id"] for call in reads if call.get("status") == "success"}
    domains = defaultdict(lambda: {"reads": 0, "errors": 0, "error_types": Counter()})
    for call in reads:
        if domain := _domain(call.get("url")):
            domains[domain]["reads"] += 1
            if call.get("status") == "error":
                domains[domain]["errors"] += 1
                domains[domain]["error_types"][call.get("error_type") or "ToolReturnedError"] += 1
    error_types = sum((values["error_types"] for values in per_tool.values()), Counter())
    tool_summary = {
        "count": len(tool_calls),
        "errors": sum(call.get("status") == "error" for call in tool_calls),
        "error_rate": _ratio(sum(call.get("status") == "error" for call in tool_calls), len(tool_calls)),
        "error_types": dict(error_types.most_common()),
        "latency_ms": percentiles(call.get("duration_ms") for call in tool_calls),
        "output_chars": sum(call.get("output_chars") or 0 for call in tool_calls),
        "searches": sum(call.get("role") == "search" for call in tool_calls),
        "reads": len(reads),
        "read_errors": sum(call.get("status") == "error" for call in reads),
        "pages_read": len(pages_read),
        "repeat_calls": sum(values["repeats"] for values in per_tool.values()) if keyed else None,
        "repeat_calls_same_agent": repeats_same_agent if keyed else None,
        "failing_domains": sorted(
            ({"domain": name, **values, "error_types": dict(values["error_types"].most_common())} for name, values in domains.items() if values["errors"]),
            key=lambda row: (-row["errors"], row["domain"]),
        )[:10],
        "by_tool": sorted(
            (
                {
                    "tool": name,
                    "role": role,
                    "count": values["count"],
                    "errors": values["errors"],
                    "error_types": dict(values["error_types"].most_common()),
                    "repeats": values["repeats"] if keyed else None,
                    "latency_ms": percentiles(values["durations"]),
                    "output_chars": values["output_chars"],
                }
                for (name, role), values in per_tool.items()
            ),
            key=lambda row: -row["count"],
        ),
    }

    # Subagents: one row per native execution, costed from its own model calls.
    cost_by_execution = defaultdict(float)
    for call in model_calls:
        if call.get("execution_id") and (priced := call_cost(call, pricing)):
            cost_by_execution[call["execution_id"]] += priced[1]
    rows = []
    for agent in sorted(agent_runs, key=lambda item: item.get("started_at") or ""):
        rows.append(
            {
                **{key: agent.get(key) for key in ("id", "skill", "agent_name", "unit_id", "phase", "cycle", "status", "error_code", "stop_reason", "model_calls", "tool_calls", "tool_errors", "max_input_tokens", *TOKEN_FIELDS)},
                "seconds": _round((agent.get("duration_ms") or 0) / 1000),
                "cost": _round(cost_by_execution[agent["id"]], 6) if agent.get("id") in cost_by_execution else None,
            }
        )
    per_skill = defaultdict(lambda: {"count": 0, "failed": 0, "seconds": 0.0, "model_calls": 0, "tool_calls": 0, "total_tokens": 0, "cost": 0.0})
    for row in rows:
        skill = per_skill[row["skill"] or "unknown"]
        skill["count"] += 1
        skill["failed"] += row["status"] != "completed"
        skill["seconds"] += row["seconds"] or 0
        skill["model_calls"] += row["model_calls"] or 0
        skill["tool_calls"] += row["tool_calls"] or 0
        skill["total_tokens"] += row["total_tokens"] or 0
        skill["cost"] += row["cost"] or 0
    agent_summary = {
        "count": len(rows),
        "completed": sum(row["status"] == "completed" for row in rows),
        "failed": sum(row["status"] == "failed" for row in rows),
        "cancelled": sum(row["status"] == "cancelled" for row in rows),
        "failure_codes": dict(Counter(row["error_code"] for row in rows if row["error_code"])),
        "max_parallel": _max_parallel(agent_runs),
        "by_skill": [
            {"skill": name, **values, "seconds": _round(values["seconds"]), "avg_seconds": _ratio(values["seconds"], values["count"], 1), "cost": _round(values["cost"], 6) if cost["priced_calls"] else None}
            for name, values in sorted(per_skill.items(), key=lambda item: -item[1]["total_tokens"])
        ],
        "runs": rows,
    }
    if not metered:
        agent_summary.update(count=None, completed=None, failed=None, cancelled=None, max_parallel=None)

    # Research and report outcomes.
    units = run.get("units") or []
    planned = [unit for unit in units if not unit.get("parent_gap_id")]
    report = run.get("report") or {}
    document = report.get("document") or ""
    citations = report.get("citations") or []
    audit = report.get("audit") or {}
    research = {
        "planned_units": len(planned),
        "supplement_units": len(units) - len(planned),
        "iterations": run.get("iteration", 0),
        "failed_units": len(run.get("unit_failures") or {}),
        "raw_evidence": sum(len((item.get("result") or {}).get("raw_evidences") or []) for item in unit_results),
        "evidence_pool": run.get("evidence_count"),
        "trimmed_evidence": sum(event["data"].get("dropped") or 0 for event in by_type["research.evidence.trimmed"]),
        "pruned_references": sum(event["data"].get("references") or 0 for event in by_type["research.output.pruned"]),
        "conversion_retries": len(by_type["research.output.retry"]),
        "deferred_supplements": len(by_type["research.supplement.deferred"]),
    }
    report_summary = {
        "versions": len(by_type["report.completed"]),
        "characters": len(document),
        "sections": sum(1 for line in document.splitlines() if line.startswith("## ")),
        "tables": len(TABLE_ROW.findall(document)),
        "diagrams": document.count("```mermaid"),
        "citations": len(citations),
        "cited_domains": len({item.get("domain") for item in citations if item.get("domain")}),
        "draft_repairs": len(by_type["report.draft.repair"]),
        "dropped_statements": audit.get("dropped_statements", 0),
        "validation_retries": len(by_type["report.validation.retry"]),
    }
    cache = Counter(event["data"].get("kind") or "unknown" for event in by_type["metrics.cache_hit"])

    # Per research unit: what it cost and what it contributed to the report.
    usage_by_unit = {row["key"]: row for row in _group(model_calls, tool_calls, "unit_id", pricing)}
    evidence_by_unit, cited_by_unit, searches_by_unit = Counter(), Counter(), Counter()
    for item in unit_results:
        result = item.get("result") or {}
        evidence_by_unit[result.get("unit_id") or str(item.get("id", "")).rpartition(":")[2]] += len(result.get("raw_evidences") or [])
    for item in citations:
        cited_by_unit.update(set(item.get("unit_ids") or []))
    pages_by_unit, seconds_by_unit = defaultdict(set), defaultdict(float)
    for call in tool_calls:
        searches_by_unit[call.get("unit_id")] += call.get("role") == "search"
        if call.get("role") == "read" and call.get("status") == "success":
            pages_by_unit[call.get("unit_id")].add(call.get("url") or call["id"])
    for agent in agent_runs:
        seconds_by_unit[agent.get("unit_id")] += (agent.get("duration_ms") or 0) / 1000
    failures = run.get("unit_failures") or {}
    unit_outcomes = []
    for unit in units:
        unit_id, usage = unit.get("id"), usage_by_unit.get(unit.get("id")) or {}
        unit_outcomes.append(
            {
                "unit_id": unit_id,
                "title": unit.get("title"),
                "skill": unit.get("skill"),
                "supplement": bool(unit.get("parent_gap_id")),
                "failed": unit_id in failures,
                "seconds": _round(seconds_by_unit.get(unit_id)) if metered else None,
                "model_calls": usage.get("model_calls", 0) if metered else None,
                "total_tokens": usage.get("total_tokens", 0) if metered else None,
                "cost": usage.get("cost"),
                "tool_calls": usage.get("tool_calls", 0),
                "searches": searches_by_unit[unit_id],
                "pages_read": len(pages_by_unit[unit_id]),
                "evidence": evidence_by_unit[unit_id],
                "citations": cited_by_unit[unit_id],
                "tokens_per_citation": _ratio(usage.get("total_tokens", 0), cited_by_unit[unit_id], 0) if metered else None,
            }
        )

    # Consumption against the run's own limits; None means unlimited.
    limits, usage = run.get("budget") or {}, run.get("usage") or {}
    budget = {
        "max_model_tokens": limits.get("max_model_tokens"),
        "model_tokens_used": _ratio(usage.get("model_tokens"), limits.get("max_model_tokens")),
        "max_tool_calls": limits.get("max_tool_calls"),
        "tool_calls_used": _ratio(usage.get("tool_calls"), limits.get("max_tool_calls")),
        "max_elapsed_seconds": limits.get("max_elapsed_seconds"),
        # usage.elapsed_seconds is settled when a drive ends; a live run also has its open span.
        "elapsed_used": _ratio(max(usage.get("elapsed_seconds") or 0, active), limits.get("max_elapsed_seconds")),
    }

    conversion_tokens = sum(call.get("total_tokens") or 0 for call in model_calls if call.get("purpose") == "conversion")
    failed_tokens = sum(row["total_tokens"] or 0 for row in rows if row["status"] != "completed")
    # Citation ratios describe a published report, not an unfinished run.
    count = report_summary["citations"] if report_summary["versions"] or report_summary["characters"] else None
    efficiency = {
        "tokens_per_citation": _ratio(tokens["total"], count, 0),
        "cost_per_citation": _ratio(cost["total"], count, 6),
        "active_seconds_per_citation": _ratio(active, count, 1),
        "pages_read_per_citation": _ratio(tool_summary["pages_read"], count, 2),
        "citations_per_page_read": _ratio(count, tool_summary["pages_read"], 3),
        "searches_per_unit": _ratio(tool_summary["searches"], len(units), 1),
        "repeat_tool_call_ratio": _ratio(tool_summary["repeat_calls"], tool_summary["count"]),
        "tokens_per_report_char": _ratio(tokens["total"], report_summary["characters"], 1),
        "conversion_token_share": _ratio(conversion_tokens, tokens["total"]) if metered else None,
        "failed_agent_token_share": _ratio(failed_tokens, tokens["total"]) if metered else None,
        "cache_read_ratio": tokens["cache_read_ratio"],
    }

    order = [*PHASE_ORDER]
    return {
        "run_id": run.get("run_id"),
        "status": status,
        "query": run.get("query"),
        "created_at": run.get("created_at"),
        "cycles": run.get("cycle", 0) + 1,
        "metered": metered,
        "generated_at": utcnow(),
        "time": time_summary,
        "tokens": tokens,
        "cost": cost,
        "model_calls": model_summary,
        "tools": tool_summary,
        "agents": agent_summary,
        "research": research,
        "units": unit_outcomes,
        "budget": budget,
        "report": report_summary,
        "cache": {"hits": sum(cache.values()), "by_kind": dict(cache)},
        "efficiency": efficiency,
        "breakdown": {
            "by_phase": _group(model_calls, tool_calls, "phase", pricing, order),
            "by_purpose": _group(model_calls, [], "purpose", pricing),
            "by_skill": _group(model_calls, tool_calls, "skill", pricing),
            "by_model": _group(model_calls, [], "model", pricing),
            "by_unit": _group(model_calls, tool_calls, "unit_id", pricing),
            "by_cycle": _group(model_calls, tool_calls, "cycle", pricing),
        },
    }


async def collect(store, run, pricing=None):
    """Load every record a summary needs from the research store."""
    run_id = run["run_id"]
    return summarize(
        run,
        model_calls=await store.model_calls(run_id),
        tool_calls=await store.calls(run_id),
        agent_runs=await store.agent_runs(run_id),
        events=await store.activity_events(run_id),
        spans=await store.span_timings(run_id),
        unit_results=await store.unit_results(run_id),
        pricing=pricing,
    )


async def export_records(store, run, pricing=None):
    """JSONL-ready records: the summary first, then every raw measurement."""
    run_id = run["run_id"]
    yield {"record": "summary", **(await collect(store, run, pricing))}
    for kind, rows in (
        ("model_call", await store.model_calls(run_id)),
        ("tool_call", await store.calls(run_id)),
        ("agent_run", await store.agent_runs(run_id)),
        ("span", await store.span_timings(run_id)),
    ):
        for row in rows:
            yield {"record": kind, "run_id": run_id, **row}


def _percent(ratio):
    return None if ratio is None else round(ratio * 100)


TABLE_COLUMNS = [
    ("run", lambda s: (s["run_id"] or "")[:8]),
    ("status", lambda s: s["status"]),
    ("created", lambda s: (s["created_at"] or "")[:16]),
    ("wall_min", lambda s: _ratio(s["time"]["wall_seconds"], 60, 1)),
    ("active_min", lambda s: _ratio(s["time"]["active_seconds"], 60, 1)),
    ("model_calls", lambda s: s["model_calls"]["count"]),
    ("input_k", lambda s: _ratio(s["tokens"]["input"], 1000, 1)),
    ("output_k", lambda s: _ratio(s["tokens"]["output"], 1000, 1)),
    ("cache_%", lambda s: _percent(s["tokens"]["cache_read_ratio"])),
    ("cost", lambda s: s["cost"]["total"]),
    ("currency", lambda s: s["cost"]["currency"]),
    ("tool_calls", lambda s: s["tools"]["count"]),
    ("searches", lambda s: s["tools"]["searches"]),
    ("pages", lambda s: s["tools"]["pages_read"]),
    ("tool_err_%", lambda s: _percent(s["tools"]["error_rate"])),
    ("repeats", lambda s: s["tools"]["repeat_calls"]),
    ("agents", lambda s: s["agents"]["count"]),
    ("failed_agents", lambda s: s["agents"]["failed"]),
    ("citations", lambda s: s["report"]["citations"]),
    ("tokens/cite", lambda s: s["efficiency"]["tokens_per_citation"]),
    ("cost/cite", lambda s: s["efficiency"]["cost_per_citation"]),
    ("query", lambda s: " ".join((s["query"] or "").split())[:40]),
]


def main(argv=None):
    from .config import load_settings
    from .store import Store

    parser = argparse.ArgumentParser(description="Summarize DeepResearch cost and efficiency from a research data directory.")
    parser.add_argument("--data-dir", required=True, type=Path, help="Directory containing research.sqlite3 (for live runs: .deerflow/deepresearch/live-*/research)")
    parser.add_argument("--config", type=Path, help="Research YAML whose pricing is used for cost estimates")
    parser.add_argument("--run", action="append", help="Only these run IDs (repeatable)")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--format", choices=["table", "csv", "json", "jsonl"], default="table", help="jsonl exports raw records of the selected runs")
    args = parser.parse_args(argv)
    database = args.data_dir / "research.sqlite3"
    if not database.is_file():
        parser.error(f"{database} does not exist")
    pricing = load_settings(args.config).pricing if args.config else {}

    async def run():
        store = Store(database)
        await store.start()
        if args.run:
            runs = [item for item in [await store.get(run_id) for run_id in args.run] if item]
        else:
            runs = sorted(await store.list(), key=lambda item: item.get("created_at") or "", reverse=True)[: args.limit]
        if args.format == "jsonl":
            for item in runs:
                async for record in export_records(store, item, pricing):
                    print(json.dumps(record, ensure_ascii=False, default=str))
            return
        summaries = [await collect(store, item, pricing) for item in runs]
        if args.format == "json":
            print(json.dumps(summaries, ensure_ascii=False, indent=2, default=str))
            return
        rows = [[getter(summary) for _, getter in TABLE_COLUMNS] for summary in summaries]
        if args.format == "csv":
            writer = csv.writer(sys.stdout)
            writer.writerow([name for name, _ in TABLE_COLUMNS])
            writer.writerows(rows)
            return
        texts = [[name for name, _ in TABLE_COLUMNS], *[["" if value is None else str(value) for value in row] for row in rows]]
        widths = [max(len(row[index]) for row in texts) for index in range(len(TABLE_COLUMNS))]
        for row in texts:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)))

    asyncio.run(run())


if __name__ == "__main__":
    main()
