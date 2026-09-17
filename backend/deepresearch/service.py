"""Lifecycle, admission, ownership and checkpoint resume for the local worker."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from .contracts import TERMINAL, CreateResearch, ResearchError, ResearchPlan, utcnow
from .conversation import REVIEW_STATUSES, ConversationLifecycle
from .evidence import digest
from .favicons import Favicons
from .runner import DeerFlowRunner, DemoRunner
from .store import ProcessLock, Store
from .trace import LocalTrace, install_log, logger


class ResearchService(ConversationLifecycle):
    def __init__(self, settings, runner=None):
        self.settings = settings
        self.store = Store(settings.resolve(settings.data_dir) / "research.sqlite3")
        self.favicons = Favicons(self.store, enabled=settings.favicons)
        self.lock = ProcessLock(settings.resolve(settings.data_dir) / "worker.lock")
        if runner is not None:
            self.runner = runner
        elif settings.runner_factory:
            import importlib

            module, name = settings.runner_factory.rsplit(":", 1)
            self.runner = getattr(importlib.import_module(module), name)(settings, self.store)
        else:
            self.runner = (DemoRunner if settings.runner == "demo" else DeerFlowRunner)(settings, self.store)
        self.tasks = {}
        self.countdowns = {}
        self.stopping = False
        self.guard = asyncio.Lock()
        self.stack = AsyncExitStack()
        self.graph = None
        self.fingerprint = None
        self.log_handler = None

    async def start(self, deps=None):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        from .workflow import build_workflow

        if self.graph is not None:
            raise RuntimeError("Research service is already started")
        self.stopping = False
        await asyncio.to_thread(self.lock.acquire)
        try:
            await self.store.start()
            self.log_handler = install_log(self.settings.resolve(self.settings.data_dir))
            bodies = {name: await asyncio.to_thread(self.settings.read_skill, name) for name in self.settings.skills}
            self.fingerprint = digest([self.settings.model_dump(mode="json"), bodies])
            self.settings._skill_cache = bodies
            cp = await self.stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(self.settings.resolve(self.settings.data_dir) / "checkpoints.sqlite3")))
            await cp.setup()
            self.graph = build_workflow(self.settings, self.store, self.runner, cp)
            for run in await self.store.list():
                if run.get("cancel_requested") and run["status"] != "COMPLETED":
                    await self.store.patch(run["run_id"], status="CANCELLED", error=None, auto_start_at=None, auto_start_paused=True, pending_operation=None)
                elif run["status"] in REVIEW_STATUSES:
                    await self.store.patch(run["run_id"], auto_start_at=None, auto_start_paused=True)
                elif run["status"] not in TERMINAL:
                    await self.store.patch(run["run_id"], status="FAILED", error={"code": "PROCESS_INTERRUPTED", "message": "进程已重启，请重新提供凭据并恢复", "recoverable": True})
        except BaseException:
            self.graph = None
            self.stopping = True
            await self.stack.aclose()
            self._close_log()
            await asyncio.to_thread(self.lock.release)
            raise

    async def stop(self):
        self.stopping = True
        async with self.guard:
            await self._stop_locked()

    async def _stop_locked(self):
        timers = list(self.countdowns.values())
        for timer in timers:
            timer.cancel()
        await asyncio.gather(*timers, return_exceptions=True)
        self.countdowns.clear()
        running = list(self.tasks.items())
        for _, task in running:
            task.cancel()
        await asyncio.gather(*(t for _, t in running), return_exceptions=True)
        try:
            for run_id, _ in running:
                run = await self.store.get(run_id)
                if run["status"] not in TERMINAL and run["status"] not in REVIEW_STATUSES:
                    await self.store.patch(run_id, status="FAILED", error={"code": "PROCESS_INTERRUPTED", "message": "服务已停止，可从检查点恢复", "recoverable": True})
        finally:
            try:
                await self.stack.aclose()
            finally:
                self.graph = None
                await asyncio.to_thread(self.lock.release)
                self._close_log()

    def _close_log(self):
        if self.log_handler is not None:
            logger.removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = None

    def context(self, run, supplied=None):
        secrets = {key: os.environ[env] for key, env in self.settings.local_secret_env.items() if os.environ.get(env)}
        supplied = supplied or {}
        # Direct Python callers may still pass the old secret-only mapping.
        secrets.update(supplied.get("secrets", supplied) if "identity" not in supplied else supplied.get("secrets", {}))
        identity = supplied.get("identity", {})
        return {"user_id": run["owner"], "thread_id": run["thread_id"], "secrets": secrets, "user_role": identity.get("user_role"), "is_internal": identity.get("is_internal", False)}

    def config(self, run):
        return {"configurable": {"thread_id": run["thread_id"]}, "recursion_limit": 200, "callbacks": []}

    def _admit(self, run_id=None):
        if self.stopping:
            raise ResearchError("SERVICE_STOPPING", "研究服务正在停止，请稍后重试")
        if run_id in self.tasks:
            raise ResearchError("RUN_BUSY", "该任务正在执行，请勿重复提交")
        if len(self.tasks) >= self.settings.max_active_runs:
            raise ResearchError("CAPACITY", "研究执行容量已满，请稍后重试")

    async def create(self, owner, request: CreateResearch, key, secrets=None, *, authorize=None):
        async with self.guard:
            request_hash = digest(request.model_dump(mode="json"))
            existing = await self.store.by_request(owner, key, request_hash)
            if existing is not None:
                if authorize:
                    await authorize(existing)
                return existing
            for field, value in request.budget.model_dump().items():
                ceiling = getattr(self.settings.budget_ceiling, field)
                if ceiling is not None and (value is None or value > ceiling):
                    raise ResearchError("BUDGET_LIMIT", f"请求超过部署预算上限: {field}", recoverable=False)
            if not set(request.source_names).issubset({s.name for s in self.settings.sources}):
                raise ResearchError("SOURCE_UNKNOWN", "请求包含未知数据源", recoverable=False)
            self._admit()
            run_id = str(uuid4())
            now = utcnow()
            run = {
                **request.model_dump(mode="json"),
                "run_id": run_id,
                "thread_id": "dr-" + run_id,
                "owner": owner,
                "status": "CREATED",
                "created_at": now,
                "updated_at": now,
                "fingerprint": self.fingerprint,
                "plan": None,
                "conversation": [{"id": "initial", "role": "user", "kind": "text", "text": request.query, "at": now}],
                "cycle": 0,
                "auto_start_at": None,
                "auto_start_paused": False,
                "units": [],
                "unit_statuses": {},
                "evidence_count": 0,
                "iteration": 0,
                "gaps": [],
                "usage": {"tool_calls": 0, "model_tokens": 0, "reported_model_tokens": 0, "elapsed_seconds": 0},
                "report": None,
                "error": None,
                "limitations": [],
                "demo": self.settings.runner == "demo",
            }
            if authorize:
                await authorize(run)
            run, created = await self.store.create(run, key, request_hash)
            if created:
                await self.store.event(run_id, "run.created", {"thread_id": run["thread_id"], "demo": run["demo"]}, key="created")
                self._launch(run, {"run": run, "iteration": 0, "synthesis_repairs": 0}, secrets)
            return run

    async def decision(self, run_id, version, action, plan=None, secrets=None):
        async with self.guard:
            self._admit(run_id)
            run = await self.store.get(run_id)
            if run["status"] not in REVIEW_STATUSES or run["plan"]["plan_version"] != version:
                raise ResearchError("PLAN_VERSION", "计划已变化或不在待确认状态，请刷新")
            if action == "approve" and run["plan"].get("clarification_questions"):
                raise ResearchError("CLARIFICATION_REQUIRED", "请先回答澄清问题")
            if action == "edit":
                try:
                    proposed = ResearchPlan.model_validate(plan)
                    self.settings.check_plan(proposed, CreateResearch.model_validate({k: run[k] for k in ["query", "constraints", "source_names", "budget"]}).budget, run["source_names"])
                except ValueError:
                    raise ResearchError("PLAN_INVALID", "计划不符合已注册 Skill、数据源、依赖或预算约束", recoverable=False) from None
            decision = {"action": action, "plan": plan}
            if run["fingerprint"] != self.fingerprint:
                raise ResearchError("CONFIG_CHANGED", "配置已变化，请创建新任务", recoverable=False)
            operation = {"id": str(uuid4()), "kind": "decision", "decision": decision}
            updated = await self.store.patch(run_id, status="PLANNING" if action == "edit" else "RESEARCHING", auto_start_at=None, auto_start_paused=True, pending_operation=operation)
            self._cancel_countdown(run_id)
            self._launch(updated, self._operation_payload(updated), secrets)
            return updated

    def _operation_payload(self, run):
        """Reconstruct accepted control input; credentials are never journaled."""
        from langgraph.types import Command

        operation = run["pending_operation"]
        if operation["kind"] == "decision":
            return Command(resume={**operation["decision"], "operation_id": operation["id"]})
        return {"run": run, "input_mode": "follow_up", "operation_id": operation["id"]}

    async def retry(self, run_id, secrets=None, *, allow_limited_report=None):
        async with self.guard:
            self._admit(run_id)
            run = await self.store.get(run_id)
            error = run.get("error") or {}
            accepted_gaps = allow_limited_report is True and error.get("code") == "RESEARCH_GAPS"
            validation_retry = error.get("code") == "FINAL_VALIDATION"
            if run["status"] != "FAILED" or not (error.get("recoverable") or accepted_gaps or validation_retry):
                raise ResearchError("NOT_RETRYABLE", "该状态不可恢复，请检查限制或创建新任务", recoverable=False)
            snapshot = await self.graph.aget_state(self.config(run))
            initial = None if snapshot.values else {"run": run, "iteration": 0, "synthesis_repairs": 0}
            if run["fingerprint"] != self.fingerprint:
                raise ResearchError("CONFIG_CHANGED", "配置已变化，请创建新任务", recoverable=False)
            operation = run.get("pending_operation")
            if operation and snapshot.values.get("operation_id") != operation["id"]:
                initial = self._operation_payload(run)
            if validation_retry and snapshot.values:
                # Each explicit retry gets another bounded repair opportunity;
                # it never accepts a rejected draft or reruns research.
                await self.graph.aupdate_state(self.config(run), {"synthesis_repairs": 0})
                await self.store.event(run_id, "report.validation.retry", {"source": "owner_retry"})
            policy = {} if allow_limited_report is None else {"allow_limited_report": allow_limited_report}
            if validation_retry:
                policy["report_retry_generation"] = run.get("report_retry_generation", 0) + 1
            updated = await self.store.patch(run_id, status="RESEARCHING", error=None, cancel_requested=False, **policy)
            if allow_limited_report is not None:
                await self.store.event(run_id, "report.limitations.policy", {"allow_limited_report": allow_limited_report, "source": "owner_retry"})
            self._launch(updated, initial, secrets)
            return updated

    def _launch(self, run, payload, secrets):
        if self.stopping:
            raise ResearchError("SERVICE_STOPPING", "研究服务正在停止，请稍后重试")
        if run["fingerprint"] != self.fingerprint:
            raise ResearchError("CONFIG_CHANGED", "任务的 Skill/配置版本与当前部署不同，请恢复原配置或创建新任务", recoverable=False)
        self.tasks[run["run_id"]] = asyncio.create_task(self._drive(run, payload, self.context(run, secrets)), name="research-" + run["run_id"])

    async def _drive(self, run, payload, context):
        from langsmith import tracing_context

        start = time.monotonic()
        pending_plan = None
        try:
            ceiling = run["budget"]["max_elapsed_seconds"]
            remaining = None if ceiling is None else ceiling - run["usage"]["elapsed_seconds"]
            if remaining is not None and remaining <= 0:
                raise ResearchError("TIME_BUDGET", "研究执行时间预算已用尽", recoverable=False)
            with tracing_context(enabled=False):
                async with asyncio.timeout(remaining):
                    trace = LocalTrace(self.store, run["run_id"], self.settings, context.get("secrets", {}).values())
                    async with trace.span("workflow", "workflow", {"resume": payload is None}):
                        output = await self.graph.ainvoke(payload, config=self.config(run), context=context)
            operation_id = (run.get("pending_operation") or {}).get("id")

            def acknowledge(current):
                if operation_id and (current.get("pending_operation") or {}).get("id") == operation_id:
                    current.pop("pending_operation", None)

            await self.store.mutate(run["run_id"], acknowledge)
            if output.get("__interrupt__"):
                questions = output["plan"].get("clarification_questions", [])
                deadline = None if questions else (datetime.now(UTC) + timedelta(seconds=self.settings.plan_countdown_seconds)).isoformat()
                pending_plan = await self.store.patch(
                    run["run_id"], status="AWAITING_CLARIFICATION" if questions else "AWAITING_PLAN_CONFIRMATION", plan=output["plan"], units=output["units"], error=None, auto_start_at=deadline, auto_start_paused=bool(questions)
                )
                await self.store.event(run["run_id"], "plan.waiting_confirmation", {"plan_version": output["plan"]["plan_version"]}, key="waiting-" + str(output["plan"]["plan_version"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, ResearchError):
                error = {"code": exc.code, "message": str(exc), "recoverable": exc.recoverable}
            elif isinstance(exc, TimeoutError):
                error = {"code": "TIMEOUT", "message": "本阶段调用超时，可在剩余预算内恢复", "recoverable": True}
            else:
                error = {"code": "EXECUTION_FAILED", "message": "执行失败；检查模型/MCP/适配配置后恢复，不回显上游敏感错误", "recoverable": True}
            await self.store.patch(run["run_id"], status="FAILED", error=error)
            await self.store.event(run["run_id"], "run.failed", error)
        finally:
            elapsed = time.monotonic() - start
            try:
                await self.store.mutate(run["run_id"], lambda r: r["usage"].update(elapsed_seconds=r["usage"]["elapsed_seconds"] + elapsed))
            finally:
                self.tasks.pop(run["run_id"], None)
                try:
                    if pending_plan is not None and not self.stopping:
                        self._arm_countdown(pending_plan, context)
                finally:
                    context.get("secrets", {}).clear()

    async def cancel(self, run_id):
        async with self.guard:

            def request(run):
                if run["status"] != "COMPLETED":
                    run["cancel_requested"] = True

            current = await self.store.mutate(run_id, request)
            if current["status"] == "COMPLETED":
                return current
            self._cancel_countdown(run_id)
            task = self.tasks.get(run_id)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            run = await self.store.get(run_id)
            if run["status"] == "COMPLETED":
                return run
            statuses = {unit["id"]: run.get("unit_statuses", {}).get(unit["id"], "CANCELLED") for unit in run.get("units", [])}
            statuses = {key: "CANCELLED" if value not in {"COMPLETED", "FAILED"} else value for key, value in statuses.items()}
            result = await self.store.patch(run_id, status="CANCELLED", error=None, auto_start_at=None, auto_start_paused=True, pending_operation=None, unit_statuses=statuses)
            await self.store.event(run_id, "run.cancelled", key="cancelled")
            return result
