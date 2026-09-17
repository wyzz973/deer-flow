"""User-facing research activity, derived from the durable event log.

The local trace remains the complete debugging record. This projection is the
concise timeline a reader follows while research runs: progress notes written
by researchers, searches grouped with their queries and result domains, pages
opened, completed steps and report writing. It returns structured items; the
client owns wording and localization. Nothing here reads tool business fields.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

from .contracts import TERMINAL
from .report import language

NATIVE_ROLES = {"web_search": "search", "image_search": "search", "web_fetch": "read"}
HIDDEN_TOOLS = {"read_file", "ls", "glob", "grep", "write_file", "str_replace", "bash", "present_files"}
WRITING = {"SYNTHESIZING", "CITATION_BINDING", "FINAL_VALIDATING", "RENDERING"}
# Researchers sometimes narrate their own harness. Those sentences stay in the
# trace; the timeline shows research progress in the reader's language.
INTERNAL_NOTE = re.compile(r"SKILL\.md|skills?\s*(?:files?|methodology|instructions|文件|说明)|(?:assigned|applicable) skill|技能文件|技能说明|\b(?:web_search|web_fetch|read_file|browser_\w+)\b", re.I)
CJK = re.compile(r"[\u4e00-\u9fff]")


def visible_note(text, lang):
    text = (text or "").strip()
    if not text or INTERNAL_NOTE.search(text):
        return False
    return lang != "zh" or bool(CJK.search(text))


def tool_roles(settings):
    return {**NATIVE_ROLES, **{source.tool: source.role for source in settings.sources}}


def _time(value):
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _domain(url):
    try:
        return (urlsplit(url or "").hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _short(text, limit=60):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build(run, events, calls, roles, now=None):
    now = now or datetime.now(UTC)
    boundary = max((index for index, event in enumerate(events) if event["type"] == "research.cycle.started"), default=-1)
    scoped = events[boundary + 1 :]
    cycle_start = events[boundary]["at"] if boundary >= 0 else ""
    calls = [call for call in calls if (call.get("started_at") or "") >= cycle_start]
    by_id = {call["id"]: call for call in calls}
    units = {unit["id"]: unit for unit in run.get("units") or []}
    plan = run.get("plan") or {}
    lang = language(run.get("query", ""))

    def role(call):
        return call.get("role") or roles.get(call.get("tool_name"))

    def title(unit_id):
        unit = units.get(unit_id) or {}
        value = unit.get("title") or _short(unit.get("objective"))
        if unit.get("parent_gap_id"):
            parent = units.get(unit.get("depends_on", [None])[0]) or {}
            return "↻ " + (parent.get("title") or _short(parent.get("objective")) or value)
        return value or unit_id

    items, started_at, finished_at = [], None, None
    for event in scoped:
        kind, data, at = event["type"], event.get("data") or {}, event["at"]
        if kind in {"plan.created", "plan.updated"}:
            items.append({"kind": "plan", "at": at, "title": plan.get("title") or plan.get("goal") or "", "version": data.get("plan_version")})
        elif kind == "research.unit.started" and data.get("unit_id") in units:
            started_at = started_at or at
            items.append({"kind": "step", "at": at, "unit_id": data["unit_id"], "title": title(data["unit_id"])})
        elif kind == "activity.note":
            if visible_note(data.get("text"), lang):
                items.append({"kind": "note", "at": at, "unit_id": data.get("unit_id"), "text": data.get("text", "")})
        elif kind == "activity.tool.completed":
            call = {**data, **by_id.get(data.get("id"), {})}
            unit_id = call.get("unit_id")
            if role(call) == "search":
                query = call.get("query")
                last = items[-1] if items else None
                if last and last["kind"] == "search" and last.get("unit_id") == unit_id:
                    last["count"] += 1
                    last["queries"] += [query] if query and query not in last["queries"] else []
                    last["domains"] += [domain for domain in call.get("domains") or [] if domain not in last["domains"]]
                else:
                    items.append({"kind": "search", "at": at, "unit_id": unit_id, "count": 1, "queries": [query] if query else [], "domains": list(call.get("domains") or [])})
            elif role(call) == "read":
                url = call.get("url")
                items.append({"kind": "read", "at": at, "unit_id": unit_id, "url": url, "domain": _domain(url), "title": call.get("title") or "", "status": call.get("status")})
            elif call.get("tool_name") not in HIDDEN_TOOLS:
                items.append({"kind": "tool", "at": at, "unit_id": unit_id, "name": call.get("tool_name"), "status": call.get("status")})
        elif kind == "research.unit.completed" and data.get("unit_id") in units:
            items.append({"kind": "step_done", "at": at, "unit_id": data["unit_id"], "title": title(data["unit_id"]), "summary": data.get("summary", ""), "findings": data.get("findings")})
        elif kind == "research.unit.failed" and data.get("unit_id") in units:
            items.append({"kind": "step_failed", "at": at, "unit_id": data["unit_id"], "title": title(data["unit_id"]), "code": data.get("code")})
        elif kind == "validator.gap_found":
            items.append({"kind": "gap", "at": at, "count": len(data.get("gaps") or [])})
        elif kind == "report.limitations.auto":
            items.append({"kind": "limited", "at": at, "count": len(data.get("gaps") or [])})
        elif kind == "conversation.steering":
            items.append({"kind": "update", "at": at, "text": data.get("text", "")})
        elif kind == "report.synthesizing":
            items.append({"kind": "writing", "at": at})
        elif kind == "report.outline.ready":
            items.append({"kind": "outline", "at": at, "sections": data.get("sections", [])})
        elif kind == "report.section.completed":
            items.append({"kind": "section", "at": at, "title": data.get("heading", "")})
        elif kind == "report.completed":
            finished_at = at
            items.append({"kind": "done", "at": at, "title": data.get("title", ""), "version": data.get("version")})
        elif kind in {"run.failed", "run.cancelled"}:
            finished_at = at
            items.append({"kind": "failed" if kind == "run.failed" else "cancelled", "at": at, "code": data.get("code"), "message": data.get("message")})
    status = run.get("status")
    if status not in TERMINAL:
        finished_at = None
    elif finished_at is None:
        finished_at = run.get("updated_at")
    start, end = _time(started_at), _time(finished_at) if finished_at else now
    elapsed = max(0, round((end - start).total_seconds())) if start and end else None
    # Progress counts the plan's own steps; supplementary research refines them.
    planned = {unit_id for unit_id, unit in units.items() if not unit.get("parent_gap_id")}
    finished = {item["unit_id"] for item in items if item["kind"] in {"step_done", "step_failed"} and item["unit_id"] in planned}
    searches = sum(1 for call in calls if role(call) == "search")
    pages = {call.get("url") or call["id"] for call in calls if role(call) == "read" and call.get("status") == "success"}
    return {
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": elapsed,
        "counts": {"searches": searches, "pages_read": len(pages), "steps": len(planned), "steps_done": len(finished)},
        "current": None if status in TERMINAL else current(status, items, calls, role),
        "items": items,
    }


def current(status, items, calls, role):
    """The one-line live status shown on the progress card."""
    if status in {"PLANNING", "CREATED", "RESPONDING"}:
        return {"kind": "planning" if status != "RESPONDING" else "responding"}
    if status in WRITING:
        section = next((item for item in reversed(items) if item["kind"] in {"section", "outline", "writing"}), None)
        return {"kind": "writing", "title": section.get("title", "") if section else ""}
    running = [call for call in calls if call.get("status") == "running"]
    latest_note = next((item for item in reversed(items[-6:]) if item["kind"] == "note"), None)
    if latest_note:
        return {"kind": "note", "text": latest_note["text"]}
    if running:
        call = max(running, key=lambda value: value.get("started_at") or "")
        if role(call) == "search":
            return {"kind": "search", "query": call.get("query") or ""}
        if role(call) == "read":
            return {"kind": "read", "url": call.get("url") or "", "domain": _domain(call.get("url"))}
    return {"kind": "researching"}
