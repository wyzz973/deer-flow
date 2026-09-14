"""Lifecycle, admission, ownership and checkpoint resume for the local worker."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import AsyncExitStack
from uuid import uuid4

from .contracts import TERMINAL, CreateResearch, ResearchError, ResearchPlan, utcnow
from .evidence import digest
from .runner import DeerFlowRunner, DemoRunner
from .store import ProcessLock, Store
from .trace import LocalTrace, install_log, logger


class ResearchService:
    def __init__(self, settings, runner=None):
        self.settings = settings
        self.store = Store(settings.resolve(settings.data_dir) / "research.sqlite3")
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
        self.guard = asyncio.Lock()
        self.stack = AsyncExitStack()
        self.graph = None
        self.fingerprint = None
        self.log_handler = None

    async def start(self, deps=None):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        from .workflow import build_workflow

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
                if run["status"] not in TERMINAL and run["status"] != "AWAITING_PLAN_CONFIRMATION":
                    await self.store.patch(run["run_id"], status="FAILED", error={"code": "PROCESS_INTERRUPTED", "message": "进程已重启，请重新提供凭据并恢复", "recoverable": True})
        except BaseException:
            await self.stack.aclose()
            self._close_log()
            await asyncio.to_thread(self.lock.release)
            raise

    async def stop(self):
        running = list(self.tasks.items())
        for _, task in running:
            task.cancel()
        await asyncio.gather(*(t for _, t in running), return_exceptions=True)
        for run_id, _ in running:
            run = await self.store.get(run_id)
            if run["status"] not in TERMINAL and run["status"] != "AWAITING_PLAN_CONFIRMATION":
                await self.store.patch(run_id, status="FAILED", error={"code": "PROCESS_INTERRUPTED", "message": "服务已停止，可从检查点恢复", "recoverable": True})
        await self.stack.aclose()
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
        if run_id in self.tasks:
            raise ResearchError("RUN_BUSY", "该任务正在执行，请勿重复提交")
        if len(self.tasks) >= self.settings.max_active_runs:
            raise ResearchError("CAPACITY", "研究执行容量已满，请稍后重试")

    async def create(self, owner, request: CreateResearch, key, secrets=None):
        for field, value in request.budget.model_dump().items():
            if value > getattr(self.settings.budget_ceiling, field):
                raise ResearchError("BUDGET_LIMIT", f"请求超过部署预算上限: {field}", recoverable=False)
        if not set(request.source_names).issubset({s.name for s in self.settings.sources}):
            raise ResearchError("SOURCE_UNKNOWN", "请求包含未知数据源", recoverable=False)
        async with self.guard:
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
            run, created = await self.store.create(run, key, digest(request.model_dump(mode="json")))
            if created:
                await self.store.event(run_id, "run.created", {"thread_id": run["thread_id"], "demo": run["demo"]}, key="created")
                self._launch(run, {"run": run, "iteration": 0, "synthesis_repairs": 0}, secrets)
            return run

    async def decision(self, run_id, version, action, plan=None, secrets=None):
        from langgraph.types import Command

        async with self.guard:
            self._admit(run_id)
            run = await self.store.get(run_id)
            if run["status"] != "AWAITING_PLAN_CONFIRMATION" or run["plan"]["plan_version"] != version:
                raise ResearchError("PLAN_VERSION", "计划已变化或不在待确认状态，请刷新")
            if action == "edit":
                try:
                    proposed = ResearchPlan.model_validate(plan)
                    self.settings.check_plan(proposed, CreateResearch.model_validate({k: run[k] for k in ["query", "constraints", "source_names", "budget"]}).budget, run["source_names"])
                except ValueError:
                    raise ResearchError("PLAN_INVALID", "计划不符合已注册 Skill、数据源、依赖或预算约束", recoverable=False) from None
            decision = {"action": action, "plan": plan}
            if run["fingerprint"] != self.fingerprint:
                raise ResearchError("CONFIG_CHANGED", "配置已变化，请创建新任务", recoverable=False)
            updated = await self.store.patch(run_id, status="PLANNING" if action == "edit" else "RESEARCHING")
            self._launch(run, Command(resume=decision), secrets)
            return updated

    async def retry(self, run_id, secrets=None):
        async with self.guard:
            self._admit(run_id)
            run = await self.store.get(run_id)
            if run["status"] != "FAILED" or not (run.get("error") or {}).get("recoverable"):
                raise ResearchError("NOT_RETRYABLE", "该状态不可恢复，请检查限制或创建新任务", recoverable=False)
            snapshot = await self.graph.aget_state(self.config(run))
            initial = None if snapshot.values else {"run": run, "iteration": 0, "synthesis_repairs": 0}
            if run["fingerprint"] != self.fingerprint:
                raise ResearchError("CONFIG_CHANGED", "配置已变化，请创建新任务", recoverable=False)
            updated = await self.store.patch(run_id, status="RESEARCHING", error=None)
            self._launch(run, initial, secrets)
            return updated

    def _launch(self, run, payload, secrets):
        if run["fingerprint"] != self.fingerprint:
            raise ResearchError("CONFIG_CHANGED", "任务的 Skill/配置版本与当前部署不同，请恢复原配置或创建新任务", recoverable=False)
        self.tasks[run["run_id"]] = asyncio.create_task(self._drive(run, payload, self.context(run, secrets)), name="research-" + run["run_id"])

    async def _drive(self, run, payload, context):
        from langsmith import tracing_context

        start = time.monotonic()
        try:
            remaining = run["budget"]["max_elapsed_seconds"] - run["usage"]["elapsed_seconds"]
            if remaining <= 0:
                raise ResearchError("TIME_BUDGET", "研究执行时间预算已用尽", recoverable=False)
            with tracing_context(enabled=False):
                async with asyncio.timeout(remaining):
                    trace = LocalTrace(self.store, run["run_id"], self.settings, context.get("secrets", {}).values())
                    async with trace.span("workflow", "workflow", {"resume": payload is None}):
                        output = await self.graph.ainvoke(payload, config=self.config(run), context=context)
            if output.get("__interrupt__"):
                await self.store.patch(run["run_id"], status="AWAITING_PLAN_CONFIRMATION", plan=output["plan"], units=output["units"], error=None)
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
            context.get("secrets", {}).clear()
            elapsed = time.monotonic() - start
            await self.store.mutate(run["run_id"], lambda r: r["usage"].update(elapsed_seconds=r["usage"]["elapsed_seconds"] + elapsed))
            self.tasks.pop(run["run_id"], None)

    async def cancel(self, run_id):
        async with self.guard:
            task = self.tasks.get(run_id)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            run = await self.store.get(run_id)
            if run["status"] == "COMPLETED":
                return run
            result = await self.store.patch(run_id, status="CANCELLED", error=None)
            await self.store.event(run_id, "run.cancelled", key="cancelled")
            return result
