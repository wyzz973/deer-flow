"""Requests are built so a provider's prompt-prefix cache can serve them.

A cache reuses a prompt only from its first token on. These tests state that
property on the text a model receives: two calls of a node that differ only in
what is new to them share most of their task as a prefix.
"""

import json

import pytest

from deepresearch.runner import DeerFlowRunner, recent_messages
from deepresearch.structured import structured_task


class Captured(Exception):
    pass


def task_text(payload, schema):
    """What the role receives: native.py sends exactly this serialization."""
    return json.dumps(structured_task(payload, schema), ensure_ascii=False)


def shared(first, second):
    """Share of the shorter text that both open with."""
    count = next((index for index, (a, b) in enumerate(zip(first, second, strict=False)) if a != b), min(len(first), len(second)))
    return count / min(len(first), len(second))


def capturing(runner, monkeypatch):
    seen = []

    async def capture(run, skill, payload, schema, *args, **kwargs):
        seen.append(task_text(payload, schema))
        raise Captured

    monkeypatch.setattr(runner, "_json", capture)
    return seen


@pytest.mark.asyncio
async def test_planning_two_requests_shares_everything_a_deployment_keeps_constant(settings, monkeypatch):
    runner = DeerFlowRunner(settings, store=None)
    seen = capturing(runner, monkeypatch)
    for query in ("对比 2026 年主流的开源向量数据库，给出中型团队的选型建议", "Assess the migration from pip-tools to uv for a monorepo"):
        run = {"run_id": "r", "query": query, "constraints": [], "source_names": [], "budget": {"max_units": 8, "max_iterations": 1, "max_tool_calls": None, "max_model_tokens": None, "max_elapsed_seconds": None}}
        with pytest.raises(Captured):
            await runner.plan(run, request={"user_query": query + " (rewritten)"})
    # Instructions, the contract, roles, sources and limits lead; the request ends the task.
    assert shared(*seen) > 0.6
    assert seen[0].index('"output_schema"') < seen[0].index('"research_request"') < seen[0].index('"proposed_plan_to_normalize"')


@pytest.mark.asyncio
async def test_a_second_follow_up_reuses_the_report_the_first_one_sent(settings, monkeypatch):
    runner = DeerFlowRunner(settings, store=None)
    seen = capturing(runner, monkeypatch)
    report = {"document": "# 报告\n\n" + "正文段落。" * 2000}
    conversation = [{"id": "initial", "role": "user", "text": "对比向量数据库"}, {"id": "a1", "role": "assistant", "text": "报告已完成"}]
    for text in ("第二章的数据来自哪里？", "把结论改成表格"):
        conversation = [*conversation, {"id": text, "role": "user", "text": text}]
        with pytest.raises(Captured):
            await runner.respond({"run_id": "r", "plan": {"title": "向量数据库选型"}, "report": report, "conversation": conversation}, text)
        conversation = [*conversation, {"id": "re-" + text, "role": "assistant", "text": "回答"}]
    # All but the new message and the constant instructions behind it (kept last for a long task).
    assert shared(*seen) > 0.9
    # The new message is the last thing before the instructions, never the first.
    assert seen[0].index('"report"') < seen[0].index('"conversation"') < seen[0].index('"message"') < seen[0].index('"instructions"')


def test_a_conversation_window_moves_its_head_once_in_ten_messages():
    messages = [{"id": str(index)} for index in range(60)]
    heads = [recent_messages(messages[:length])[0]["id"] for length in range(1, 61)]
    # Short conversations are sent whole; afterwards the first message changes
    # at most once per ten messages instead of on every turn.
    assert heads[:29] == ["0"] * 29 and heads[29] == "10"
    assert sum(1 for before, after in zip(heads, heads[1:], strict=False) if before != after) == 4
    assert all(20 <= len(recent_messages(messages[:length])) <= 29 for length in range(20, 61))
