#!/usr/bin/env python3
# ruff: noqa: E501 - findings and table rows are prose; wrapping them hides what a reader compares.
"""Audit one DeepResearch run (a "session") from its research store.

Produces the facts an audit report stands on: where the time and the tokens
went, what the prompt cache served, how tools behaved, how research and report
writing proceeded, what went wrong, and which settings would change it.

It reads the store with DeepResearch's own code (``Store``, ``metrics.collect``)
so the numbers are the ones the product shows, and adds the per-thread and
per-request analysis the product does not show. Run it with the backend's
Python, from anywhere inside the repository:

    backend/.venv/bin/python .agents/skills/deepresearch-engineering/scripts/audit_run.py --list
    backend/.venv/bin/python .agents/skills/deepresearch-engineering/scripts/audit_run.py --run 244cd6d5
    ... --run <id|prefix|page URL|thread id|latest> [--baseline <run>] [--data-dir DIR] [--config research.yaml] [--out DIR] [--report FILE]

It writes ``audit-<run8>.json`` (the facts), ``audit-<run8>.md`` (the same facts
as a sheet to read) and ``audit-<run8>.html`` (the same facts as one offline
page with the timeline and charts; the written audit report is shown on top
once it exists). Nothing is written to the research store; output goes to
``--out`` (default ``<repo>/.deerflow/deepresearch/audits``, which git ignores).
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # an audit writes to --out and nowhere else, not even bytecode next to itself

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sqlite3  # noqa: E402
import zlib  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402

TOP = 10


# --------------------------------------------------------------------------
# Locating the repository, the stores and the run
# --------------------------------------------------------------------------
def find_repo(start):
    for base in [Path(start).resolve(), Path.cwd().resolve()]:
        for folder in [base, *base.parents]:
            if (folder / "backend" / "deepresearch" / "store.py").is_file():
                return folder
    return None


def databases(repo, data_dirs):
    found = []
    for item in data_dirs:
        path = Path(item).expanduser()
        found += [path] if path.is_file() else sorted(path.rglob("research.sqlite3"))
    if not data_dirs and repo is not None:
        home = repo / ".deerflow" / "deepresearch"
        if home.is_dir():
            found += sorted(path for path in home.rglob("research.sqlite3") if len(path.relative_to(home).parts) <= 3)
    return list(dict.fromkeys(path.resolve() for path in found))


def read_only(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def run_rows(path):
    """(id, body) of every run in one store; read-only, never blocks a live gateway."""
    try:
        with read_only(path) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(research_run)")]
            if "body" not in columns:
                return []
            return [(row[0], json.loads(row[1])) for row in db.execute("SELECT id, body FROM research_run")]
    except sqlite3.Error:
        return []


def resolve(reference, stores):
    """The store and full id of the run a person pointed at."""
    reference = reference.strip()
    match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", reference)
    token = match.group(0) if match else reference.rstrip("/").rsplit("/", 1)[-1]
    rows = [(path, run_id, body) for path in stores for run_id, body in run_rows(path)]
    if token == "latest":
        rows.sort(key=lambda row: row[2].get("created_at") or "")
        return rows[-1][:2] if rows else None
    hits = [row for row in rows if row[1] == token] or [row for row in rows if row[1].startswith(token) or row[2].get("thread_id") == token]
    if len(hits) > 1:
        names = ", ".join(f"{row[1][:8]} ({row[0].parent.parent.name})" for row in hits[:8])
        raise SystemExit(f"'{reference}' matches several runs: {names}. Use a longer id or --data-dir.")
    return hits[0][:2] if hits else None


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def open_store(database):
    """The product's store over a read-only connection.

    ``Store`` opens its database read-write, begins every operation with
    ``BEGIN IMMEDIATE`` (a write lock) and creates tables on start. An audit must
    neither wait for nor block a gateway that is writing research, and must work
    on a copied or write-protected store, so the same reading code runs here on
    ``mode=ro`` connections and ``start`` is skipped.
    """
    from deepresearch.store import Store

    class ReadOnlyStore(Store):
        def _call(self, fn):
            db = read_only(self.path)
            try:
                db.row_factory = sqlite3.Row
                return fn(db)
            finally:
                db.close()

    return ReadOnlyStore(Path(database))


async def load(database, run_id, pricing):
    from deepresearch.metrics import collect

    store = open_store(database)
    run = await store.get(run_id)
    profile = run.get("profile") or {}
    snapshot = await store.snapshot(profile["hash"]) if profile.get("hash") else None
    return {
        "run": run,
        "summary": await collect(store, run, pricing),
        "model_calls": await store.model_calls(run_id),
        "tool_calls": await store.calls(run_id),
        "agent_runs": await store.agent_runs(run_id),
        "events": await store.activity_events(run_id),
        "unit_results": await store.unit_results(run_id),
        "spans": await store.span_timings(run_id),
        "evidence": evidence_pool(database, run_id),
        "snapshot": snapshot,
    }


def evidence_pool(database, run_id):
    """The merged evidence pool: which steps saw each record the report may cite."""
    try:
        with read_only(database) as db:
            return {key: json.loads(body) for key, body in db.execute("SELECT id, body FROM research_evidence WHERE run_id=?", (run_id,))}
    except sqlite3.Error:
        return {}


def exchanges(database, run_id):
    """Audited requests with their message hashes (the list API leaves the hashes out)."""
    try:
        with read_only(database) as db:
            return [json.loads(body) for (body,) in db.execute("SELECT body FROM research_llm_exchange WHERE run_id=? ORDER BY rowid", (run_id,))]
    except sqlite3.Error:
        return []


def blob(database, run_id, digest):
    with read_only(database) as db:
        row = db.execute("SELECT body FROM research_llm_blob WHERE run_id=? AND hash=?", (run_id, digest)).fetchone()
    return json.loads(zlib.decompress(row[0]).decode("utf-8")) if row else None


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------
def when(value):
    return datetime.fromisoformat(value) if value else None


def ratio(part, whole, digits=3):
    return round(part / whole, digits) if whole else None


def node_of(call):
    return call.get("config_node") or call.get("purpose") or "other"


def text_of(message):
    content = (message or {}).get("content")
    if isinstance(content, str):
        return content
    return " ".join(str(part.get("text", "")) for part in content or [] if isinstance(part, dict))


def threads(model_calls):
    """Agent loops: requests of one execution, in order. A loop is where a prompt cache pays."""
    groups = defaultdict(list)
    for call in model_calls:
        if call.get("execution_id") and call.get("engine_node") in (None, "model"):
            groups[call["execution_id"]].append(call)
    rows = []
    for execution, calls in groups.items():
        calls.sort(key=lambda call: call.get("started_at") or "")
        metered = [call for call in calls if call.get("input_tokens")]
        served = checked = 0
        for before, after in zip(metered, metered[1:], strict=False):
            checked += 1
            # A provider that cached the previous request serves at least its prompt again
            # (64-token blocks): this is the wire-level proof that the prefix matched.
            served += (after.get("cache_read_tokens") or 0) >= 0.98 * (before["input_tokens"] // 64 * 64)
        gaps = [(when(after["started_at"]) - when(before["ended_at"])).total_seconds() for before, after in zip(calls, calls[1:], strict=False) if before.get("ended_at") and after.get("started_at")]
        rows.append(
            {
                "execution_id": execution,
                "node": node_of(calls[0]),
                "unit_id": calls[0].get("unit_id"),
                "skill": calls[0].get("skill"),
                "turns": len(calls),
                "first_input": metered[0]["input_tokens"] if metered else None,
                "last_input": metered[-1]["input_tokens"] if metered else None,
                "max_input": max((call["input_tokens"] for call in metered), default=None),
                "inputs": [call["input_tokens"] for call in metered],
                "input_tokens": sum(call.get("input_tokens") or 0 for call in calls),
                "output_tokens": sum(call.get("output_tokens") or 0 for call in calls),
                "cache_read_tokens": sum(call.get("cache_read_tokens") or 0 for call in calls),
                "cache_read_ratio": ratio(sum(call.get("cache_read_tokens") or 0 for call in calls), sum(call.get("input_tokens") or 0 for call in calls)),
                "prefix_reuse_ratio": ratio(sum(call.get("prefix_chars") or 0 for call in calls), sum(call.get("prompt_chars") or 0 for call in calls)) if any("prefix_chars" in call for call in calls) else None,
                "turns_checked": checked,
                "turns_served_from_cache": served,
                "model_seconds": round(sum(call.get("duration_ms") or 0 for call in calls) / 1000, 1),
                "between_turns_seconds": round(sum(gaps), 1),
                "truncated": sum(call.get("finish_reason") == "length" for call in calls),
                "errors": sum(call.get("status") not in {"ok", "running"} for call in calls),
            }
        )
    return sorted(rows, key=lambda row: -row["input_tokens"])


def admission_waits(agent_runs, model_calls):
    """Seconds each native role waited between being submitted and its first model call.

    Research starts its steps together, but the engine admits only
    ``subagent_runtime.max_running`` roles at once and queues the rest. That wait
    is inside a step's duration and in no research metric; it shows as steps
    whose first request comes minutes after their start, each beginning when an
    earlier one ends.
    """
    first = {}
    for call in model_calls:
        key = call.get("execution_id")
        if key and call.get("started_at") and (key not in first or call["started_at"] < first[key]):
            first[key] = call["started_at"]
    rows = []
    for item in agent_runs:
        key = item.get("execution_id") or item.get("id")
        if item.get("started_at") and key in first:
            wait = (when(first[key]) - when(item["started_at"])).total_seconds()
            rows.append({"execution_id": key, "node": item.get("config_node"), "unit_id": item.get("unit_id"), "wait_seconds": round(max(0.0, wait), 1)})
    return sorted(rows, key=lambda row: -row["wait_seconds"])


def failed_tool_time(tool_calls):
    """What failed tool calls cost: a timeout is paid in full before a step moves on."""
    groups = defaultdict(lambda: {"calls": 0, "seconds": 0.0, "error_types": Counter()})
    for call in tool_calls:
        if call.get("status") == "error":
            entry = groups[call.get("tool_name") or "tool"]
            entry["calls"] += 1
            entry["seconds"] += (call.get("duration_ms") or 0) / 1000
            entry["error_types"][call.get("error_type") or "ToolReturnedError"] += 1
    return [{"tool": tool, "calls": item["calls"], "seconds": round(item["seconds"], 1), "avg_seconds": round(item["seconds"] / item["calls"], 1), "error_types": dict(item["error_types"])} for tool, item in groups.items()]


def seconds_of(value):
    return when(value).timestamp() if value else None


def step_rounds(units):
    """Research round of each step: 0 for the plan, n for the supplements of the n-th gap round."""
    by_id = {unit["id"]: unit for unit in units}

    def depth(unit, seen=()):
        if not unit.get("parent_gap_id"):
            return 0
        named = re.match(r"S(\d+)-", unit["id"])
        if named:
            return int(named.group(1))
        parent = by_id.get((unit.get("depends_on") or [None])[0])
        return 1 + (depth(parent, (*seen, unit["id"])) if parent and parent["id"] not in seen else 0)

    return {unit["id"]: depth(unit) for unit in units}


def tool_waits(model_calls, tool_calls):
    """What failed tool calls cost on the clock rather than as a sum.

    The tools of one turn run together and the model continues when the slowest
    returns, so a single 35-second timeout holds a turn whose other calls took
    two. Every batch is measured as it ran, and again with each failed call
    answered in that tool's median successful time - what a working provider
    would have cost. The difference is time a step spent waiting for nothing.
    """
    answered = defaultdict(list)
    for call in tool_calls:
        if call.get("status") == "success" and call.get("duration_ms"):
            answered[call.get("tool_name")].append(call["duration_ms"] / 1000)
    typical = {tool: sorted(values)[len(values) // 2] for tool, values in answered.items()}
    turns = defaultdict(list)
    for call in model_calls:
        if call.get("execution_id") and call.get("ended_at"):
            turns[call["execution_id"]].append(seconds_of(call["ended_at"]))
    batches = defaultdict(lambda: defaultdict(list))
    for call in tool_calls:
        if call.get("execution_id") and call.get("started_at"):
            start = seconds_of(call["started_at"])
            turn = sum(1 for end in turns.get(call["execution_id"], ()) if end <= start)
            batches[call["execution_id"]][turn].append((start, (call.get("duration_ms") or 0) / 1000, call))
    rows = []
    for execution, groups in batches.items():
        waited = without = 0.0
        delayed = failed = 0
        for calls in groups.values():
            begin = min(start for start, _, _ in calls)
            actual = max(start + seconds for start, seconds, _ in calls) - begin
            repaired = max(start + (min(seconds, typical.get(call.get("tool_name"), 0.0)) if call.get("status") == "error" else seconds) for start, seconds, call in calls) - begin
            waited, without = waited + actual, without + repaired
            delayed += actual - repaired >= 1
            failed += sum(call.get("status") == "error" for _, _, call in calls)
        first = next(iter(groups.values()))[0][2]
        rows.append(
            {
                "execution_id": execution,
                "node": node_of(first),
                "unit_id": first.get("unit_id"),
                "batches": len(groups),
                "delayed_batches": delayed,
                "failed_calls": failed,
                "wait_seconds": round(waited, 1),
                "wait_seconds_without_failures": round(without, 1),
                "lost_seconds": round(waited - without, 1),
            }
        )
    return sorted(rows, key=lambda row: -row["lost_seconds"])


def finish_time(durations, slots):
    """When the last of ``durations`` ends if they start in order on ``slots`` workers."""
    free = [0.0] * max(1, slots)
    for seconds in durations:
        free.sort()
        free[0] += seconds
    return max(free)


def research_rounds(data, waits, concurrency):
    """Each research round (the plan, then every supplement round): what it cost and what it gave the report.

    A supplement round is only worth its minutes if the report cites what it
    found, so cost (time, tokens, tool calls) stands next to yield (findings,
    cited evidence, sources first seen in that round).
    """
    run = data["run"]
    units = run.get("units") or []
    rounds = step_rounds(units)
    lost = {row["unit_id"]: row["lost_seconds"] for row in waits if row["node"] == "research"}
    results = {item["result"].get("unit_id"): item["result"] for item in data["unit_results"] if isinstance(item.get("result"), dict)}
    spans = {}
    for item in data["agent_runs"]:
        if item.get("unit_id") in rounds and item.get("config_node") in (None, "research") and item.get("started_at"):
            start, end = seconds_of(item["started_at"]), seconds_of(item.get("ended_at")) or seconds_of(item["started_at"]) + (item.get("duration_ms") or 0) / 1000
            known = spans.get(item["unit_id"])
            spans[item["unit_id"]] = (min(start, known[0]), max(end, known[1])) if known else (start, end)
    citations = citation_rounds(run, data.get("evidence") or {}, rounds)
    cited = cited_findings(units, results, rounds, citations)
    rows = []
    for number in sorted(set(rounds.values())):
        members = [unit["id"] for unit in units if rounds[unit["id"]] == number]
        calls = [call for call in data["model_calls"] if call.get("unit_id") in members]
        tools = [call for call in data["tool_calls"] if call.get("unit_id") in members]
        timed = sorted((spans[unit] for unit in members if unit in spans), key=lambda span: span[0])
        ends = [seconds_of(call["ended_at"]) for call in calls if call.get("ended_at")]
        wall = (max([end for _, end in timed] + ends) - timed[0][0]) if timed else None
        ordered = sorted((unit for unit in members if unit in spans), key=lambda unit: spans[unit][0])
        slots = concurrency or len(ordered) or 1
        as_run = finish_time([spans[unit][1] - spans[unit][0] for unit in ordered], slots)
        repaired = finish_time([max(0.0, spans[unit][1] - spans[unit][0] - lost.get(unit, 0.0)) for unit in ordered], slots)
        rows.append(
            {
                "round": number,
                "units": members,
                "wall_seconds": round(wall, 1) if wall is not None else None,
                # An estimate: the round replayed on the same number of workers without the waiting that failed calls caused.
                "estimated_wall_seconds_without_failed_calls": round(wall * repaired / as_run, 1) if wall and as_run else None,
                "model_calls": len(calls),
                "input_tokens": sum(call.get("input_tokens") or 0 for call in calls),
                "output_tokens": sum(call.get("output_tokens") or 0 for call in calls),
                "cache_read_tokens": sum(call.get("cache_read_tokens") or 0 for call in calls),
                "tool_calls": len(tools),
                "tool_errors": sum(call.get("status") == "error" for call in tools),
                "searches": sum(call.get("role") == "search" for call in tools),
                "reads": sum(call.get("role") == "read" for call in tools),
                "findings": sum(len(results.get(unit, {}).get("findings") or []) for unit in members),
                "open_questions": sum(len(results.get(unit, {}).get("open_questions") or []) for unit in members),
                "cited_findings": sum(cited[unit] for unit in members) if cited is not None else None,
                "evidence_first_seen": citations["pool"].get(number, 0) if citations else None,
                "cited_evidence_first_seen": citations["evidence"].get(number, 0) if citations else None,
                "cited_sources_first_seen": citations["sources"].get(number, 0) if citations else None,
            }
        )
    return rows


def citation_rounds(run, evidence, rounds):
    """The round that first saw each evidence record, for the pool and for what the report cites."""
    marks = (run.get("report") or {}).get("citation_map") or {}
    if not evidence or not marks:
        return None
    first = {}
    for key, record in evidence.items():
        seen = [rounds[unit] for unit in record.get("unit_ids") or [] if unit in rounds]
        if seen:
            first[key] = min(seen)
    sources = {}
    for key, number in marks.items():
        if key in first:
            sources[number] = min(first[key], sources.get(number, first[key]))
    return {"pool": dict(Counter(first.values())), "evidence": dict(Counter(first[key] for key in marks if key in first)), "sources": dict(Counter(sources.values())), "cited_ids": sorted(key for key in marks if key in first)}


def cited_findings(units, results, rounds, citations):
    """Findings per step that the report cites, from the product's own evidence merge.

    Findings point at a step's raw records; only the merge maps those to the
    ids a report cites. The merge is deterministic (same ordered input, same
    ids), so it is replayed round by round; ``None`` when it cannot be (records
    the current contract rejects, another cycle's pool) rather than a guess.
    """
    if not citations:
        return None
    try:
        from deepresearch.evidence import merge_results, valid_result

        pool, findings = None, []
        for number in sorted(set(rounds.values())):
            batch = [valid_result(results[unit["id"]])[0] for unit in units if rounds[unit["id"]] <= number and unit["id"] in results]
            pool, findings, _ = merge_results(batch, pool)
        marked = set(citations["cited_ids"])
        if not marked <= set(pool):
            return None
        counts = Counter(finding["unit_id"] for finding in findings if marked & set(finding["evidence_ids"]))
        return {unit["id"]: counts.get(unit["id"], 0) for unit in units}
    except Exception:  # noqa: BLE001 - an audit reports "not measured" instead of failing on a record it cannot replay
        return None


def phase_view(summary, tool_calls):
    """Every workflow stage with its time, tokens and tool use side by side."""
    metered = {row.get("key"): row for row in summary["breakdown"].get("by_phase") or []}
    roles = defaultdict(Counter)
    for call in tool_calls:
        entry = roles[call.get("phase")]
        entry[call.get("role") or "other"] += 1
        entry[(call.get("role") or "other") + "_errors"] += call.get("status") == "error"
    rows, seen = [], set()
    for phase in summary["time"].get("phases") or []:
        name = phase["phase"]
        seen.add(name)
        row = metered.get(name, {})
        rows.append(
            {
                "phase": name,
                "seconds": phase.get("seconds"),
                "runs": phase.get("runs"),
                "errors": phase.get("errors"),
                "model_calls": row.get("model_calls") or 0,
                "model_seconds": round((row.get("model_ms") or 0) / 1000, 1),
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "cache_read_tokens": row.get("cache_read_tokens"),
                "tool_calls": row.get("tool_calls") or 0,
                "tool_errors": row.get("tool_errors") or 0,
                "tool_seconds": round((row.get("tool_ms") or 0) / 1000, 1),
                "searches": roles[name]["search"],
                "failed_searches": roles[name]["search_errors"],
                "reads": roles[name]["read"],
                "failed_reads": roles[name]["read_errors"],
            }
        )
    return rows


def search_view(tool_calls):
    """Searching and reading per step, and how long an answered and a failed search take."""
    steps = defaultdict(Counter)
    answered, failed = [], []
    for call in tool_calls:
        role, entry = call.get("role") or "other", steps[call.get("unit_id")]
        entry[role] += 1
        if call.get("status") == "error":
            entry[role + "_errors"] += 1
        if role == "search" and call.get("duration_ms"):
            (failed if call.get("status") == "error" else answered).append(call["duration_ms"] / 1000)
    edges = (2, 5, 10, 20, 30)
    labels = ["<2s", "2–5s", "5–10s", "10–20s", "20–30s", "≥30s"]

    def histogram(values):
        counts = [0] * len(labels)
        for value in values:
            counts[sum(value >= edge for edge in edges)] += 1
        return counts

    def middle(values, part):
        return round(sorted(values)[min(len(values) - 1, int(len(values) * part))], 1) if values else None

    return {
        "by_step": [{"unit_id": unit, "searches": entry["search"], "failed_searches": entry["search_errors"], "reads": entry["read"], "failed_reads": entry["read_errors"]} for unit, entry in steps.items()],
        "latency_buckets": labels,
        "answered_searches": histogram(answered),
        "failed_searches": histogram(failed),
        "answered_p50_seconds": middle(answered, 0.5),
        "answered_p90_seconds": middle(answered, 0.9),
    }


def tool_log(tool_calls, origin):
    """Every tool call in order: the page shows it per step, an auditor greps it."""
    rows = []
    for call in sorted(tool_calls, key=lambda item: item.get("started_at") or ""):
        attempts = " ".join(f"{item.get('provider')}:{item.get('status')}" + (f"/{item['kind']}" if item.get("kind") else "") for item in call.get("attempts") or [])
        rows.append(
            {
                "at": round(seconds_of(call["started_at"]) - origin, 1) if call.get("started_at") and origin else None,
                "unit_id": call.get("unit_id"),
                "tool": call.get("tool_name"),
                "role": call.get("role"),
                "status": call.get("status"),
                "seconds": round((call.get("duration_ms") or 0) / 1000, 1),
                "error_type": call.get("error_type"),
                "attempts": attempts,
                "target": " ".join(str(call.get("query") or call.get("url") or "").split())[:200],
                "output_chars": call.get("output_chars"),
            }
        )
    return rows


def timeline(data, rounds, origin):
    """Offsets in seconds from the run's creation: workflow stages, then every role with its model and tool calls."""
    if not origin:
        return None
    stages = []
    for span in data.get("spans") or []:
        if span.get("kind") == "node" and span.get("at"):
            end = seconds_of(span["at"]) - origin
            stages.append({"name": span.get("name"), "start": round(end - (span.get("duration_ms") or 0) / 1000, 1), "end": round(end, 1), "status": span.get("status")})
    models, tools = defaultdict(list), defaultdict(list)
    for call in data["model_calls"]:
        if call.get("execution_id") and call.get("started_at") and call.get("duration_ms"):
            start = seconds_of(call["started_at"]) - origin
            models[call["execution_id"]].append([round(start, 1), round(start + call["duration_ms"] / 1000, 1)])
    for call in data["tool_calls"]:
        if call.get("execution_id") and call.get("started_at"):
            start = seconds_of(call["started_at"]) - origin
            tools[call["execution_id"]].append([round(start, 1), round(start + (call.get("duration_ms") or 0) / 1000, 1), call.get("role") or "other", call.get("status") != "error"])
    roles = []
    for item in sorted(data["agent_runs"], key=lambda entry: entry.get("started_at") or ""):
        if not item.get("started_at"):
            continue
        key = item.get("execution_id") or item.get("id")
        start = seconds_of(item["started_at"]) - origin
        end = seconds_of(item["ended_at"]) - origin if item.get("ended_at") else start + (item.get("duration_ms") or 0) / 1000
        roles.append(
            {
                "execution_id": key,
                "node": item.get("config_node"),
                "unit_id": item.get("unit_id"),
                "round": rounds.get(item.get("unit_id")),
                "start": round(start, 1),
                "end": round(end, 1),
                "status": item.get("status"),
                "model": models.get(key, []),
                "tools": tools.get(key, []),
            }
        )
    finish = max([stage["end"] for stage in stages] + [role["end"] for role in roles], default=0)
    return {"origin": data["run"].get("created_at"), "end": round(finish, 1), "stages": sorted(stages, key=lambda stage: stage["start"]), "roles": roles}


def repeat_calls(model_calls):
    """Model calls a node spent on doing a task again: the real cost of retries and repairs.

    The product counts retry and repair *events*, and a repair that still fails
    its check is recorded once more without another model call. For single-shot
    tasks (a conversion, an outline, a section) every call after the first of the
    same task is a repeat; agent loops with tools are not tasks of this kind.
    """
    seen, repeats = Counter(), Counter()
    for call in sorted(model_calls, key=lambda item: item.get("started_at") or ""):
        node = node_of(call)
        if node == "research" or call.get("engine_node") not in (None, "model", "dispatch", "rewrite") and call.get("purpose") == "agent":
            continue
        key = (node, call.get("unit_id") or call.get("contract"), call.get("cycle"))
        seen[key] += 1
        if seen[key] > 1:
            repeats[node] += 1
    return dict(repeats)


def slowest(model_calls, count=TOP):
    rows = []
    for call in sorted((c for c in model_calls if c.get("duration_ms")), key=lambda c: -c["duration_ms"])[:count]:
        seconds = call["duration_ms"] / 1000
        rows.append(
            {
                "call_id": call.get("id"),
                "node": node_of(call),
                "unit_id": call.get("unit_id"),
                "seconds": round(seconds, 1),
                "input_tokens": call.get("input_tokens"),
                "cache_read_tokens": call.get("cache_read_tokens"),
                "output_tokens": call.get("output_tokens"),
                "output_tokens_per_second": round(call["output_tokens"] / seconds, 1) if call.get("output_tokens") and seconds else None,
                "finish_reason": call.get("finish_reason"),
                "status": call.get("status"),
            }
        )
    return rows


def speed_by_node(model_calls):
    """Output speed including prefill: the number a slow self-hosted model is felt by."""
    groups = defaultdict(lambda: [0, 0.0, 0])
    for call in model_calls:
        if call.get("status") == "ok" and call.get("output_tokens") and call.get("duration_ms"):
            entry = groups[node_of(call)]
            entry[0] += call["output_tokens"]
            entry[1] += call["duration_ms"] / 1000
            entry[2] += 1
    return [{"node": node, "calls": calls, "output_tokens": tokens, "seconds": round(seconds, 1), "output_tokens_per_second": round(tokens / seconds, 1)} for node, (tokens, seconds, calls) in groups.items() if seconds]


def prefix_breaks(database, run_id, records, count=TOP):
    """Requests whose conversation no longer starts like the previous one of its thread.

    An agent loop should only append. Anything else (a message inserted or
    rewritten near the head) makes every later token a cache miss; compaction is
    the one legitimate cause and is labelled.
    """
    groups = defaultdict(list)
    for record in records:
        if record.get("message_hashes") and record.get("group"):
            groups[(record["group"], record.get("node"))].append(record)
    breaks, turns = [], 0
    for (_, engine_node), items in groups.items():
        for before, after in zip(items, items[1:], strict=False):
            turns += 1
            old, new = before["message_hashes"], after["message_hashes"]
            shared = next((index for index, (a, b) in enumerate(zip(old, new, strict=False)) if a != b), min(len(old), len(new)))
            if shared >= len(old):
                continue
            entry = {
                "call_id": after.get("id"),
                "node": after.get("config_node") or after.get("purpose"),
                "engine_node": engine_node,
                "unit_id": after.get("unit_id"),
                "shared_messages": shared,
                "previous_messages": len(old),
                "messages": len(new),
            }
            if len(breaks) < count:
                was, now = blob(database, run_id, old[shared]), blob(database, run_id, new[shared]) if shared < len(new) else None
                entry.update(previous_role=(was or {}).get("role"), role=(now or {}).get("role"), previous_preview=" ".join(text_of(was).split())[:160], preview=" ".join(text_of(now).split())[:160])
                entry["likely_compaction"] = "summary" in ((now or {}).get("name") or "") or len(new) < len(old)
            breaks.append(entry)
    return {"turns_compared": turns, "breaks": len(breaks), "examples": breaks[:count]}


def process(data):
    run, events = data["run"], data["events"]
    by_type = defaultdict(list)
    for event in events:
        by_type[event["type"]].append(event)
    results = {item["result"].get("unit_id"): item["result"] for item in data["unit_results"] if isinstance(item.get("result"), dict)}
    agent_by_unit = defaultdict(list)
    for item in data["agent_runs"]:
        agent_by_unit[item.get("unit_id")].append(item)
    units = []
    for unit in run.get("units") or []:
        result = results.get(unit["id"], {})
        runs = [item for item in agent_by_unit.get(unit["id"], []) if item.get("config_node") in (None, "research")]
        units.append(
            {
                "unit_id": unit["id"],
                "title": unit.get("title"),
                "skill": unit.get("skill"),
                "supplement_of": (unit.get("depends_on") or [None])[0] if unit.get("parent_gap_id") else None,
                "gap": unit.get("parent_gap_id"),
                "status": (run.get("unit_statuses") or {}).get(unit["id"]),
                "stop_reason": next((item.get("stop_reason") for item in runs if item.get("stop_reason")), None),
                "error_code": next((item.get("error_code") for item in runs if item.get("error_code")), None),
                "seconds": round(sum(item.get("duration_ms") or 0 for item in runs) / 1000, 1),
                "findings": len(result.get("findings") or []),
                "raw_evidences": len(result.get("raw_evidences") or []),
                "open_questions": len(result.get("open_questions") or []),
                "limitations": len(result.get("limitations") or []),
            }
        )
    repairs = Counter(str((event.get("data") or {}).get("task") or "") for event in by_type["report.draft.repair"])
    return {
        "iterations": run.get("iteration", 0),
        "gap_rounds": [{"at": event["at"], "codes": dict(Counter(gap.get("code") for gap in (event.get("data") or {}).get("gaps", [])))} for event in by_type["validator.gap_found"]],
        "open_gaps": [{"unit_id": gap.get("unit_id"), "code": gap.get("code")} for gap in run.get("gaps") or []],
        "limited_report": [event.get("data") for event in by_type["report.limitations.auto"]],
        "search_limits": [event.get("data") for event in by_type["research.search.limited"]],
        "evidence_trimmed": [event.get("data") for event in by_type["research.evidence.trimmed"]],
        "output_retries": [event.get("data") for event in by_type["research.output.retry"]],
        "pruned_outputs": [event.get("data") for event in by_type["research.output.pruned"]],
        "draft_repairs": dict(repairs),
        "deferred_supplements": [event.get("data") for event in by_type["research.supplement.deferred"]],
        "units": units,
        "event_counts": dict(Counter(event["type"] for event in events)),
    }


SETTING_KEYS = (
    "default_model",
    "rewrite_model",
    "extraction_model",
    "nodes",
    "max_concurrency",
    "writer_concurrency",
    "plan_min_units",
    "plan_max_units",
    "max_searches_per_unit",
    "max_seconds_per_unit",
    "max_findings_per_unit",
    "max_output_tokens",
    "output_retries",
    "max_synthesis_repairs",
    "max_report_sections",
    "report_length_scale",
    "report_time_reserve_seconds",
    "supplement_gap_codes",
    "allow_limited_report",
    "require_dual_source",
    "cite_search_results",
    "compaction",
    "tool_receipt_ledger",
    "engine_tools",
)


def settings_view(snapshot):
    """The tunables the run actually used; credentials and prompt text stay out."""
    if not snapshot:
        return None
    view = {key: snapshot[key] for key in SETTING_KEYS if key in snapshot}
    models = []
    for model in snapshot.get("models") or []:
        host = re.sub(r"^(https?://[^/]+).*", r"\1", model.get("base_url") or "") or None
        models.append(
            {key: model.get(key) for key in ("name", "provider", "model", "max_tokens", "context_window", "temperature", "stream_usage", "max_tokens_param", "session_param", "session_header", "supports_thinking")} | {"base_url_host": host}
        )
    view["models"] = models
    view["sources"] = [
        {
            "name": source.get("name"),
            "tool": source.get("tool"),
            "role": source.get("role"),
            "origin": source.get("origin"),
            "kind": source.get("kind"),
            "providers": [provider.get("type") or provider.get("id") for provider in source.get("providers") or []],
        }
        for source in snapshot.get("sources") or []
    ]
    view["mcp_servers"] = sorted((snapshot.get("mcp_servers") or {}).keys())
    defaults = None
    try:
        from deepresearch.prompts import PromptSet

        defaults = PromptSet().model_dump()
    except Exception:  # noqa: BLE001 - the audit works without knowing prompt defaults
        pass
    prompts = snapshot.get("prompts") or {}
    view["prompts_differing_from_current_defaults"] = sorted(name for name, text in prompts.items() if defaults is not None and defaults.get(name) != text)
    return view


# --------------------------------------------------------------------------
# Findings: what deserves attention, with the setting that changes it
# --------------------------------------------------------------------------
def lost_on_the_clock(facts):
    lost = round(sum(row["lost_seconds"] for row in facts.get("tool_waits") or []))
    if lost < 30:
        return ""
    rounds = [row for row in facts.get("rounds") or [] if row.get("wall_seconds") and row.get("estimated_wall_seconds_without_failed_calls")]
    text = f" 按每轮工具批次重算：各步骤合计多等了 {lost} 秒"
    if rounds:
        text += "；推算" + "、".join(f"第 {row['round']} 轮 {round(row['wall_seconds'])} → {round(row['estimated_wall_seconds_without_failed_calls'])} 秒" for row in rounds) + "（失败的调用按该工具成功调用的中位耗时返回）"
    return text + "。"


def findings(facts):
    summary, out = facts["summary"], []

    def add(severity, code, title, evidence, advice):
        out.append({"severity": severity, "code": code, "title": title, "evidence": evidence, "advice": advice})

    run = facts["identity"]
    if run["status"] == "FAILED":
        add("high", "run-failed", f"研究失败：{(run.get('error') or {}).get('code')}", (run.get("error") or {}).get("message"), "对照 references/debugging.md 的错误码表定位；recoverable 的错误可以从检查点重试。")
    # Only answered calls count: a call that failed or was interrupted has no usage to report.
    unreported = sum(row.get("unreported_usage") or 0 for row in summary["breakdown"]["by_node"])
    if unreported:
        add(
            "high",
            "usage-unreported",
            f"{unreported} 次成功的模型调用没有上报 Token 用量",
            "Token、费用、缓存指标不完整，预算按满额输出上限保守扣减。",
            "模型 stream_usage 设为 true；网关不支持 stream_options 时只能看耗时与可复用前缀。",
        )
    nodes = {row["node"]: row for row in summary["breakdown"]["by_node"]}
    for name, row in nodes.items():
        if row.get("truncated"):
            add(
                "high",
                "truncated",
                f"节点 {name} 有 {row['truncated']} 次输出被截断（finish_reason=length）",
                f"平均输出 {row.get('avg_output_tokens')} token",
                f"调大 nodes.{name}.max_tokens；写作节点也可以调小 report_length_scale。截断的 JSON 会触发重试，截断的章节会丢内容。",
            )
        if row.get("retries"):
            again = facts["repeat_calls"].get(name, 0)
            level = "medium" if again >= max(2, row["model_calls"] // 4) else "low"
            add(
                level,
                "format-retries",
                f"节点 {name}：{row['model_calls']} 次调用里有 {again} 次是重做（重试/修复事件 {row['retries']} 条）",
                "重做的调用才是成本；事件数会多于调用数：修复后仍不合格会再记一条事件，但不再调用模型。",
                f"先看是哪一条校验失败（LLM 调用审计里重试那次的最后一条 user 消息）；再考虑 nodes.{name}.temperature 调低、直接调用节点开 json_mode、换更守格式的模型，或修改对应提示词。",
            )
        if row.get("model_errors"):
            add(
                "medium",
                "model-errors",
                f"节点 {name} 有 {row['model_errors']} 次模型调用出错",
                json.dumps(summary["model_calls"].get("error_codes"), ensure_ascii=False),
                "看错误码：限流/超时调 max_retries、timeout_seconds 或降低并发；鉴权类检查凭据引用。",
            )
    research = nodes.get("research")
    loops = [row for row in facts["threads"] if row["node"] == "research" and row["turns"] >= 3]
    if research and loops:
        prefix = research.get("prefix_reuse_ratio")
        cache = research.get("cache_read_ratio")
        if prefix is not None and prefix < 0.7:
            add(
                "high",
                "prefix-rewritten",
                f"研究循环的可复用前缀只有 {prefix:.0%}",
                f"前缀断点 {facts['prompt_cache']['prefix_breaks']['breaks']} 处，见 prompt_cache.prefix_breaks.examples",
                "有东西在改写对话头部（插入/重写消息）。检查 tool_receipt_ledger 是否被打开、压缩是否过于频繁、是否有新的中间件或载荷字段放在了前面。",
            )
        elif prefix is not None and cache is not None and cache < 0.5 * prefix:
            add(
                "medium",
                "provider-not-caching",
                f"请求可复用前缀 {prefix:.0%}，但供应商只命中 {cache:.0%}",
                "请求构造没问题，缓存没被服务端用上。",
                "确认推理服务开启 prefix caching；网关多副本时给模型配置 session_param / session_header 做会话粘性；两轮间隔过长也会过期。",
            )
    tools = summary["tools"]
    if tools.get("count") and (tools.get("error_rate") or 0) >= 0.2:
        add(
            "medium",
            "tool-errors",
            f"工具调用错误率 {tools['error_rate']:.0%}（{tools['errors']}/{tools['count']}）",
            json.dumps(tools.get("error_types"), ensure_ascii=False),
            "按错误类型处理：HTTP 429/432 给供应商配 key 或换供应商顺序；ConnectError 看网络与代理；failing_domains 里的站点考虑排除。",
        )
    time = summary["time"]
    latency = tools.get("latency_ms") or {}
    if tools.get("count") and ((time.get("tool_seconds") or 0) > (time.get("model_seconds") or 0) or (latency.get("p95") or 0) >= 20000):
        add(
            "medium",
            "slow-tools",
            f"工具耗时 {time.get('tool_seconds')} 秒，超过模型耗时 {time.get('model_seconds')} 秒（工具 P50 {fmt((latency.get('p50') or 0) / 1000, 's')} / P95 {fmt((latency.get('p95') or 0) / 1000, 's')}）",
            f"供应商切换 {tools.get('failovers')} 次；见第 5 节按供应商的表，以及 Agent 循环表的“轮间秒”。",
            "时间花在等工具而不是等模型：看“失败的工具调用耗时”和按供应商的表，先处理超时与失败的供应商（供应商的 timeout_seconds、顺序、凭据）。换更快的模型对这次运行帮助不大。",
        )
    waited = [row for row in facts["admission_waits"] if row["wait_seconds"] >= 20]
    if waited:
        add(
            "high",
            "engine-queue",
            f"{len(waited)} 个角色在引擎里排队后才开始，最长等了 {waited[0]['wait_seconds']} 秒（合计 {round(sum(row['wait_seconds'] for row in waited))} 秒）",
            "；".join(f"{row['unit_id']} {row['wait_seconds']}s" for row in waited[:6]) + "。底稿的“排队秒”只量研究侧的信号量，看不到这条队；步骤表里的秒数包含它。",
            "引擎同时运行的原生角色数由引擎配置的 subagent_runtime.max_running 决定（宿主默认 3；验收目录里是 research.yaml 旁边的 host.yaml），超出的排队，等过 queue_timeout_seconds（默认 300）会直接失败。把它调到不小于 max(max_concurrency, writer_concurrency) 并重启网关；暂时不能重启就把这两个并发数降到 max_running。`python -m deepresearch.doctor` 会报告这个不匹配。",
        )
    for row in facts["failed_tool_time"]:
        if row["calls"] >= 3 and row["seconds"] >= 0.1 * max(time.get("tool_seconds") or 0, 1):
            add(
                "medium",
                "failed-tool-time",
                f"{row['tool']} 失败的 {row['calls']} 次调用花了 {row['seconds']} 秒（平均 {row['avg_seconds']} 秒/次）",
                json.dumps(row["error_types"], ensure_ascii=False) + "；同一轮并行的其他工具即使早回来，也要等它。失败的搜索同样计入每步的 max_searches_per_unit。" + lost_on_the_clock(facts),
                "平均耗时接近超时值说明是超时：在该数据源对应的供应商上设 timeout_seconds（sources[].providers[].timeout_seconds，默认 30；旧字段 tool_timeout_seconds 已不生效），并把更可靠的供应商排在前面。",
            )
    dead = [row for row in tools.get("by_provider") or [] if row.get("attempts", 0) >= 3 and not row.get("answered")]
    if dead:
        add(
            "high" if len(dead) < len(tools.get("by_provider") or []) else "medium",
            "provider-failing",
            "供应商从未成功：" + "、".join(f"{row['tool']}/{row['provider']}" for row in dead),
            "；".join(f"{row['provider']} {json.dumps(row.get('error_kinds'), ensure_ascii=False)}" for row in dead),
            "auth=凭据无效或未配置，quota=额度用完，network/timeout=不可达。失败几次后供应商会进入冷却被跳过（表里的“跳过”），所以它们自身的耗时通常不大；真正的代价是流量全部落到排在后面的兜底供应商上——看它的错误与延迟。修好凭据，或从该数据源的 providers 里移除。设置页“数据源”里可以逐个“测试”。",
        )
    if (summary["efficiency"].get("repeat_tool_call_ratio") or 0) >= 0.15:
        add(
            "low",
            "repeat-tools",
            f"重复工具调用占 {summary['efficiency']['repeat_tool_call_ratio']:.0%}",
            f"同一 Agent 内重复 {tools.get('repeat_calls_same_agent')} 次",
            "同一 Agent 内重复通常是上下文被压缩后忘了做过；跨 Agent 重复说明计划的步骤重叠，收紧 plan 提示词或减少步骤数。",
        )
    if time.get("active_seconds") and (time.get("queue_seconds") or 0) >= 0.15 * time["active_seconds"]:
        add(
            "medium",
            "queueing",
            f"排队等待累计 {time['queue_seconds']} 秒",
            f"活跃时长 {time['active_seconds']} 秒；最大并行 {summary['agents'].get('max_parallel')}",
            "模型服务扛得住就调大 max_concurrency / writer_concurrency；扛不住就减少 plan_max_units。",
        )
    proc = facts["process"]
    supplements = [unit for unit in proc["units"] if unit["supplement_of"]]
    if supplements:
        # A gap id is "<original unit id>-<code>"; the supplement names the unit it extends.
        codes = Counter((unit["gap"] or "")[len(unit["supplement_of"]) + 1 :] or "?" for unit in supplements)
        seconds = round(sum(unit["seconds"] for unit in supplements))
        level = "medium" if seconds > 0.25 * (time.get("active_seconds") or 1) else "low"
        add(
            level,
            "supplements",
            f"补研了 {len(supplements)} 个步骤，耗时约 {seconds} 秒",
            "触发原因：" + json.dumps(dict(codes), ensure_ascii=False),
            "慢模型下把 supplement_gap_codes 收到 [coverage, unsupported]，或把预算的 max_iterations 设为 0/1；open-questions 几乎总会触发。",
        )
        later = [row for row in facts.get("rounds") or [] if row["round"] > 0]
        sources = sum(row["cited_sources_first_seen"] or 0 for row in facts.get("rounds") or [])
        if later and sources and all(row["cited_sources_first_seen"] is not None for row in later):
            gained, wall = sum(row["cited_sources_first_seen"] for row in later), sum(row["wall_seconds"] or 0 for row in later)
            share = wall / (time.get("active_seconds") or wall or 1)
            add(
                "medium" if share >= 0.25 and gained / sources < 0.5 * share else "info",
                "supplement-yield",
                f"补研轮用了 {round(wall)} 秒（活跃时长的 {share:.0%}），报告引用的来源里 {gained}/{sources} 个是补研轮才第一次读到的",
                "；".join(f"第 {row['round']} 轮：发现 {row['findings']} 条（被引用 {fmt(row['cited_findings'])}），未解问题 {row['open_questions']} 条，被引用的新证据 {row['cited_evidence_first_seen']} 条" for row in facts["rounds"]),
                "产出占比远低于时间占比时补研不值：收紧 supplement_gap_codes。产出相当时它是篇幅换时间的选择。未解问题补研后没有减少，说明补研在追研究员自己列的问题，不一定是用户要的内容——对照请求检查报告是否真的交付了（见 references/audit-report.md）。",
            )
    lost = [item for item in proc.get("pruned_outputs") or [] if item and (item.get("findings") or item.get("references"))]
    if lost:
        add(
            "high" if run["status"] == "FAILED" else "medium",
            "findings-pruned",
            f"结果转换时裁掉了 {sum(item.get('findings') or 0 for item in lost)} 条结论、{sum(item.get('references') or 0 for item in lost)} 处引用：它们指向的证据不存在或不可引用",
            "；".join(f"{item.get('unit_id')}：结论 {item.get('findings') or 0}、引用 {item.get('references') or 0}" for item in lost[:8]),
            "研究员读到了内容，但转换节点没能把笔记对到可引用的证据上。打开该步的 conversion 调用看 observed_calls：条目没有 url / title（直接暴露的工具没被识别成页面或记录）、或只有“发现的链接”而搜索结果不可引用。检查数据源的 role 是否如实（能打开原文的是 read），没有读取工具时 cite_search_results 会自动生效；全部被裁掉时运行以 NO_EVIDENCE 结束。",
        )
    cap = (facts.get("settings") or {}).get("max_findings_per_unit")
    capped = [unit["unit_id"] for unit in proc["units"] if cap and unit["findings"] >= cap]
    if cap and len(capped) >= max(2, len(proc["units"]) // 2):
        add(
            "low",
            "findings-capped",
            f"{len(capped)}/{len(proc['units'])} 个步骤的发现数顶到了 max_findings_per_unit={cap}",
            "、".join(capped[:8]),
            "上限生效时，笔记里多出来的结论不会交给写作。打开该步最后一轮研究调用和对应的 conversion 调用，对比笔记与发现；“逐项对比”这类每项一条结论的请求需要更大的上限，或把步骤拆细。",
        )
    stopped = [unit for unit in proc["units"] if unit["stop_reason"]]
    starved = [unit for unit in stopped if unit["stop_reason"] == "token_capped" and not unit["findings"]]
    if starved and len(starved) >= max(1, len(proc["units"]) // 2):
        add(
            "high",
            "budget-too-small",
            f"{len(starved)} 个步骤一开始就被 Token 预算收尾，没有产出任何发现",
            f"预算 {json.dumps(run.get('budget'), ensure_ascii=False)}；每步份额 = (max_model_tokens − 报告预留[上限的 1/5，至少 6 万，至多一半] − 已用) ÷ 并行步骤数 − 8192，而研究员第一轮请求本身就要约 3k Token",
            "份额小于第一轮请求时，引擎在第一轮就去掉工具调用（工具调用 0 次、每步只有 1 轮、finish_reason=tool_calls），与检索工具无关。调大 budget.max_model_tokens（不限传 null）或降低 max_concurrency；max_output_tokens 不影响这个份额。用接口创建任务时省略 budget 会落到默认的 120000。预算类失败重试无效（预算与已用量不变），要用新预算新建研究。",
        )
    if stopped:
        add(
            "medium",
            "steps-capped",
            f"{len(stopped)} 个研究步骤被提前收尾",
            json.dumps({unit["unit_id"]: unit["stop_reason"] for unit in stopped}, ensure_ascii=False),
            "token_capped 调大 Token 预算或减少并行步骤数；turn_capped 调角色 max_turns；loop_capped 说明模型在重复同类调用，看该步骤的工具错误。",
        )
    empty = [unit["unit_id"] for unit in proc["units"] if unit["status"] == "COMPLETED" and not unit["findings"]]
    if empty:
        add("medium", "empty-units", f"{len(empty)} 个步骤完成但没有任何发现", ", ".join(empty), "看该步骤的研究笔记与笔记整理调用：常见原因是工具全部失败、检索结果不可引用（cite_search_results）或转换丢弃了无证据的结论。")
    if proc["search_limits"]:
        # The same event reports every reason a step's searching was wound down.
        scopes = Counter((item or {}).get("scope") or "step" for item in proc["search_limits"])
        advice = {
            "step": "step=用完 max_searches_per_unit：确实需要更多检索就调大，否则说明步骤目标过宽，收紧计划",
            "time": "time=到了每步软时限 max_seconds_per_unit / 研究时间预算",
            "run": "run=整次研究的 max_tool_calls 用完",
        }
        add(
            "low",
            "search-budget",
            f"{len(proc['search_limits'])} 次检索被预算收尾：{json.dumps(dict(scopes), ensure_ascii=False)}",
            json.dumps(proc["search_limits"][:3], ensure_ascii=False),
            "；".join(text for scope, text in advice.items() if scope in scopes) or "看事件里的 scope。",
        )
    windows = {model["name"]: model.get("context_window") for model in (facts.get("settings") or {}).get("models", [])}
    window = next((value for value in windows.values() if value), None)
    peak = summary["model_calls"].get("max_input_tokens")
    if window and peak and peak >= 0.8 * window:
        add("medium", "context-pressure", f"最大上下文 {peak} 已达模型窗口 {window} 的 {peak / window:.0%}", "接近窗口会触发压缩或直接报错。", "调低 compaction.trigger_fraction、调小 max_searches_per_unit，或给研究节点换更大窗口的模型。")
    # Direct calls carry the workflow node as engine_node; only an agent's own
    # calls from another engine node (the summarization middleware) are compaction.
    compactions = sum(1 for call in facts["_model_calls"] if call.get("purpose") == "agent" and call.get("engine_node") not in (None, "model"))
    if compactions:
        add(
            "low",
            "compaction",
            f"发生了 {compactions} 次上下文压缩调用",
            "每次压缩是一次额外的长输入调用，并让该会话的提示词缓存失效一轮。",
            "压缩频繁时调大 compaction.trigger_fraction 或调小 keep_fraction；确认模型声明了 context_window。",
        )
    if (summary["efficiency"].get("conversion_token_share") or 0) >= 0.15:
        add("low", "conversion-share", f"笔记整理占总 Token 的 {summary['efficiency']['conversion_token_share']:.0%}", "", "调小 max_findings_per_unit，或给 nodes.conversion 指定更便宜的模型。")
    if (summary["efficiency"].get("failed_agent_token_share") or 0) >= 0.1:
        add(
            "medium",
            "failed-agent-tokens",
            f"失败的子 Agent 消耗了 {summary['efficiency']['failed_agent_token_share']:.0%} 的 Token",
            json.dumps(summary["agents"].get("failure_codes"), ensure_ascii=False),
            "按失败码处理；超时类调 nodes.research.timeout_seconds / max_seconds_per_unit。",
        )
    report = summary["report"]
    if report.get("dropped_statements"):
        add("medium", "dropped-statements", f"终检删除了 {report['dropped_statements']} 条无法核实引用的陈述", "", "写作模型在编造或写错证据 ID：调低 nodes.section.temperature，或换模型；确认写作提示词里“引用标记必须保留”的措辞没被改掉。")
    if report.get("validation_retries"):
        add("medium", "report-rewrites", f"报告终检触发了 {report['validation_retries']} 次整体重写", "", "每次重写都是完整的写作流程；看 final_errors 对应的校验。")
    if run["status"] == "COMPLETED" and not report.get("citations"):
        add("high", "no-citations", "报告没有任何引用", "", "检查证据是否可引用（cite_search_results、来源角色 read/search）以及写作提示词。")
    if summary["cost"].get("unpriced_models"):
        add("info", "unpriced", "没有配置模型单价，费用为空", ", ".join(summary["cost"]["unpriced_models"]), "在部署私有配置的 pricing 里填真实单价（不要写进示例配置）。")
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(out, key=lambda item: order[item["severity"]])


def build(database, run_id, data):
    run, summary = data["run"], data["summary"]
    records = exchanges(database, run_id)
    facts = {
        "identity": {
            "run_id": run["run_id"],
            "thread_id": run.get("thread_id"),
            "store": str(database),
            "query": run.get("query"),
            "status": run.get("status"),
            "error": run.get("error"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "cycles": summary.get("cycles"),
            "follow_ups": sum(1 for message in run.get("conversation") or [] if message.get("role") == "user" and message.get("id") != "initial"),
            "budget": run.get("budget"),
            "usage": run.get("usage"),
            "profile": run.get("profile"),
            "audited_requests": len(records),
        },
        "settings": settings_view(data["snapshot"]),
        "summary": summary,
        "threads": threads(data["model_calls"]),
        "slowest_calls": slowest(data["model_calls"]),
        "output_speed": speed_by_node(data["model_calls"]),
        "repeat_calls": repeat_calls(data["model_calls"]),
        "admission_waits": admission_waits(data["agent_runs"], data["model_calls"]),
        "failed_tool_time": failed_tool_time(data["tool_calls"]),
        "tool_waits": tool_waits(data["model_calls"], data["tool_calls"]),
        "phases": phase_view(summary, data["tool_calls"]),
        "search": search_view(data["tool_calls"]),
        "prompt_cache": {"prefix_breaks": prefix_breaks(database, run_id, records)},
        "process": process(data),
        "_model_calls": data["model_calls"],
    }
    origin = seconds_of(run.get("created_at"))
    concurrency = (facts["settings"] or {}).get("max_concurrency") or summary["agents"].get("max_parallel")
    facts["rounds"] = research_rounds(data, facts["tool_waits"], concurrency)
    facts["timeline"] = timeline(data, step_rounds(run.get("units") or []), origin)
    facts["tool_log"] = tool_log(data["tool_calls"], origin)
    loops = [row for row in facts["threads"] if row["turns_checked"]]
    facts["prompt_cache"]["provider_check"] = {"turns_checked": sum(row["turns_checked"] for row in loops), "turns_served_from_cache": sum(row["turns_served_from_cache"] for row in loops)}
    facts["findings"] = findings(facts)
    del facts["_model_calls"]
    return facts


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def fmt(value, unit=""):
    if value is None:
        return "—"
    if isinstance(value, float):
        value = round(value, 1)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) >= 10000:
        return f"{value / 1000:.1f}k{unit}"
    return f"{value}{unit}"


def pct(value):
    return "—" if value is None else f"{value:.0%}"


def table(head, rows):
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    lines += ["| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def render(facts, baseline=None):
    identity, summary, proc = facts["identity"], facts["summary"], facts["process"]
    time, tokens, tools, report = summary["time"], summary["tokens"], summary["tools"], summary["report"]
    parts = [f"# DeepResearch 运行审计底稿：{identity['run_id'][:8]}", ""]
    parts += [
        f"- 请求：{' '.join((identity.get('query') or '').split())[:200]}",
        f"- 状态：**{identity['status']}**" + (f"（{identity['error'].get('code')}：{identity['error'].get('message')}）" if identity.get("error") else ""),
        f"- run_id：`{identity['run_id']}` · thread：`{identity.get('thread_id')}` · 创建：{identity.get('created_at')} · 追问 {identity['follow_ups']} 次 · 任务周期 {identity.get('cycles')}",
        f"- 数据：`{identity['store']}` · 已审计的模型请求 {identity['audited_requests']} 条",
        f"- 预算：{json.dumps(identity.get('budget'), ensure_ascii=False)}",
        "",
        "> 这是脚本生成的事实底稿。数字来自产品自己的指标代码；“发现”是规则命中，结论与根因需要审计者核实（见 references/audit-report.md）。",
        "",
    ]

    parts += ["## 1. 需要关注的发现", ""]
    if facts["findings"]:
        parts.append(table(["级别", "代码", "发现", "证据", "可调的设置"], [[item["severity"], item["code"], item["title"], item["evidence"] or "", item["advice"]] for item in facts["findings"]]))
    else:
        parts.append("规则没有命中任何异常。")
    parts.append("")

    parts += ["## 2. 时间去哪了", ""]
    parts.append(table(["总时长", "活跃", "等待(用户/停机)", "模型", "工具", "排队"], [[fmt(time.get(key), "s") for key in ("wall_seconds", "active_seconds", "waiting_seconds", "model_seconds", "tool_seconds", "queue_seconds")]]))
    parts += ["", "模型、工具时长是各调用之和，并行时会超过活跃时长。", "", "按阶段：", ""]
    parts.append(
        table(
            ["阶段", "秒", "次数", "错误", "模型调用", "模型秒", "输入", "输出", "缓存命中", "工具调用", "工具错误", "搜索(失败)", "阅读(失败)"],
            [
                [
                    row["phase"],
                    fmt(row["seconds"]),
                    row.get("runs"),
                    row.get("errors"),
                    row["model_calls"],
                    fmt(row["model_seconds"]),
                    fmt(row["input_tokens"]),
                    fmt(row["output_tokens"]),
                    pct(ratio(row["cache_read_tokens"] or 0, row["input_tokens"])) if row["input_tokens"] else "—",
                    row["tool_calls"],
                    row["tool_errors"],
                    f"{row['searches']}({row['failed_searches']})",
                    f"{row['reads']}({row['failed_reads']})",
                ]
                for row in facts["phases"]
            ],
        )
    )
    parts += ["", "按节点：", ""]
    speed = {row["node"]: row for row in facts["output_speed"]}
    parts.append(
        table(
            ["节点", "模型", "调用", "模型耗时", "P50 / P95", "输入", "输出", "输出速度(含prefill)", "缓存命中 / 可复用", "截断", "重试", "错误"],
            [
                [
                    row["node"],
                    "、".join(row.get("models") or []),
                    row["model_calls"],
                    fmt(row["model_ms"] / 1000, "s"),
                    f"{fmt((row['latency_ms'].get('p50') or 0) / 1000, 's')} / {fmt((row['latency_ms'].get('p95') or 0) / 1000, 's')}",
                    fmt(row["input_tokens"]),
                    fmt(row["output_tokens"]),
                    fmt(speed.get(row["node"], {}).get("output_tokens_per_second"), " tok/s"),
                    f"{pct(row.get('cache_read_ratio'))} / {pct(row.get('prefix_reuse_ratio'))}",
                    row.get("truncated"),
                    row.get("retries"),
                    row.get("model_errors"),
                ]
                for row in summary["breakdown"]["by_node"]
            ],
        )
    )
    parts += ["", f"最慢的 {len(facts['slowest_calls'])} 次模型调用（call_id 可在 LLM 调用审计里打开）：", ""]
    parts.append(
        table(
            ["call_id", "节点", "步骤", "秒", "输入", "命中", "输出", "tok/s", "结束原因"],
            [
                [
                    row["call_id"],
                    row["node"],
                    row["unit_id"] or "",
                    row["seconds"],
                    fmt(row["input_tokens"]),
                    fmt(row["cache_read_tokens"]),
                    fmt(row["output_tokens"]),
                    fmt(row["output_tokens_per_second"]),
                    row["finish_reason"] or row["status"],
                ]
                for row in facts["slowest_calls"]
            ],
        )
    )
    parts.append("")

    parts += ["## 3. Token 去哪了", ""]
    parts.append(
        table(
            ["输入", "输出", "缓存命中", "命中率", "可复用前缀", "未上报调用", "总计", "费用"],
            [
                [
                    fmt(tokens.get("input")),
                    fmt(tokens.get("output")),
                    fmt(tokens.get("cache_read")),
                    pct(tokens.get("cache_read_ratio")),
                    pct(tokens.get("prefix_reuse_ratio")),
                    tokens.get("unreported_calls"),
                    fmt(tokens.get("total")),
                    f"{fmt(summary['cost'].get('total'))} {summary['cost'].get('currency') or ''}",
                ]
            ],
        )
    )
    loops = [row for row in facts["threads"] if row["turns"] >= 2]
    parts += ["", f"Agent 循环（{len(loops)} 个，按输入 Token 排序；上下文从首轮长到末轮，每轮整段重发，所以输入合计远大于最大上下文。单轮调用见第 2 节的节点表）：", ""]
    parts.append(
        table(
            ["节点", "步骤", "轮数", "首轮→末轮输入", "最大上下文", "输入合计", "输出", "命中率", "可复用", "供应商逐轮命中", "模型秒", "轮间秒(工具等)"],
            [
                [
                    row["node"],
                    row["unit_id"] or "",
                    row["turns"],
                    f"{fmt(row['first_input'])} → {fmt(row['last_input'])}",
                    fmt(row["max_input"]),
                    fmt(row["input_tokens"]),
                    fmt(row["output_tokens"]),
                    pct(row["cache_read_ratio"]),
                    pct(row["prefix_reuse_ratio"]),
                    f"{row['turns_served_from_cache']}/{row['turns_checked']}",
                    row["model_seconds"],
                    row["between_turns_seconds"],
                ]
                for row in loops[:20]
            ],
        )
    )
    parts.append("")

    cache = facts["prompt_cache"]
    parts += ["## 4. 提示词缓存", ""]
    check = cache["provider_check"]
    parts.append(f"- 供应商逐轮核对：{check['turns_served_from_cache']}/{check['turns_checked']} 个后续轮次的命中 Token ≥ 上一轮的完整输入（这是线上字节级前缀一致的证据；供应商不上报缓存时为 0/N，不代表没命中）。")
    breaks = cache["prefix_breaks"]
    parts.append(f"- 前缀断点：比较了 {breaks['turns_compared']} 个相邻请求，{breaks['breaks']} 处不是“只追加”。")
    if breaks["examples"]:
        parts += [
            "",
            table(
                ["call_id", "节点", "步骤", "相同消息数/上一轮消息数", "疑似压缩", "原消息", "新消息"],
                [
                    [
                        row["call_id"],
                        row["node"],
                        row["unit_id"] or "",
                        f"{row['shared_messages']}/{row['previous_messages']}",
                        "是" if row.get("likely_compaction") else "否",
                        f"{row.get('previous_role')}: {row.get('previous_preview', '')}",
                        f"{row.get('role')}: {row.get('preview', '')}",
                    ]
                    for row in breaks["examples"]
                ],
            ),
        ]
    parts.append("")

    parts += ["## 5. 工具与检索", ""]
    parts.append(
        table(
            ["调用", "错误", "错误率", "搜索", "阅读", "读到的页面", "重复调用", "预算拒绝", "供应商切换"],
            [[tools.get("count"), tools.get("errors"), pct(tools.get("error_rate")), tools.get("searches"), tools.get("reads"), tools.get("pages_read"), tools.get("repeat_calls"), tools.get("budget_stops"), tools.get("failovers")]],
        )
    )
    parts += ["", f"错误类型：{json.dumps(tools.get('error_types'), ensure_ascii=False)}", ""]
    parts.append(
        table(
            ["工具", "角色", "次数", "错误", "重复", "P50 / P95", "返回字符"],
            [
                [
                    row["tool"],
                    row.get("role") or "",
                    row["count"],
                    row["errors"],
                    row.get("repeats"),
                    f"{fmt((row['latency_ms'].get('p50') or 0) / 1000, 's')} / {fmt((row['latency_ms'].get('p95') or 0) / 1000, 's')}",
                    fmt(row.get("output_chars")),
                ]
                for row in tools.get("by_tool", [])
            ],
        )
    )
    if tools.get("by_provider"):
        parts += [
            "",
            table(
                ["工具", "供应商", "尝试", "有结果", "空", "错误", "跳过", "缓存", "错误种类"],
                [[row["tool"], row["provider"], row["attempts"], row["answered"], row["empty"], row["errors"], row["skipped"], row["cached"], json.dumps(row.get("error_kinds"), ensure_ascii=False)] for row in tools["by_provider"]],
            ),
        ]
    if facts["failed_tool_time"]:
        parts += ["", "失败的工具调用耗时：" + "；".join(f"{row['tool']} {row['calls']} 次 / {row['seconds']} 秒（平均 {row['avg_seconds']} 秒）" for row in facts["failed_tool_time"])]
    if tools.get("failing_domains"):
        parts += ["", "读取失败最多的站点：" + json.dumps(tools["failing_domains"], ensure_ascii=False)]
    search = facts["search"]
    parts += ["", f"搜索耗时分布（成功 P50 {fmt(search['answered_p50_seconds'], 's')} / P90 {fmt(search['answered_p90_seconds'], 's')}）：", ""]
    parts.append(table(["", *search["latency_buckets"]], [["成功", *search["answered_searches"]], ["失败", *search["failed_searches"]]]))
    waits = [row for row in facts["tool_waits"] if row["node"] == "research"]
    if waits:
        parts += [
            "",
            "等工具的时间（一轮里并行的工具要等最慢的一个；“去掉失败”把失败的调用换成该工具成功调用的中位耗时，差值就是白等的时间）：",
            "",
            table(
                ["步骤", "工具批次", "被失败拖住的批次", "失败调用", "等工具秒", "去掉失败后", "白等秒"],
                [[row["unit_id"] or "", row["batches"], row["delayed_batches"], row["failed_calls"], row["wait_seconds"], row["wait_seconds_without_failures"], row["lost_seconds"]] for row in waits],
            ),
        ]
    parts.append("")

    parts += ["## 6. 研究与写作过程", ""]
    research = summary["research"]
    parts.append(
        f"- 计划 {research.get('planned_units')} 步，补研 {research.get('supplement_units')} 步，补研轮数 {proc['iterations']}，失败 {research.get('failed_units')} 步；原始证据 {research.get('raw_evidence')}，证据池 {research.get('evidence_pool')}，裁剪 {research.get('trimmed_evidence')}；转换重试 {research.get('conversion_retries')}。"
    )
    if proc["gap_rounds"]:
        parts.append("- 触发补研的缺口：" + "；".join(json.dumps(item["codes"], ensure_ascii=False) for item in proc["gap_rounds"]))
    if proc["open_gaps"]:
        parts.append("- 结束时仍存在的缺口：" + json.dumps(dict(Counter(gap["code"] for gap in proc["open_gaps"])), ensure_ascii=False) + ("（已作为局限写入报告）" if proc["limited_report"] else ""))
    parts += [
        "",
        table(
            ["步骤", "角色", "补研自", "状态", "提前收尾", "秒", "发现", "原始证据", "未解问题"],
            [
                [unit["unit_id"], unit["skill"], unit["supplement_of"] or "", unit["status"] or "", unit["stop_reason"] or unit["error_code"] or "", unit["seconds"], unit["findings"], unit["raw_evidences"], unit["open_questions"]]
                for unit in proc["units"]
            ],
        ),
    ]
    if facts.get("rounds"):
        parts += [
            "",
            "研究轮次的成本与产出（第 0 轮是计划的步骤，之后每轮是一次补研；“首次读到”按证据第一次出现的轮次归属，“推算”是去掉失败调用造成的等待后按同样并发重排的时长）：",
            "",
            table(
                ["轮", "步骤", "墙钟秒", "推算(无失败调用)", "模型调用", "输入", "输出", "命中率", "工具(错误)", "搜索", "阅读", "发现", "被引用的发现", "未解问题", "首次读到的证据", "其中被引用", "首次读到的引用来源"],
                [
                    [
                        row["round"],
                        len(row["units"]),
                        fmt(row["wall_seconds"]),
                        fmt(row["estimated_wall_seconds_without_failed_calls"]),
                        row["model_calls"],
                        fmt(row["input_tokens"]),
                        fmt(row["output_tokens"]),
                        pct(ratio(row["cache_read_tokens"], row["input_tokens"])),
                        f"{row['tool_calls']}({row['tool_errors']})",
                        row["searches"],
                        row["reads"],
                        row["findings"],
                        fmt(row["cited_findings"]),
                        row["open_questions"],
                        fmt(row["evidence_first_seen"]),
                        fmt(row["cited_evidence_first_seen"]),
                        fmt(row["cited_sources_first_seen"]),
                    ]
                    for row in facts["rounds"]
                ],
            ),
        ]
    waits = [row for row in facts["admission_waits"] if row["wait_seconds"] >= 5]
    if waits:
        parts += ["", "引擎准入排队（角色提交到第一次模型调用之间；已包含在上表的秒数里）：" + "；".join(f"{row['unit_id']} {row['wait_seconds']}s" for row in waits[:12])]
    parts += [
        "",
        f"- 报告：{report.get('characters')} 字符，{report.get('sections')} 章，{report.get('tables')} 表，{report.get('diagrams')} 图，{report.get('citations')} 条引用，{report.get('cited_domains')} 个域名；章节修复 {report.get('draft_repairs')}，删除陈述 {report.get('dropped_statements')}，终检重写 {report.get('validation_retries')}。",
    ]
    if proc["draft_repairs"]:
        parts.append("- 触发修复的写作任务：" + json.dumps(proc["draft_repairs"], ensure_ascii=False))
    efficiency = summary["efficiency"]
    parts.append(
        f"- 效率：每条引用 {fmt(efficiency.get('tokens_per_citation'))} token / {fmt(efficiency.get('active_seconds_per_citation'))} 秒；每读一页产出 {fmt(efficiency.get('citations_per_page_read'))} 条引用；每步搜索 {fmt(efficiency.get('searches_per_unit'))} 次。"
    )
    parts.append("")

    parts += ["## 7. 本次运行使用的设置", ""]
    parts += ["```json", json.dumps(facts["settings"], ensure_ascii=False, indent=1) if facts["settings"] else "null（该运行早于配置快照，设置以当时的运维配置为准）", "```", ""]

    if baseline:
        parts += [f"## 8. 与基线 {baseline['identity']['run_id'][:8]} 对比", "", "两次运行的计划通常不同，先比比例（命中率、每条引用成本、每次调用耗时），再比总量。", ""]
        extra = facts.get("comparison") or {}
        parts += ["两次运行的设置差异（先确认到底改了什么；提示词只给长度，内容用设置页的历史或快照看）：", ""]
        parts.append(table(["设置", "基线", "本次"], extra.get("settings_diff") or [["（没有差异，或其中一次早于设置快照）", "", ""]]))
        parts += ["", table(["指标", "基线", "本次", "变化"], compare(baseline, facts)), ""]
        if extra.get("same_question_runs"):
            parts += ["同一问题的其他已完成运行（什么都没改时两次运行会差多少；变化小于这里的波动就不能算效果）：", ""]
            parts.append(
                table(
                    ["run", "创建", "步骤", "活跃秒", "模型调用", "输入", "命中率", "每步搜索", "工具错误率", "重做调用", "引用", "字符"],
                    [
                        [
                            row["run_id"][:8],
                            (row["created_at"] or "")[:16],
                            row["units"],
                            fmt(row["active_seconds"]),
                            row["model_calls"],
                            fmt(row["input_tokens"]),
                            pct(row["cache_read_ratio"]),
                            fmt(row["searches_per_unit"]),
                            pct(row["tool_error_rate"]),
                            row["repeat_calls"],
                            row["citations"],
                            fmt(row["characters"]),
                        ]
                        for row in extra["same_question_runs"]
                    ],
                )
            )
        parts.append("")
    return "\n".join(parts)


COMPARE = [
    ("状态", lambda f: f["identity"]["status"]),
    ("活跃秒", lambda f: f["summary"]["time"].get("active_seconds")),
    ("模型秒", lambda f: f["summary"]["time"].get("model_seconds")),
    ("工具秒", lambda f: f["summary"]["time"].get("tool_seconds")),
    ("排队秒(研究侧)", lambda f: f["summary"]["time"].get("queue_seconds")),
    ("引擎准入排队秒(合计)", lambda f: round(sum(row["wait_seconds"] for row in f["admission_waits"]), 1)),
    ("模型调用", lambda f: f["summary"]["model_calls"].get("count")),
    ("输入 Token", lambda f: f["summary"]["tokens"].get("input")),
    ("输出 Token", lambda f: f["summary"]["tokens"].get("output")),
    ("未命中输入 Token", lambda f: (f["summary"]["tokens"].get("input") or 0) - (f["summary"]["tokens"].get("cache_read") or 0) if f["summary"]["tokens"].get("input") is not None else None),
    ("缓存命中率", lambda f: f["summary"]["tokens"].get("cache_read_ratio"), "ratio"),
    ("可复用前缀", lambda f: f["summary"]["tokens"].get("prefix_reuse_ratio"), "ratio"),
    ("费用", lambda f: f["summary"]["cost"].get("total")),
    ("工具调用", lambda f: f["summary"]["tools"].get("count")),
    ("工具错误率", lambda f: f["summary"]["tools"].get("error_rate"), "ratio"),
    ("研究步骤(含补研)", lambda f: len(f["process"]["units"])),
    ("补研步骤", lambda f: f["summary"]["research"].get("supplement_units")),
    ("重做的模型调用", lambda f: sum(f["repeat_calls"].values())),
    ("追问次数(其调用计入总量)", lambda f: f["identity"]["follow_ups"]),
    ("格式重试+章节修复事件", lambda f: (f["summary"]["research"].get("conversion_retries") or 0) + (f["summary"]["report"].get("draft_repairs") or 0)),
    ("引用数", lambda f: f["summary"]["report"].get("citations")),
    ("报告字符", lambda f: f["summary"]["report"].get("characters")),
    ("每条引用 Token", lambda f: f["summary"]["efficiency"].get("tokens_per_citation")),
    ("每条引用活跃秒", lambda f: f["summary"]["efficiency"].get("active_seconds_per_citation")),
    ("研究节点 P50 毫秒", lambda f: next((row["latency_ms"].get("p50") for row in f["summary"]["breakdown"]["by_node"] if row["node"] == "research"), None)),
    ("章节节点 P50 毫秒", lambda f: next((row["latency_ms"].get("p50") for row in f["summary"]["breakdown"]["by_node"] if row["node"] == "section"), None)),
]


def compare(before, after):
    rows = []
    for label, getter, *kind in COMPARE:
        old, new = getter(before), getter(after)
        change = ""
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and not isinstance(old, bool):
            if kind == ["ratio"]:
                change = f"{(new - old) * 100:+.0f} 个百分点"
            elif old:
                change = f"{(new - old) / old:+.0%}"
        if kind == ["ratio"]:
            old, new = pct(old), pct(new)
        rows.append([label, fmt(old) if not isinstance(old, str) else old, fmt(new) if not isinstance(new, str) else new, change])
    return rows


def settings_diff(before, after):
    """What was configured differently: the first thing a before/after comparison needs.

    Walks into mappings and into lists of named entries (models, sources), so a
    changed field shows as ``models[flash].session_param`` instead of two long
    values that look alike. Prompt texts are reported by length only.
    """
    rows, missing = [], "（无）"

    def show(value):
        return missing if value is missing else json.dumps(value, ensure_ascii=False)[:120]

    def named(items):
        return isinstance(items, list) and items and all(isinstance(item, dict) and item.get("name") for item in items)

    def walk(path, old, new, depth=0):
        if old == new:
            return
        if path.startswith("prompts.") and isinstance(old if old is not missing else "", str) and isinstance(new if new is not missing else "", str):
            rows.append([path, f"{len(old) if old is not missing else 0} 字符", f"{len(new) if new is not missing else 0} 字符"])
        elif isinstance(old, dict) and isinstance(new, dict) and depth < 4:
            for key in sorted(set(old) | set(new)):
                walk(f"{path}.{key}" if path else key, old.get(key, missing), new.get(key, missing), depth + 1)
        elif named(old) and named(new) and depth < 4:
            left, right = {item["name"]: item for item in old}, {item["name"]: item for item in new}
            for name in sorted(set(left) | set(right)):
                walk(f"{path}[{name}]", left.get(name, missing), right.get(name, missing), depth + 1)
        else:
            rows.append([path, show(old), show(new)])

    walk("", before or {}, after or {})
    return rows


def peers(stores, facts, pricing, limit=6):
    """Other runs of the same question: how much two runs differ when nothing was changed."""
    query = " ".join((facts["identity"].get("query") or "").split())
    rows = []
    for path in stores:
        for run_id, body in run_rows(path):
            if run_id != facts["identity"]["run_id"] and " ".join((body.get("query") or "").split()) == query and body.get("status") == "COMPLETED":
                rows.append((body.get("created_at") or "", path, run_id))
    table = []
    for _, path, run_id in sorted(rows, reverse=True)[:limit]:
        data = asyncio.run(load(path, run_id, pricing))
        summary = data["summary"]
        table.append(
            {
                "run_id": run_id,
                "created_at": data["run"].get("created_at"),
                "units": len(data["run"].get("units") or []),
                "active_seconds": summary["time"].get("active_seconds"),
                "model_calls": summary["model_calls"].get("count"),
                "input_tokens": summary["tokens"].get("input"),
                "cache_read_ratio": summary["tokens"].get("cache_read_ratio"),
                "searches_per_unit": summary["efficiency"].get("searches_per_unit"),
                "tool_error_rate": summary["tools"].get("error_rate"),
                "repeat_calls": sum(repeat_calls(data["model_calls"]).values()),
                "citations": summary["report"].get("citations"),
                "characters": summary["report"].get("characters"),
            }
        )
    return table


def listing(stores):
    rows = []
    for path in stores:
        for run_id, body in run_rows(path):
            rows.append((body.get("created_at") or "", run_id, body.get("status"), path.parent.parent.name, " ".join((body.get("query") or "").split())[:60]))
    for created, run_id, status, home, query in sorted(rows, reverse=True)[:40]:
        print(f"{created[:16]}  {run_id}  {status or '':<28} {home:<18} {query}")
    if not rows:
        print("No runs found. Pass --data-dir <directory containing research.sqlite3>.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", help="Run id, id prefix, research page URL, thread id, or 'latest'")
    parser.add_argument("--baseline", help="Another run to compare against (before/after a tuning change)")
    parser.add_argument("--data-dir", action="append", default=[], help="Directory (or research.sqlite3) to search; default: every store under <repo>/.deerflow/deepresearch")
    parser.add_argument("--config", type=Path, help="Research YAML whose pricing is used for cost estimates")
    parser.add_argument("--out", type=Path, help="Output directory (default: <repo>/.deerflow/deepresearch/audits, which git ignores)")
    parser.add_argument("--report", type=Path, help="The written audit report (Markdown) to show at the top of the HTML page (default: 审计报告-<run8>.md in the output directory, when it exists)")
    parser.add_argument("--repo", type=Path, help="Repository root, when it cannot be found from the script or the working directory")
    parser.add_argument("--list", action="store_true", help="List the runs in the discovered stores and exit")
    args = parser.parse_args(argv)

    repo = args.repo.resolve() if args.repo else find_repo(__file__)
    if repo is None:
        parser.error("Cannot find the repository (backend/deepresearch). Pass --repo.")
    sys.path.insert(0, str(repo / "backend"))
    stores = databases(repo, args.data_dir)
    if args.list or not args.run:
        listing(stores)
        return 0
    try:
        import deepresearch.metrics  # noqa: F401
    except ImportError as error:
        raise SystemExit(f"Cannot import DeepResearch ({error}). Run this script with the backend's Python: {repo / 'backend' / '.venv' / 'bin' / 'python'}") from None
    pricing = {}
    if args.config:
        from deepresearch.config import load_settings

        pricing = load_settings(args.config).pricing

    def audit(reference):
        found = resolve(reference, stores)
        if found is None:
            raise SystemExit(f"No run matches '{reference}' in {len(stores)} store(s). Try --list or --data-dir.")
        database, run_id = found
        data = asyncio.run(load(database, run_id, pricing))
        return build(database, run_id, data), data["snapshot"]

    facts, snapshot = audit(args.run)
    baseline = None
    if args.baseline:
        baseline, baseline_snapshot = audit(args.baseline)
        facts["comparison"] = {"settings_diff": settings_diff(baseline_snapshot, snapshot), "same_question_runs": peers(stores, facts, pricing)}
    # Next to the run data and ignored by git: an audit quotes private research content.
    out = (args.out or repo / ".deerflow" / "deepresearch" / "audits").resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = "audit-" + facts["identity"]["run_id"][:8]
    (out / f"{name}.json").write_text(json.dumps({"run": facts, "baseline": baseline}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / f"{name}.md").write_text(render(facts, baseline), encoding="utf-8")
    # The page shows the same facts; the auditor's written report, once it exists, leads it.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # also when this module was imported rather than run
    try:
        from audit_html import render_html
    finally:
        sys.dont_write_bytecode = previous

    written = args.report or out / f"审计报告-{facts['identity']['run_id'][:8]}.md"
    report = written.read_text(encoding="utf-8") if written.is_file() else None
    (out / f"{name}.html").write_text(render_html(facts, baseline, compare(baseline, facts) if baseline else None, report), encoding="utf-8")
    print(f"facts : {out / (name + '.json')}")
    print(f"sheet : {out / (name + '.md')}")
    print(f"page  : {out / (name + '.html')}" + ("" if report else f"  (write {written.name} next to it and run again to put the conclusions on the page)"))
    print(f"status: {facts['identity']['status']} · findings: " + (", ".join(f"{item['severity']}:{item['code']}" for item in facts["findings"]) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
