#!/usr/bin/env python3
# ruff: noqa: E501 - table rows are easier to compare on one line.
"""Look inside the model calls of one DeepResearch run: what a model was sent and what it answered.

List the calls, then open one. Works offline on the research store (the same
records the page's "LLM 调用" tab shows), so it also works for a run whose
gateway is no longer running.

    backend/.venv/bin/python .agents/skills/deepresearch-engineering/scripts/show_call.py --run 244cd6d5
    ... --run 244cd6d5 --node section --unit report-section-7
    ... --run 244cd6d5 --tools [--unit benchmarks]     # tool calls: status, seconds, provider attempts
    ... --run 244cd6d5 --call 01a0b9d1-48b3-7891-9234-a7e88e615d7d [--full] [--request]

``--request`` prints the call as an OpenAI-compatible Chat Completions body, to replay
it against a model with curl. Credentials were removed when the call was recorded.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # reading a run must leave the repository untouched

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_run import databases, find_repo, open_store, resolve  # noqa: E402


def clip(text, limit):
    """Head and tail of a long text: a task's instructions and repair notes sit at its end."""
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    if limit is None or len(text) <= limit:
        return text
    head, tail = limit * 3 // 5, limit * 2 // 5
    return f"{text[:head]}\n…[{len(text) - limit} characters hidden; --full shows them]…\n{text[-tail:]}"


def body_of(message):
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(str(part.get("text") or part) if isinstance(part, dict) else str(part) for part in content)
    return content or ""


async def main_async(args, database, run_id):
    from deepresearch.audit import openai_request

    store = open_store(database)
    if args.tools:
        print(f"{'started':8} {'unit':22} {'tool':12} {'status':8} {'sec':>6} {'chars':>7}  error / provider attempts / query")
        for call in await store.calls(run_id):
            if args.unit and call.get("unit_id") != args.unit:
                continue
            attempts = " ".join(f"{item.get('provider')}:{item.get('status')}{('/' + item['kind']) if item.get('kind') else ''}" for item in call.get("attempts") or [])
            text = " ".join(filter(None, [call.get("error_type"), attempts, (call.get("query") or call.get("url") or "")[:80]]))
            print(
                f"{(call.get('started_at') or '')[11:19]:8} {(call.get('unit_id') or '')[:22]:22} {(call.get('tool_name') or '')[:12]:12} {(call.get('status') or '')[:8]:8} {(call.get('duration_ms') or 0) / 1000:>6.1f} {call.get('output_chars') or 0:>7}  {text}"
            )
        return 0
    if not args.call:
        calls = {call["id"]: call for call in await store.model_calls(run_id)}
        turns = {}
        print(f"{'call_id':38} {'node':11} {'unit':22} {'turn':>4} {'msgs':>4} {'input':>7} {'cached':>7} {'output':>6} {'sec':>6}  finish")
        for item in await store.llm_exchanges(run_id):
            node = item.get("config_node") or item.get("purpose") or ""
            if args.node and node != args.node or args.unit and item.get("unit_id") != args.unit:
                continue
            turns[item.get("group")] = turn = turns.get(item.get("group"), 0) + 1
            usage = calls.get(item["id"], {})
            print(
                f"{item['id']:38} {node:11} {(item.get('unit_id') or '')[:22]:22} {turn:>4} {item.get('message_count') or 0:>4} {usage.get('input_tokens') or 0:>7} {usage.get('cache_read_tokens') or 0:>7} {usage.get('output_tokens') or 0:>6} {(item.get('duration_ms') or 0) / 1000:>6.1f}  {item.get('finish_reason') or item.get('status') or ''}"
            )
        return 0
    detail = await store.llm_exchange(run_id, args.call)
    if detail is None:
        raise SystemExit(f"Run {run_id[:8]} has no audited call {args.call}. Audit needs trace_capture_content and llm_audit on.")
    if args.request:
        print(json.dumps(openai_request(detail), ensure_ascii=False, indent=1))
        return 0
    limit = None if args.full else args.max_chars
    head = {key: detail.get(key) for key in ("id", "config_node", "purpose", "skill", "unit_id", "model", "response_model", "status", "finish_reason", "duration_ms", "started_at") if detail.get(key) is not None}
    print(json.dumps(head, ensure_ascii=False))
    print("params:", json.dumps(detail.get("params"), ensure_ascii=False))
    print("usage :", json.dumps(detail.get("usage"), ensure_ascii=False))
    print("tools :", [(tool.get("function") or tool).get("name") for tool in detail.get("tools") or []])
    new = set(detail.get("new_message_indexes") or [])
    messages = detail.get("messages") or []
    print(f"\n{len(messages)} messages; {detail.get('repeated_prefix', 0)} repeat the previous request of this thread, the rest are new to the model.\n")
    for index, message in enumerate(messages):
        if message is None:
            continue
        if not args.all_messages and new and index not in new and index != 0:
            continue
        mark = "NEW" if index in new else "   "
        text = body_of(message)
        print(f"--- [{index}] {mark} {message.get('role')}{' ' + message['name'] if message.get('name') else ''} · {len(text)} chars")
        print(clip(text, limit))
        for call in message.get("tool_calls") or []:
            print(f"    tool_call {call.get('name')}({clip(json.dumps(call.get('args'), ensure_ascii=False), 400)})")
    if not args.all_messages and new:
        print("\n(messages repeated from the previous request are hidden; --all-messages shows them)")
    print("\n=== response")
    for generation in (detail.get("response") or {}).get("generations") or []:
        if generation.get("reasoning"):
            print("[reasoning]", clip(generation["reasoning"], limit))
        print(clip(body_of(generation), limit))
        for call in generation.get("tool_calls") or []:
            print(f"    tool_call {call.get('name')}({clip(json.dumps(call.get('args'), ensure_ascii=False), 400)})")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="Run id, id prefix, research page URL, thread id, or 'latest'")
    parser.add_argument("--call", help="Call id to open; without it the calls are listed")
    parser.add_argument("--node", help="List only this node (rewrite, plan, research, conversion, outline, section, summary, revision, follow_up)")
    parser.add_argument("--unit", help="List only this research step or writer task")
    parser.add_argument("--tools", action="store_true", help="List the run's tool calls (status, seconds, error type, provider attempts) instead of its model calls")
    parser.add_argument("--all-messages", action="store_true", help="Also print the messages repeated from the previous request")
    parser.add_argument("--max-chars", type=int, default=1500, help="Characters shown per message (default 1500)")
    parser.add_argument("--full", action="store_true", help="Print every message completely")
    parser.add_argument("--request", action="store_true", help="Print the call as a Chat Completions request body")
    parser.add_argument("--data-dir", action="append", default=[])
    parser.add_argument("--repo", type=Path)
    args = parser.parse_args(argv)
    repo = args.repo.resolve() if args.repo else find_repo(__file__)
    if repo is None:
        parser.error("Cannot find the repository (backend/deepresearch). Pass --repo.")
    sys.path.insert(0, str(repo / "backend"))
    found = resolve(args.run, databases(repo, args.data_dir))
    if found is None:
        raise SystemExit(f"No run matches '{args.run}'. Try audit_run.py --list or --data-dir.")
    return asyncio.run(main_async(args, *found))


if __name__ == "__main__":
    sys.exit(main())
