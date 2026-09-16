"""Conversation controls around the existing LangGraph workflow.

Auto-start is a server-owned deadline, not a browser timer that can reset on
render or race across tabs. Timer and manual actions enter the same guarded
decision path. Credentials stay in the timer's transient context and never in
the persisted plan; a process restart pauses outstanding approvals.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .contracts import ResearchError, utcnow

REVIEW_STATUSES = {"AWAITING_PLAN_CONFIRMATION", "EDITING_PLAN", "AWAITING_CLARIFICATION"}


class ConversationLifecycle:
    def _cancel_countdown(self, run_id):
        task = self.countdowns.pop(run_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _arm_countdown(self, run, context):
        self._cancel_countdown(run["run_id"])
        if not run.get("auto_start_at") or self.stopping:
            return
        credentials = {
            "secrets": dict(context.get("secrets") or {}),
            "identity": {"user_role": context.get("user_role"), "is_internal": context.get("is_internal", False)},
        }
        self.countdowns[run["run_id"]] = asyncio.create_task(
            self._countdown(run["run_id"], run["plan"]["plan_version"], run["auto_start_at"], credentials),
            name="research-countdown-" + run["run_id"],
        )

    async def _countdown(self, run_id, version, deadline, credentials):
        try:
            while not self.stopping:
                run = await self.store.get(run_id)
                if run["status"] != "AWAITING_PLAN_CONFIRMATION" or run.get("auto_start_paused") or run.get("auto_start_at") != deadline or run["plan"]["plan_version"] != version:
                    return
                remaining = (datetime.fromisoformat(deadline) - datetime.now(UTC)).total_seconds()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                try:
                    await self.decision(run_id, version, "approve", secrets=credentials)
                    await self.store.event(run_id, "plan.auto_started", {"plan_version": version}, key=f"auto-{version}")
                    return
                except ResearchError as exc:
                    if exc.code not in {"RUN_BUSY", "CAPACITY"}:
                        return
                    # Capacity delay does not grant a fresh countdown window.
                    await asyncio.sleep(0.2)
        finally:
            credentials["secrets"].clear()
            if self.countdowns.get(run_id) is asyncio.current_task():
                self.countdowns.pop(run_id, None)

    async def pause_plan(self, run_id, version):
        async with self.guard:
            run = await self.store.get(run_id)
            if run["status"] not in REVIEW_STATUSES or run["plan"]["plan_version"] != version:
                raise ResearchError("PLAN_VERSION", "计划已变化或研究已启动，请刷新")
            run = await self.store.patch(run_id, status="EDITING_PLAN", auto_start_at=None, auto_start_paused=True)
            self._cancel_countdown(run_id)
            await self.store.event(run_id, "plan.editing", {"plan_version": version})
            return run

    async def resume_plan(self, run_id, version, secrets=None):
        async with self.guard:
            run = await self.store.get(run_id)
            if run["status"] != "EDITING_PLAN" or run["plan"]["plan_version"] != version:
                raise ResearchError("PLAN_VERSION", "计划已变化，请刷新")
            if run["plan"].get("clarification_questions"):
                raise ResearchError("CLARIFICATION_REQUIRED", "请先回答澄清问题")
            deadline = (datetime.now(UTC) + timedelta(seconds=self.settings.plan_countdown_seconds)).isoformat()
            run = await self.store.patch(run_id, status="AWAITING_PLAN_CONFIRMATION", auto_start_at=deadline, auto_start_paused=False)
            context = self.context(run, secrets)
            self._arm_countdown(run, context)
            context["secrets"].clear()
            await self.store.event(run_id, "plan.countdown_resumed", {"plan_version": version, "auto_start_at": deadline})
            return run

    async def message(self, run_id, text, client_message_id, *, plan_version=None, secrets=None):
        async with self.guard:
            run = await self.store.get(run_id)
            previous = next((m for m in run.get("conversation", []) if m["id"] == client_message_id), None)
            if previous:
                if previous["role"] != "user" or previous["id"] == "initial" or previous["text"] != text or previous.get("request_plan_version") != plan_version:
                    raise ResearchError("IDEMPOTENCY_CONFLICT", "同一消息幂等键不能绑定不同内容", recoverable=False)
                return run
            self._admit(run_id)
            if run["fingerprint"] != self.fingerprint:
                raise ResearchError("CONFIG_CHANGED", "配置已变化，请创建新研究", recoverable=False)
            if run["status"] not in REVIEW_STATUSES | {"COMPLETED"}:
                raise ResearchError("RUN_BUSY", "当前研究正在执行，请等待完成或先停止研究")
            editing = run["status"] in REVIEW_STATUSES
            if editing and plan_version != run["plan"]["plan_version"]:
                raise ResearchError("PLAN_VERSION", "计划版本已变化，请刷新后再修改")
            operation = {"id": str(uuid4()), "kind": "decision" if editing else "follow_up"}
            if editing:
                operation["decision"] = {"action": "edit", "plan": run["plan"], "revision": text}

            def accept(current):
                current.setdefault("conversation", []).append({"id": client_message_id, "role": "user", "kind": "text", "text": text, "at": utcnow(), "request_plan_version": plan_version})
                current.update(status="PLANNING" if editing else "RESPONDING", auto_start_at=None, auto_start_paused=True, error=None, pending_operation=operation)

            # One transaction owns acceptance and its pending graph input. A
            # restart between this write and task submission can replay it.
            run = await self.store.mutate(run_id, accept)
            self._cancel_countdown(run_id)
            payload = self._operation_payload(run)
            await self.store.event(run_id, "conversation.message", {"message_id": client_message_id})
            self._launch(run, payload, secrets)
            return run
