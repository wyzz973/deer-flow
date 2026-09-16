"""Authenticated HTTP API with durable, cursor-based SSE replay."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import ipaddress
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from .contracts import TERMINAL, ConversationMessage, CreateResearch, PlanDecision, PlanEdit, ResearchError, RetryResearch, utcnow


def build_router(service, *, local_demo=False, demo_origins=None):
    router = APIRouter(prefix="/api/deepresearch", tags=["deepresearch"])

    async def principal(request: Request):
        if local_demo:
            try:
                local = ipaddress.ip_address(request.client.host).is_loopback
            except (ValueError, AttributeError):
                local = False
            if not local or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise HTTPException(403, "Standalone demo is loopback-only")
            origin = request.headers.get("origin")
            allowed_origins = set(demo_origins or ["http://localhost:3000", "http://127.0.0.1:3000"])
            allowed_origins.add(str(request.base_url).rstrip("/"))
            if origin and origin not in allowed_origins:
                raise HTTPException(403, "Origin denied")
            return "local-demo"
        from deerflow_extension_api import resolve_principal

        user = resolve_principal(request)
        if user is None:
            raise HTTPException(401, "Authentication required")
        request.state.research_identity = {
            "user_role": "admin" if getattr(user, "is_admin", False) else next(iter(getattr(user, "roles", ())), "user"),
            "is_internal": getattr(user, "is_internal", False),
        }
        return str(user.user_id)

    async def can_read(request, run):
        if not service.settings.access_policy:
            return True
        module, name = service.settings.access_policy.rsplit(":", 1)
        policy = getattr(importlib.import_module(module), name)
        allowed = policy(request, run)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        return bool(allowed)

    async def owned(run_id: str, request: Request, owner: str = Depends(principal)):
        run = await service.store.get(run_id)
        # Owner mismatch is deliberately indistinguishable from a missing run.
        if run is None or run["owner"] != owner:
            raise HTTPException(404, "Research run not found")
        if not await can_read(request, run):
            raise HTTPException(403, "Source/report access revoked")
        return run

    def secrets(request):
        # Explicit headers only; never forward the browser's host session cookie by default.
        return {"secrets": {key: request.headers[header] for header, key in service.settings.request_secret_headers.items() if request.headers.get(header)}, "identity": getattr(request.state, "research_identity", {})}

    def public(run, report=True):
        body = {k: v for k, v in run.items() if k not in {"owner", "fingerprint", "pending_operation", "research_cycle_message_id"}}
        body["server_time"] = utcnow()
        if not report:
            body.pop("report", None)
            body.pop("conversation", None)
        return body

    async def invoke(awaitable):
        try:
            return public(await awaitable)
        except ResearchError as exc:
            conflict = exc.code in {"RUN_BUSY", "PLAN_VERSION", "NOT_RETRYABLE", "IDEMPOTENCY_CONFLICT", "CONFIG_CHANGED"}
            status = 503 if exc.code == "SERVICE_STOPPING" else 409 if conflict else 429 if exc.code == "CAPACITY" else 422
            raise HTTPException(status, {"code": exc.code, "message": str(exc), "recoverable": exc.recoverable}) from None

    @router.get("/capabilities")
    async def capabilities(owner=Depends(principal)):
        return {
            "mode": service.settings.runner,
            "ready": service.graph is not None and not service.stopping,
            "skills": {k: {"description": v.description, "agent": v.agent} for k, v in service.settings.skills.items()},
            "sources": [{"name": s.name, "origin": s.origin, "level": s.level} for s in service.settings.sources],
            "budget_ceiling": service.settings.budget_ceiling.model_dump(),
            "plan_countdown_seconds": service.settings.plan_countdown_seconds,
        }

    @router.post("", status_code=202)
    async def create(body: CreateResearch, request: Request, owner=Depends(principal), idempotency_key: str | None = Header(default=None)):
        if service.graph is None or service.stopping:
            raise HTTPException(503, "Research service is not ready")
        key = idempotency_key or str(uuid4())
        if len(key) > 200:
            raise HTTPException(422, "Idempotency-Key too long")

        async def authorize(run):
            if not await can_read(request, run):
                raise HTTPException(403, "Source/report access revoked")

        return await invoke(service.create(owner, body, key, secrets(request), authorize=authorize))

    @router.get("")
    async def list_runs(request: Request, owner=Depends(principal), limit: int = Query(default=50, ge=1, le=100)):
        # History contains goals/plan metadata too; revoked runs must not leak here.
        visible = []
        for run in await service.store.list(owner, limit):
            if await can_read(request, run):
                visible.append(public(run, report=False))
        return visible

    @router.get("/{run_id}")
    async def get_run(run=Depends(owned)):
        return public(run)

    @router.post("/{run_id}/plan/approve", status_code=202)
    async def approve(body: PlanDecision, request: Request, run=Depends(owned)):
        return await invoke(service.decision(run["run_id"], body.plan_version, "approve", secrets=secrets(request)))

    @router.post("/{run_id}/plan/pause")
    async def pause(body: PlanDecision, run=Depends(owned)):
        return await invoke(service.pause_plan(run["run_id"], body.plan_version))

    @router.post("/{run_id}/plan/resume")
    async def resume(body: PlanDecision, request: Request, run=Depends(owned)):
        return await invoke(service.resume_plan(run["run_id"], body.plan_version, secrets(request)))

    @router.post("/{run_id}/messages", status_code=202)
    async def message(body: ConversationMessage, request: Request, run=Depends(owned)):
        return await invoke(service.message(run["run_id"], body.text, body.client_message_id, plan_version=body.plan_version, secrets=secrets(request)))

    @router.get("/{run_id}/sources")
    async def sources(run=Depends(owned)):
        return {"sources": await service.store.sources(run["run_id"]), "calls": await service.store.calls(run["run_id"])}

    @router.post("/{run_id}/plan/edit", status_code=202)
    async def edit(body: PlanEdit, request: Request, run=Depends(owned)):
        return await invoke(service.decision(run["run_id"], body.plan_version, "edit", body.plan.model_dump(mode="json"), secrets(request)))

    @router.post("/{run_id}/plan/reject", status_code=202)
    async def reject(body: PlanDecision, request: Request, run=Depends(owned)):
        return await invoke(service.decision(run["run_id"], body.plan_version, "reject", secrets=secrets(request)))

    @router.post("/{run_id}/cancel")
    async def cancel(run=Depends(owned)):
        return await invoke(service.cancel(run["run_id"]))

    @router.post("/{run_id}/retry", status_code=202)
    async def retry(request: Request, body: RetryResearch | None = None, run=Depends(owned)):
        return await invoke(service.retry(run["run_id"], secrets(request), allow_limited_report=body.allow_limited_report if body else None))

    @router.get("/{run_id}/events")
    async def events(request: Request, run=Depends(owned), after: int = Query(default=0, ge=0), last_event_id: str | None = Header(default=None)):
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError:
            raise HTTPException(422, "Invalid Last-Event-ID") from None

        async def stream():
            nonlocal cursor
            ticks = 0
            while not await request.is_disconnected():
                current = await owned(run["run_id"], request, run["owner"])
                batch = await service.store.events(run["run_id"], cursor)
                for item in batch:
                    cursor = item["seq"]
                    visible = item
                    if item["type"].startswith("trace."):
                        visible = {**item, "data": {k: v for k, v in item["data"].items() if k != "payload"}}
                    yield f"id: {cursor}\nevent: message\ndata: {json.dumps(visible, ensure_ascii=False)}\n\n"
                if len(batch) == 200:
                    continue
                if current["status"] in TERMINAL and run["run_id"] not in service.tasks:
                    # Recheck for a terminal event committed after the first read.
                    remaining = await service.store.events(run["run_id"], cursor)
                    if remaining:
                        continue
                    return
                ticks += 1
                if ticks % 20 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.get("/{run_id}/evidences")
    async def evidences(run=Depends(owned)):
        snapshot = await service.graph.aget_state(service.config(run))
        return {"evidences": list(snapshot.values.get("evidence_pool", {}).values()), "lineage": snapshot.values.get("lineage", {})}

    @router.get("/{run_id}/trace")
    async def trace(after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200), run=Depends(owned)):
        items = await service.store.trace_events(run["run_id"], after, limit)
        return {"trace_id": run["run_id"], "items": items, "next_cursor": items[-1]["seq"] if items else after}

    @router.get("/{run_id}/trace/export")
    async def export_trace(request: Request, run=Depends(owned)):
        async def stream():
            cursor = 0
            while True:
                await owned(run["run_id"], request, run["owner"])
                items = await service.store.trace_events(run["run_id"], cursor, 200)
                if not items:
                    return
                for item in items:
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                cursor = items[-1]["seq"]

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="research-{run["run_id"]}-trace.jsonl"', "Cache-Control": "no-store"})

    @router.get("/{run_id}/report")
    async def report(format: Literal["json", "md", "html", "docx"] = "json", version: int | None = Query(default=None, ge=1), run=Depends(owned)):
        # Historical cards export their own immutable report, even while a
        # follow-up is running. Ownership and source ACLs still apply above.
        if version is not None:
            value = next((m["report"] for m in run.get("conversation", []) if m.get("report", {}).get("version") == version), None)
            if value is None and (run.get("report") or {}).get("version") == version:
                value = run["report"]
            if value is None:
                raise HTTPException(404, "Report version not found")
        else:
            if run["status"] != "COMPLETED" or not run.get("report"):
                raise HTTPException(409, "Report is not ready")
            value = run["report"]
        if format == "json":
            return value
        if format == "docx":
            from .render import docx_report

            data = await asyncio.to_thread(docx_report, value)
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            data = value["markdown" if format == "md" else "html"]
            media = "text/markdown" if format == "md" else "text/html"
        return Response(
            data, media_type=media, headers={"Content-Disposition": f'attachment; filename="research-{run["run_id"]}.{format}"', "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}
        )

    return router
