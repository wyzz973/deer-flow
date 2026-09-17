"""User-facing research activity: concise, structured and cycle-scoped."""

from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from deepresearch.activity import build, tool_roles
from deepresearch.config import SourceSpec
from deepresearch.store import Store
from deepresearch.trace import LocalTrace, model_callbacks, tool_detail


def event(seq, kind, data=None, at=None):
    return {"seq": seq, "type": kind, "at": at or f"2026-09-16T00:00:{seq:02d}+00:00", "data": data or {}}


def run(status="RESEARCHING"):
    units = [
        {"id": "R1", "title": "梳理官方能力", "objective": "objective one"},
        {"id": "S1-x", "title": "", "objective": "supplement", "parent_gap_id": "R1-coverage", "depends_on": ["R1"]},
    ]
    return {"status": status, "units": units, "plan": {"title": "代码仓智能化调研", "goal": "goal"}, "updated_at": "2026-09-16T00:01:00+00:00"}


def test_timeline_groups_searches_and_reads_and_reports_counts():
    roles = {"web_search": "search", "web_fetch": "read"}
    calls = [
        {"id": "c1", "tool_name": "web_search", "unit_id": "R1", "status": "success", "started_at": "2026-09-16T00:00:03+00:00", "query": "GitHub Copilot code review", "domains": ["docs.github.com"]},
        {"id": "c2", "tool_name": "web_search", "unit_id": "R1", "status": "success", "started_at": "2026-09-16T00:00:04+00:00", "query": "GitLab Duo", "domains": ["docs.gitlab.com", "docs.github.com"]},
        {"id": "c3", "tool_name": "web_fetch", "unit_id": "R1", "status": "success", "started_at": "2026-09-16T00:00:05+00:00", "url": "https://docs.github.com/copilot", "title": "About Copilot"},
        {"id": "c4", "tool_name": "read_file", "unit_id": "R1", "status": "success", "started_at": "2026-09-16T00:00:06+00:00"},
        {"id": "c5", "tool_name": "web_fetch", "unit_id": "R1", "status": "running", "started_at": "2026-09-16T00:00:09+00:00", "url": "https://docs.gitlab.com/duo"},
    ]
    events = [
        event(1, "plan.created", {"plan_version": 1}),
        event(2, "research.unit.started", {"unit_id": "R1"}),
        event(3, "activity.tool.completed", calls[0]),
        event(4, "activity.tool.completed", calls[1]),
        event(5, "activity.note", {"unit_id": "R1", "text": "我先核对 GitHub 官方文档"}),
        event(6, "activity.tool.completed", calls[2]),
        event(7, "activity.tool.completed", calls[3]),
        event(8, "research.unit.completed", {"unit_id": "R1", "summary": "已确认三项能力", "findings": 3}),
        event(9, "research.unit.started", {"unit_id": "S1-x"}),
    ]
    value = build(run(), events, calls, roles, now=datetime(2026, 9, 16, 0, 0, 12, tzinfo=UTC))
    kinds = [item["kind"] for item in value["items"]]
    assert kinds == ["plan", "step", "search", "note", "read", "step_done", "step"]
    search = value["items"][2]
    assert search["count"] == 2 and search["queries"] == ["GitHub Copilot code review", "GitLab Duo"]
    assert search["domains"] == ["docs.github.com", "docs.gitlab.com"]
    assert value["items"][4] == {**value["items"][4], "domain": "docs.github.com", "title": "About Copilot"}
    assert value["items"][5]["summary"] == "已确认三项能力" and value["items"][6]["title"] == "↻ 梳理官方能力"
    assert value["counts"] == {"searches": 2, "pages_read": 1, "steps": 1, "steps_done": 1}
    assert value["elapsed_seconds"] == 10 and value["finished_at"] is None
    assert value["current"] == {"kind": "note", "text": "我先核对 GitHub 官方文档"}


def test_progress_counts_only_planned_steps_and_failed_steps_are_finished():
    value = run()
    value["units"].append({"id": "R2", "title": "核对价格", "objective": "pricing"})
    events = [
        event(1, "research.unit.started", {"unit_id": "R1"}),
        event(2, "research.unit.started", {"unit_id": "R2"}),
        event(3, "research.unit.completed", {"unit_id": "R1"}),
        event(4, "research.unit.failed", {"unit_id": "R2", "code": "NATIVE_AGENT_TIMEOUT"}),
        event(5, "research.unit.started", {"unit_id": "S1-x"}),
        event(6, "research.unit.completed", {"unit_id": "S1-x"}),
    ]
    activity = build(value, events, [], {})
    assert activity["counts"]["steps"] == 2 and activity["counts"]["steps_done"] == 2
    assert activity["items"][3] == {**activity["items"][3], "kind": "step_failed", "title": "核对价格", "code": "NATIVE_AGENT_TIMEOUT"}


def test_notes_follow_the_reader_language_and_hide_internal_steps():
    events = [
        event(1, "research.unit.started", {"unit_id": "R1"}),
        event(2, "activity.note", {"unit_id": "R1", "text": "I'll start by reading the assigned skill file, then search."}),
        event(3, "activity.note", {"unit_id": "R1", "text": "Checking the official pricing page next."}),
        event(4, "activity.note", {"unit_id": "R1", "text": "先读取 SKILL.md，再用 web_search 检索"}),
        event(5, "activity.note", {"unit_id": "R1", "text": "接下来核对 Meilisearch 官方定价页"}),
        event(6, "activity.note", {"unit_id": "R1", "text": "已读取skill文件并获取两份一手来源。"}),
    ]
    chinese = build({**run(), "query": "比较三种搜索引擎"}, events, [], {})
    assert [item["text"] for item in chinese["items"] if item["kind"] == "note"] == ["接下来核对 Meilisearch 官方定价页"]
    english = build({**run(), "query": "Compare search engines"}, events, [], {})
    assert [item["text"] for item in english["items"] if item["kind"] == "note"] == ["Checking the official pricing page next.", "接下来核对 Meilisearch 官方定价页"]


def test_current_status_follows_writing_and_terminal_runs_stop_the_clock():
    events = [event(1, "research.unit.started", {"unit_id": "R1"}), event(2, "report.synthesizing"), event(3, "report.section.completed", {"heading": "功能蓝图"})]
    writing = build(run("SYNTHESIZING"), events, [], {})
    assert writing["current"] == {"kind": "writing", "title": "功能蓝图"}
    done = build(run("COMPLETED"), [*events, event(20, "report.completed", {"title": "报告", "version": 1})], [], {})
    assert done["current"] is None and done["elapsed_seconds"] == 19
    assert done["items"][-1] == {"kind": "done", "at": events[0]["at"][:-8] + "20+00:00", "title": "报告", "version": 1}
    searching = build(run(), [event(1, "research.unit.started", {"unit_id": "R1"})], [{"id": "c", "tool_name": "web_search", "status": "running", "query": "pricing", "started_at": "2026-09-16T00:00:02+00:00"}], {"web_search": "search"})
    assert searching["current"] == {"kind": "search", "query": "pricing"}


def test_a_new_research_cycle_starts_a_fresh_timeline():
    calls = [{"id": "old", "tool_name": "web_search", "status": "success", "started_at": "2026-09-16T00:00:02+00:00"}]
    events = [event(1, "research.unit.started", {"unit_id": "R1"}), event(2, "activity.tool.completed", calls[0]), event(5, "research.cycle.started", {"cycle": 1}), event(6, "plan.created")]
    value = build(run("AWAITING_PLAN_CONFIRMATION"), events, calls, {"web_search": "search"})
    assert [item["kind"] for item in value["items"]] == ["plan"]
    assert value["counts"]["searches"] == 0 and value["elapsed_seconds"] is None


def test_tool_detail_uses_declared_roles_and_safe_urls_only():
    assert tool_detail("search", {"query": "  GitLab   Duo pricing "}) == {"query": "GitLab Duo pricing"}
    assert tool_detail("search", '{"q": "json args"}') == {"query": "json args"}
    assert tool_detail("read", {"url": "https://docs.gitlab.com/duo"}) == {"url": "https://docs.gitlab.com/duo"}
    assert tool_detail("read", {"url": "javascript:alert(1)"}) == {}
    assert tool_detail("data", {"query": "private business argument"}) == {}


@pytest.mark.asyncio
async def test_callbacks_record_queries_and_visible_progress_notes(settings, tmp_path):
    settings.sources = [SourceSpec(name="kb", server="kb", tool="kb_search", origin="internal", role="search")]
    assert tool_roles(settings)["kb_search"] == "search" and tool_roles(settings)["web_fetch"] == "read"
    store = Store(tmp_path / "activity.sqlite")
    await store.start()
    await store.create({"run_id": "r", "owner": "u", "usage": {"model_tokens": 0, "tool_calls": 0}, "budget": {"max_model_tokens": None, "max_tool_calls": None}}, "k", "h")
    callbacks = model_callbacks(LocalTrace(store, "r", settings, ["private-token"]), scope={"unit_id": "R1", "agent_name": "researcher"})
    await callbacks.on_tool_start({"name": "kb_search"}, "", run_id="call", inputs={"query": "Copilot private-token"})
    call = (await store.calls("r"))[0]
    assert call["role"] == "search" and call["query"] == "Copilot [redacted]" and call["unit_id"] == "R1"
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="research")]], run_id="model")
    thinking = AIMessage(content="<think>hidden</think>先检查 GitLab 官方定价页", tool_calls=[{"id": "t", "name": "web_fetch", "args": {"url": "https://about.gitlab.com/pricing"}}])
    await callbacks.on_llm_end(LLMResult(generations=[[ChatGeneration(message=thinking)]]), run_id="model")
    await callbacks.on_chat_model_start({}, [[HumanMessage(content="skill")]], run_id="skill")
    skill = AIMessage(content="读取方法说明", tool_calls=[{"id": "s", "name": "read_file", "args": {"path": "SKILL.md"}}])
    await callbacks.on_llm_end(LLMResult(generations=[[ChatGeneration(message=skill)]]), run_id="skill")
    notes = [e["data"] for e in await store.events("r", limit=100) if e["type"] == "activity.note"]
    assert notes == [{"unit_id": "R1", "agent_name": "researcher", "text": "先检查 GitLab 官方定价页"}]
