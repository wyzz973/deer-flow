"""The run-audit scripts of the engineering skill read the store this code writes.

They live outside the package (``.agents/skills/deepresearch-engineering``) so
agents of any harness can use them. Running them here keeps them honest: a
changed table, record field or metrics key fails a test instead of silently
producing an empty audit.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from deepresearch.store import Store

SCRIPTS = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "deepresearch-engineering" / "scripts"
RUN = "11111111-2222-3333-4444-555555555555"


def script(name):
    sys.path.insert(0, str(SCRIPTS))
    # The skill folder is shared documentation: importing from it leaves no bytecode behind.
    previous, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous
        sys.path.remove(str(SCRIPTS))


class Message:
    def __init__(self, role, content, **extra):
        self.type, self.content = role, content
        for key, value in extra.items():
            setattr(self, key, value)


async def recorded_run(tmp_path):
    """A research whose second researcher turn rewrote the head of its conversation."""
    from deepresearch.audit import audit_request

    home = tmp_path / "live-test" / "research"
    store = Store(home / "research.sqlite3")
    await store.start()
    unit = {"id": "R1", "skill": "technical-route", "title": "Compare", "objective": "Compare the options"}
    run = {
        "run_id": RUN,
        "thread_id": "dr-" + RUN,
        "owner": "u",
        "query": "Compare vector databases",
        "status": "COMPLETED",
        "created_at": "2026-09-19T10:00:00+00:00",
        "cycle": 0,
        "iteration": 0,
        "budget": {"max_iterations": 1, "max_units": 8, "max_model_tokens": None, "max_tool_calls": None, "max_elapsed_seconds": None},
        "usage": {"model_tokens": 9000, "tool_calls": 6},
        "units": [unit],
        "unit_statuses": {"R1": "COMPLETED"},
        "gaps": [{"gap_id": "R1-open-questions", "unit_id": "R1", "code": "open-questions", "description": "more to check"}],
        "conversation": [{"id": "initial", "role": "user", "text": "Compare vector databases"}],
    }
    await store.create(run, "key", "hash")
    system, task = Message("system", "You are a researcher."), Message("human", "the task")
    turns = [
        [system, task],
        # The ledger inserted behind the system prompt ends the reusable prefix at one message.
        [system, Message("human", "## Tool receipts\n- [r1] web_search"), task, Message("ai", "checking"), Message("tool", "result", tool_call_id="c1")],
    ]
    for index, (messages, tokens, cached, prefix) in enumerate(zip(turns, (3000, 6000), (1024, 1024), (None, 21), strict=True), 1):
        call_id = f"call-{index}"
        details = {"config_node": "research", "purpose": "agent", "skill": "technical-route", "unit_id": "R1", "execution_id": "exec-1", "engine_node": "model", "model": "flash", "status": "ok"}
        metrics = {**details, "started_at": f"2026-09-19T10:0{index}:00+00:00", "ended_at": f"2026-09-19T10:0{index}:05+00:00", "duration_ms": 5000, "input_tokens": tokens, "output_tokens": 200, "total_tokens": tokens + 200}
        metrics |= {"cache_read_tokens": cached, "usage_reported": True, "finish_reason": "tool_calls", "prompt_chars": 100 * index, **({"prefix_chars": prefix} if prefix is not None else {})}
        await store.record_model_call(RUN, call_id, metrics)
        normalized, tools, params = audit_request(messages, {"model": "flash"})
        await store.record_llm_request(RUN, call_id, {**details, "group": "exec-1", "node": "model", "params": params, "message_count": len(normalized)}, normalized, tools)
    # One provider that never answers makes every search wait for its failure first.
    attempts = [{"provider": "serper", "type": "serper", "status": "error", "kind": "auth", "ms": 9000}, {"provider": "duckduckgo", "type": "duckduckgo", "status": "ok", "ms": 900}]
    for index in range(1, 5):
        body = {"tool_name": "web_search", "role": "search", "unit_id": "R1", "execution_id": "exec-1", "status": "success", "duration_ms": 30000, "output_chars": 900}
        await store.record_call(RUN, f"tool-{index}", body | {"started_at": f"2026-09-19T10:01:{index:02d}+00:00", "request_key": f"k{index}", "attempts": attempts, "failovers": 1})
    await store.record_agent_run(RUN, "exec-1", {"config_node": "research", "purpose": "agent", "skill": "technical-route", "unit_id": "R1", "status": "completed", "duration_ms": 130000, "model_calls": 2, "tool_calls": 4})
    await store.save_unit(RUN, "0:R1", "h", {"unit_id": "R1", "findings": [{"claim": "c"}], "raw_evidences": [{}], "open_questions": ["q"], "limitations": []})
    await store.event(RUN, "report.draft.repair", {"task": "report-section-1", "attempt": 1, "problems": 1})
    return home


def test_a_recorded_run_becomes_a_fact_sheet_with_findings_an_auditor_can_act_on(tmp_path, capsys):
    # The scripts are command-line programs with their own event loop.
    home = asyncio.run(recorded_run(tmp_path))
    audit = script("audit_run")
    out = tmp_path / "out"
    database = home / "research.sqlite3"
    before = database.read_bytes()
    # A person points at a run the way they have it: here the page URL.
    assert audit.main(["--run", f"http://127.0.0.1:3100/workspace/deepresearch/{RUN}", "--data-dir", str(home), "--out", str(out)]) == 0
    facts = json.loads((out / "audit-11111111.json").read_text(encoding="utf-8"))["run"]
    assert facts["identity"]["run_id"] == RUN and facts["identity"]["status"] == "COMPLETED"
    # The numbers are the product's own summary; the script adds the per-thread view.
    assert facts["summary"]["tokens"]["input"] == 9000 and facts["summary"]["tokens"]["cache_read_ratio"] == 0.228
    (loop,) = facts["threads"]
    assert (loop["node"], loop["unit_id"], loop["turns"], loop["first_input"], loop["last_input"]) == ("research", "R1", 2, 3000, 6000)
    # The provider served less than the previous prompt, and the request shows why.
    assert (loop["turns_checked"], loop["turns_served_from_cache"]) == (1, 0)
    breaks = facts["prompt_cache"]["prefix_breaks"]
    assert (breaks["turns_compared"], breaks["breaks"]) == (1, 1)
    assert breaks["examples"][0]["shared_messages"] == 1 and breaks["examples"][0]["preview"].startswith("## Tool receipts")
    codes = {item["code"] for item in facts["findings"]}
    assert {"provider-failing", "slow-tools"} <= codes
    assert facts["process"]["draft_repairs"] == {"report-section-1": 1} and facts["process"]["open_gaps"] == [{"unit_id": "R1", "code": "open-questions"}]
    sheet = (out / "audit-11111111.md").read_text(encoding="utf-8")
    assert "## 1. 需要关注的发现" in sheet and "web_search/serper" in sheet and "## 4. 提示词缓存" in sheet
    assert "COMPLETED" in capsys.readouterr().out
    # An audit reads a store a gateway may be writing: no lock taken, no byte changed.
    assert database.read_bytes() == before and sorted(path.name for path in home.iterdir()) == ["research.sqlite3"]


def test_one_model_call_can_be_opened_and_replayed_from_the_store(tmp_path, capsys):
    # The scripts are command-line programs with their own event loop.
    home = asyncio.run(recorded_run(tmp_path))
    show = script("show_call")
    assert show.main(["--run", RUN[:8], "--data-dir", str(home)]) == 0
    listing = capsys.readouterr().out
    assert "call-1" in listing and "call-2" in listing and "research" in listing
    assert show.main(["--run", RUN[:8], "--data-dir", str(home), "--call", "call-2"]) == 0
    opened = capsys.readouterr().out
    # Only what is new to the model since its previous request is shown by default.
    assert "## Tool receipts" in opened and "NEW" in opened
    assert show.main(["--run", RUN[:8], "--data-dir", str(home), "--call", "call-2", "--request"]) == 0
    request = json.loads(capsys.readouterr().out)
    assert request["model"] == "flash" and [message["role"] for message in request["messages"]][:2] == ["system", "user"]
