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


def evidence(raw_id, url):
    return {"raw_id": raw_id, "title": raw_id, "url": url, "origin": "external", "source_name": "web-read", "publisher": "example", "snippet": f"text of {raw_id}", "provenance": "fetched_document"}


async def supplemented_run(tmp_path):
    """A planned step held up by one search timeout, then a supplement round the report cites."""
    from deepresearch.evidence import merge_results, valid_result

    home = tmp_path / "live-rounds" / "research"
    store = Store(home / "research.sqlite3")
    await store.start()
    units = [
        {"id": "R1", "skill": "technical-route", "title": "Compare", "objective": "Compare the options"},
        {"id": "S1-aaaa", "skill": "technical-route", "title": "Compare", "objective": "Fill the gap", "depends_on": ["R1"], "parent_gap_id": "R1-open-questions"},
    ]

    def finding(claim, *refs):
        return {"claim": claim, "raw_evidence_refs": list(refs), "confidence": 0.8}

    known, unused, added = evidence("doc_a", "https://a.example/1"), evidence("doc_b", "https://b.example/1"), evidence("doc_c", "https://c.example/1")
    results = [
        {"unit_id": "R1", "findings": [finding("first", "doc_a"), finding("unused", "doc_b")], "raw_evidences": [known, unused], "open_questions": ["q1", "q2"], "confidence": 0.7},
        # A supplement carries the evidence of the step it extends and adds its own.
        {"unit_id": "S1-aaaa", "findings": [finding("second", "doc_a", "doc_c")], "raw_evidences": [known, added], "open_questions": ["q3"], "confidence": 0.7},
    ]
    first, _, _ = merge_results([valid_result(results[0])[0]])
    pool, _, lineage = merge_results([valid_result(body)[0] for body in results], first)
    cited = {lineage["R1:doc_a"]: 1, lineage["S1-aaaa:doc_c"]: 2}
    run = {
        "run_id": RUN,
        "thread_id": "dr-" + RUN,
        "owner": "u",
        "query": "Compare vector databases",
        "status": "COMPLETED",
        "created_at": "2026-09-19T10:00:00+00:00",
        "cycle": 0,
        "iteration": 1,
        "budget": {"max_iterations": 1, "max_units": 8, "max_model_tokens": None, "max_tool_calls": None, "max_elapsed_seconds": None},
        "usage": {"model_tokens": 0, "tool_calls": 0},
        "units": units,
        "unit_statuses": {"R1": "COMPLETED", "S1-aaaa": "COMPLETED"},
        "gaps": [],
        "conversation": [{"id": "initial", "role": "user", "text": "Compare vector databases"}],
        "report": {"citation_map": cited, "citations": [{"number": 1, "url": "https://a.example/1"}, {"number": 2, "url": "https://c.example/1"}], "markdown": "# Report"},
    }
    await store.create(run, "key", "hash")
    await store.save_pool(RUN, pool, [])
    for cycle_unit, body in zip(("0:R1", "0:S1-aaaa"), results, strict=True):
        await store.save_unit(RUN, cycle_unit, "h", body)
    for execution, unit, minute in (("exec-1", "R1", 1), ("exec-2", "S1-aaaa", 4)):
        details = {"config_node": "research", "purpose": "agent", "phase": "dispatch", "skill": "technical-route", "unit_id": unit, "execution_id": execution, "engine_node": "model", "model": "flash", "status": "ok"}
        # Two turns with the tools of the first in between; the model resumes when the slowest tool returns.
        for index, (start, end) in enumerate(((0, 5), (45, 50)), 1):
            clock = {"started_at": f"2026-09-19T10:0{minute}:{start:02d}+00:00", "ended_at": f"2026-09-19T10:0{minute}:{end:02d}+00:00", "duration_ms": 5000}
            usage = {"input_tokens": 1000 * index, "output_tokens": 100, "total_tokens": 1000 * index + 100, "cache_read_tokens": 0, "usage_reported": True, "finish_reason": "stop"}
            await store.record_model_call(RUN, f"{execution}-{index}", details | clock | usage)
        clock = {"started_at": f"2026-09-19T10:0{minute}:00+00:00", "ended_at": f"2026-09-19T10:0{minute}:50+00:00", "duration_ms": 50000}
        await store.record_agent_run(RUN, execution, details | clock | {"status": "completed", "model_calls": 2, "tool_calls": 3})
    tool = {"phase": "dispatch", "config_node": "research", "unit_id": "R1", "execution_id": "exec-1", "started_at": "2026-09-19T10:01:06+00:00"}
    search, read = {"tool_name": "web_search", "role": "search"}, {"tool_name": "web_fetch", "role": "read", "status": "success", "duration_ms": 2000}
    timeout = [{"provider": "duckduckgo", "status": "error", "kind": "timeout"}]
    await store.record_call(RUN, "t1", tool | search | {"status": "success", "duration_ms": 5000, "query": "vector database benchmark", "attempts": [{"provider": "duckduckgo", "status": "ok"}]})
    await store.record_call(RUN, "t2", tool | search | {"status": "error", "error_type": "ToolReturnedError", "duration_ms": 35000, "query": "qdrant sla", "attempts": timeout})
    await store.record_call(RUN, "t3", tool | read | {"url": "https://a.example/1"})
    await store.record_call(RUN, "t4", tool | read | {"url": "https://c.example/1", "unit_id": "S1-aaaa", "execution_id": "exec-2", "started_at": "2026-09-19T10:04:06+00:00"})
    await store.event(RUN, "trace.ended", {"span_id": "s1", "kind": "node", "name": "dispatch", "status": "ok", "duration_ms": 50000})
    return home


def test_a_failed_search_is_priced_on_the_clock_and_a_supplement_round_by_what_the_report_cites(tmp_path):
    home = asyncio.run(supplemented_run(tmp_path))
    audit = script("audit_run")
    out = tmp_path / "out"
    assert audit.main(["--run", RUN, "--data-dir", str(home), "--out", str(out)]) == 0
    facts = json.loads((out / "audit-11111111.json").read_text(encoding="utf-8"))["run"]
    # The turn waited 35 s for a timeout while its other calls took 5 s: a working search costs the median answered one.
    held = next(row for row in facts["tool_waits"] if row["unit_id"] == "R1")
    assert (held["batches"], held["delayed_batches"], held["failed_calls"]) == (1, 1, 1)
    assert (held["wait_seconds"], held["wait_seconds_without_failures"], held["lost_seconds"]) == (35.0, 5.0, 30.0)
    assert next(row for row in facts["tool_waits"] if row["unit_id"] == "S1-aaaa")["lost_seconds"] == 0
    plan, supplement = facts["rounds"]
    assert (plan["round"], plan["units"], supplement["round"], supplement["units"]) == (0, ["R1"], 1, ["S1-aaaa"])
    assert (plan["wall_seconds"], plan["estimated_wall_seconds_without_failed_calls"]) == (50.0, 20.0)
    # Evidence belongs to the round that first read it, so what a supplement inherits is not its yield.
    assert (plan["evidence_first_seen"], plan["cited_evidence_first_seen"], plan["cited_sources_first_seen"]) == (2, 1, 1)
    assert (supplement["evidence_first_seen"], supplement["cited_evidence_first_seen"], supplement["cited_sources_first_seen"]) == (1, 1, 1)
    assert (plan["findings"], plan["cited_findings"], supplement["findings"], supplement["cited_findings"]) == (2, 1, 1, 1)
    assert (plan["searches"], plan["reads"], plan["tool_errors"], plan["input_tokens"], supplement["input_tokens"]) == (2, 1, 1, 3000, 3000)
    assert "supplement-yield" in {item["code"] for item in facts["findings"]}
    # The finding on failed calls quotes the clock, not only the sum of their durations.
    assert "多等了 30 秒" in audit.lost_on_the_clock(facts) and "第 0 轮 50 → 20 秒" in audit.lost_on_the_clock(facts)
    # Every stage carries its time, tokens and tool use.
    dispatch = next(row for row in facts["phases"] if row["phase"] == "dispatch")
    assert (dispatch["model_calls"], dispatch["input_tokens"], dispatch["tool_calls"], dispatch["searches"], dispatch["failed_searches"], dispatch["reads"]) == (4, 6000, 4, 2, 1, 2)
    assert facts["search"]["failed_searches"][-1] == 1 and facts["search"]["answered_p50_seconds"] == 5.0
    assert [role["unit_id"] for role in facts["timeline"]["roles"]] == ["R1", "S1-aaaa"] and facts["timeline"]["roles"][0]["tools"][1][3] is False
    assert facts["tool_log"][1]["attempts"] == "duckduckgo:error/timeout" and facts["tool_log"][1]["target"] == "qdrant sla"
    sheet = (out / "audit-11111111.md").read_text(encoding="utf-8")
    assert "研究轮次的成本与产出" in sheet and "白等秒" in sheet


def test_the_audit_is_also_one_offline_page_led_by_the_written_report(tmp_path, capsys):
    home = asyncio.run(supplemented_run(tmp_path))
    audit = script("audit_run")
    out = tmp_path / "out"
    arguments = ["--run", RUN, "--data-dir", str(home), "--out", str(out)]
    assert audit.main(arguments) == 0
    page = (out / "audit-11111111.html").read_text(encoding="utf-8")
    assert "审计报告-11111111.md" in capsys.readouterr().out and "id='conclusion'" not in page
    for section in ("timeline", "phases", "nodes", "steps", "rounds", "cache", "tools", "log"):
        assert f"id='{section}'" in page
    assert "<svg" in page and "补研第 1 轮" in page and "qdrant sla" in page
    # It has to open on an intranet and from a file: nothing is fetched.
    assert "http://" not in page.replace("http://www.w3.org", "") and "src=" not in page and "<link" not in page
    # The auditor's conclusions lead the page once they are written next to it.
    (out / "审计报告-11111111.md").write_text("# 审计报告\n\n1. **搜索超时**拖住了 `R1`。\n\n| 改什么 | 预期 |\n| --- | --- |\n| 换供应商 | <更快> |\n", encoding="utf-8")
    assert audit.main(arguments) == 0
    page = (out / "audit-11111111.html").read_text(encoding="utf-8")
    assert "id='conclusion'" in page and "<strong>搜索超时</strong>" in page and "<code>R1</code>" in page and "&lt;更快&gt;" in page
    # The skill folder is shared documentation: rendering the page leaves no bytecode in it.
    assert not (SCRIPTS / "__pycache__").exists()


def test_a_run_with_nothing_metered_still_gets_its_page(tmp_path):
    # The demo backend and runs older than the metering record no model or tool calls.
    async def bare():
        home = tmp_path / "live-bare" / "research"
        store = Store(home / "research.sqlite3")
        await store.start()
        budget = {"max_iterations": 1, "max_units": 8, "max_model_tokens": None, "max_tool_calls": None, "max_elapsed_seconds": None}
        failure = {"status": "FAILED", "error": {"code": "NO_EVIDENCE", "message": "nothing"}}
        await store.create({"run_id": RUN, "thread_id": "dr-" + RUN, "owner": "u", "query": "demo", "created_at": "2026-09-19T10:00:00+00:00", "budget": budget} | failure, "key", "hash")
        return home

    home, out = asyncio.run(bare()), tmp_path / "out"
    assert script("audit_run").main(["--run", "latest", "--data-dir", str(home), "--out", str(out)]) == 0
    page = (out / "audit-11111111.html").read_text(encoding="utf-8")
    assert "NO_EVIDENCE" in page and "没有模型调用记录" in page and "没有工具调用" in page
