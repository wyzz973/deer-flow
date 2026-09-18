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
from pydantic import BaseModel, Field, ValidationError

from .contracts import TERMINAL, ConversationMessage, CreateResearch, PlanDecision, PlanEdit, ResearchError, RetryResearch, utcnow


class SettingsUpdate(BaseModel):
    version: int = Field(ge=0)
    settings: dict


class SettingsReset(BaseModel):
    version: int = Field(ge=0)
    fields: list[str] | None = None


class SettingsRestore(BaseModel):
    version: int = Field(ge=0)
    target_version: int = Field(ge=1)


class SecretUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    value: str | None = Field(default=None, max_length=20000)


class ModelTest(BaseModel):
    model: dict


class ProviderTest(BaseModel):
    source: dict
    provider_id: str
    query: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=4000)
    mcp_servers: dict | None = None


class McpToolsRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    server: dict


def validation_detail(error):
    """Field paths and messages only; submitted values may contain credentials."""
    errors = [{"field": ".".join(str(part) for part in item.get("loc", ())), "message": item.get("msg", "")} for item in error.errors(include_input=False, include_url=False)[:30]]
    return {"code": "SETTINGS_INVALID", "message": "研究设置未通过校验", "errors": errors}


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
            request.state.research_admin = True
            return "local-demo"
        from deerflow_extension_api import resolve_principal

        user = resolve_principal(request)
        if user is None:
            raise HTTPException(401, "Authentication required")
        request.state.research_identity = {
            "user_role": "admin" if getattr(user, "is_admin", False) else next(iter(getattr(user, "roles", ())), "user"),
            "is_internal": getattr(user, "is_internal", False),
        }
        request.state.research_admin = bool(getattr(user, "is_admin", False))
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

    @router.get("/favicon")
    async def favicon(domain: str = Query(min_length=1, max_length=253), owner=Depends(principal)):
        # Declared before /{run_id}. Icons are shared public site assets, but the
        # route still requires a user so it cannot serve as an open fetch proxy.
        from .favicons import normalize_domain

        host = normalize_domain(domain)
        if host is None:
            raise HTTPException(422, "Invalid domain")
        icon = await service.favicons.get(host)
        headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"}
        if icon.get("pending"):
            # Still fetching in the background; the browser must ask again later.
            return Response(status_code=404, headers={**headers, "Cache-Control": "no-store"})
        if not icon.get("body"):
            return Response(status_code=404, headers={**headers, "Cache-Control": "private, max-age=86400"})
        return Response(icon["body"], media_type=icon["content_type"], headers={**headers, "Cache-Control": "private, max-age=604800"})

    async def administrator(request: Request, owner: str = Depends(principal)):
        # Research settings affect every user's research: administrators only.
        if not getattr(request.state, "research_admin", False):
            raise HTTPException(403, "Administrator access is required to change research settings")
        return owner

    async def settings_view(request):
        from . import profile
        from .catalog import settings_catalog
        from .channels import HEALTH
        from .secrets import SECRETS, references

        current = await asyncio.to_thread(profile.editable_view, service.settings)
        stored = await service.store.profile()
        return {
            "version": service.profile_version,
            "editable": bool(getattr(request.state, "research_admin", False)),
            "error": service.profile_error,
            "settings": current,
            "defaults": await asyncio.to_thread(profile.editable_view, service.operator),
            "overridden": sorted(((stored or {}).get("overrides") or {}).keys()),
            "updated_at": (stored or {}).get("updated_at"),
            "operator": {
                "runner": service.operator.runner,
                "max_active_runs": service.operator.max_active_runs,
                "favicons": service.operator.favicons,
                "budget_ceiling": service.operator.budget_ceiling.model_dump(),
                "native_tools": service.operator.native_tools,
            },
            "catalog": settings_catalog(),
            "secrets": {"saved": SECRETS.names(), "references": {reference: SECRETS.status(reference) for reference in sorted(references(current))}},
            "health": HEALTH.snapshot(),
        }

    async def settings_call(request, awaitable):
        try:
            await awaitable
        except ValidationError as exc:
            raise HTTPException(422, validation_detail(exc)) from None
        except ResearchError as exc:
            raise HTTPException(409 if exc.code == "PROFILE_VERSION" else 422, {"code": exc.code, "message": str(exc), "recoverable": exc.recoverable}) from None
        except ValueError as exc:
            raise HTTPException(422, {"code": "SETTINGS_INVALID", "message": str(exc)[:2000]}) from None
        return await settings_view(request)

    @router.get("/settings")
    async def get_settings(request: Request, owner=Depends(principal)):
        return await settings_view(request)

    @router.post("/settings")
    async def update_settings(body: SettingsUpdate, request: Request, owner=Depends(administrator)):
        return await settings_call(request, service.update_profile(body.settings, body.version, owner))

    @router.post("/settings/reset")
    async def reset_settings(body: SettingsReset, request: Request, owner=Depends(administrator)):
        return await settings_call(request, service.reset_profile(body.fields, body.version, owner))

    @router.post("/settings/restore")
    async def restore_settings(body: SettingsRestore, request: Request, owner=Depends(administrator)):
        return await settings_call(request, service.restore_profile(body.target_version, body.version, owner))

    @router.get("/settings/history")
    async def settings_history(owner=Depends(principal)):
        items = await service.store.profile_history()
        return {"items": [{"version": item["version"], "updated_at": item["updated_at"], "updated_by": item["updated_by"], "fields": sorted(item["overrides"])} for item in items]}

    @router.post("/settings/secrets")
    async def update_secret(body: SecretUpdate, request: Request, owner=Depends(administrator)):
        return await settings_call(request, service.save_secret(body.name, body.value, owner))

    @router.post("/settings/test-model")
    async def test_model(body: ModelTest, owner=Depends(administrator)):
        from .config import ModelSpec
        from .doctor import probe_model

        try:
            spec = ModelSpec.model_validate(body.model)
        except ValidationError as exc:
            raise HTTPException(422, validation_detail(exc)) from None
        candidate = service.settings.model_copy(update={"models": [spec], "default_model": spec.name})
        return await probe_model(spec.name, timeout=120, settings=candidate)

    @router.post("/settings/test-provider")
    async def test_source_provider(body: ProviderTest, owner=Depends(administrator)):
        from .channels import test_provider
        from .config import McpServerSpec, SourceSpec

        try:
            source = SourceSpec.model_validate(body.source)
            servers = {name: McpServerSpec.model_validate(value) for name, value in (body.mcp_servers or {}).items()} if body.mcp_servers is not None else service.settings.mcp_servers
        except ValidationError as exc:
            raise HTTPException(422, validation_detail(exc)) from None
        provider = next((item for item in source.providers if item.id == body.provider_id), None)
        if provider is None:
            raise HTTPException(404, "Provider not found in the submitted source")
        candidate = service.settings.model_copy(update={"mcp_servers": servers})
        return await test_provider(source, provider, candidate, query=body.query, url=body.url)

    @router.post("/settings/mcp-tools")
    async def list_mcp_tools(body: McpToolsRequest, owner=Depends(administrator)):
        from .config import McpServerSpec
        from .mcp import MANAGER

        try:
            spec = McpServerSpec.model_validate(body.server)
        except ValidationError as exc:
            raise HTTPException(422, validation_detail(exc)) from None
        try:
            tools = await MANAGER.tools(body.name, spec, refresh=True)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "tools": []}
        return {"ok": True, "tools": [{"name": tool.name, "description": (tool.description or "")[:1000], "arguments": getattr(tool, "args", {}) or {}} for tool in tools]}

    @router.get("/settings/health")
    async def provider_health(owner=Depends(principal)):
        from .channels import HEALTH

        return {"providers": HEALTH.snapshot()}

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

    @router.get("/{run_id}/activity")
    async def research_activity(run=Depends(owned)):
        # A concise user-facing projection; the full trace stays separate.
        from .activity import build, tool_roles

        events = await service.store.activity_events(run["run_id"])
        calls = await service.store.calls(run["run_id"])
        return build(run, events, calls, tool_roles(await service.load_settings_for(run)))

    @router.get("/{run_id}/metrics")
    async def research_metrics(run=Depends(owned)):
        # Cost and efficiency from recorded measurements; never trace payloads.
        from .metrics import collect

        return await collect(service.store, run, service.settings.pricing)

    @router.get("/{run_id}/metrics/export")
    async def export_metrics(run=Depends(owned)):
        from .metrics import export_records

        async def stream():
            async for record in export_records(service.store, run, service.settings.pricing):
                yield json.dumps(record, ensure_ascii=False, default=str) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="research-{run["run_id"]}-metrics.jsonl"', "Cache-Control": "no-store"})

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

        # no-transform keeps compressing proxies (for example the Next.js dev
        # rewrite) from buffering sparse progress events until the stream ends.
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})

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

    @router.get("/{run_id}/llm-calls")
    async def llm_calls(run=Depends(owned)):
        # Summaries in request order; complete prompts and answers are read per call.
        metrics = {record["id"]: record for record in await service.store.model_calls(run["run_id"])}
        items = [{**metrics.pop(exchange["id"], {}), **exchange, "audited": True} for exchange in await service.store.llm_exchanges(run["run_id"])]
        # Calls recorded before full auditing (or with content capture off) keep their metrics.
        items.extend({**record, "audited": False} for record in metrics.values())
        items.sort(key=lambda item: item.get("started_at") or "")
        return {"items": items}

    @router.get("/{run_id}/llm-calls/export")
    async def export_llm_calls(request: Request, run=Depends(owned)):
        from .audit import openai_request

        async def stream():
            for index, summary in enumerate(await service.store.llm_exchanges(run["run_id"])):
                if index % 50 == 0:
                    await owned(run["run_id"], request, run["owner"])
                detail = await service.store.llm_exchange(run["run_id"], summary["id"])
                if detail is not None:
                    yield json.dumps({**detail, "openai_request": openai_request(detail)}, ensure_ascii=False, default=str) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Content-Disposition": f'attachment; filename="research-{run["run_id"]}-llm-calls.jsonl"', "Cache-Control": "no-store"})

    @router.get("/{run_id}/llm-calls/{call_id}")
    async def llm_call(call_id: str, run=Depends(owned)):
        from .audit import openai_request

        detail = await service.store.llm_exchange(run["run_id"], call_id)
        if detail is None:
            raise HTTPException(404, "Model call not found")
        metrics = await service.store.model_call(run["run_id"], call_id) or {}
        return {**metrics, **detail, "openai_request": openai_request(detail)}

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
            if value.get("format") == "markdown-v2":
                from .report import docx_document as export
            else:
                from .render import docx_report as export  # Historical AST reports.

            data = await asyncio.to_thread(export, value)
            media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            data = value["markdown" if format == "md" else "html"]
            media = "text/markdown" if format == "md" else "text/html"
        return Response(
            data, media_type=media, headers={"Content-Disposition": f'attachment; filename="research-{run["run_id"]}.{format}"', "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"}
        )

    return router
