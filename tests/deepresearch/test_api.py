import sys
import types

import httpx
import pytest
from fastapi import FastAPI

from deepresearch.api import build_router
from deepresearch.service import ResearchService


@pytest.mark.asyncio
async def test_owner_authorization_and_demo_origin(settings, monkeypatch):
    service = ResearchService(settings)
    await service.store.start()
    run = {"run_id": "private-run", "owner": "alice", "status": "COMPLETED", "report": {"internal": "private"}}
    await service.store.create(run, "k", "h")

    def resolver(req):
        user = req.headers.get("x-test-user")
        return types.SimpleNamespace(user_id=user) if user else None

    monkeypatch.setitem(sys.modules, "deerflow_extension_api", types.SimpleNamespace(resolve_principal=resolver))
    app = FastAPI()
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/api/deepresearch/private-run")).status_code == 401
        assert (await client.get("/api/deepresearch/private-run", headers={"x-test-user": "bob"})).status_code == 404
        response = await client.get("/api/deepresearch/private-run", headers={"x-test-user": "alice"})
        assert response.status_code == 200 and "owner" not in response.json()
        retry_calls = []

        async def retry(run_id, secrets=None, *, allow_limited_report=None):
            retry_calls.append((run_id, allow_limited_report))
            return run

        monkeypatch.setattr(service, "retry", retry)
        assert (await client.post("/api/deepresearch/private-run/retry", json={"allow_limited_report": True}, headers={"x-test-user": "bob"})).status_code == 404
        assert not retry_calls
        assert (await client.post("/api/deepresearch/private-run/retry", json={"allow_limited_report": True}, headers={"x-test-user": "alice"})).status_code == 202
        assert retry_calls == [("private-run", True)]
        await service.store.event("private-run", "trace.started", {"span_id": "s", "name": "private"})
        assert (await client.get("/api/deepresearch/private-run/trace", headers={"x-test-user": "bob"})).status_code == 404
        assert (await client.get("/api/deepresearch/private-run/trace/export", headers={"x-test-user": "bob"})).status_code == 404
        trace = await client.get("/api/deepresearch/private-run/trace", headers={"x-test-user": "alice"})
        assert trace.json()["items"][0]["data"]["span_id"] == "s"
        assert (await client.get("/api/deepresearch/private-run/activity", headers={"x-test-user": "bob"})).status_code == 404
        activity = await client.get("/api/deepresearch/private-run/activity", headers={"x-test-user": "alice"})
        assert activity.status_code == 200 and activity.json()["status"] == "COMPLETED"
        # The activity projection never carries trace payloads.
        assert "trace.started" not in activity.text
    demo = FastAPI()
    demo.include_router(build_router(service, local_demo=True))
    transport = httpx.ASGITransport(app=demo, client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        assert (await client.get("/api/deepresearch/capabilities", headers={"origin": "https://attacker.example"})).status_code == 403
        assert (await client.get("/api/deepresearch/capabilities", headers={"host": "attacker.example"})).status_code == 403
        assert (await client.get("/api/deepresearch/capabilities")).status_code == 200


@pytest.mark.asyncio
async def test_acl_revocation_also_hides_history(settings, monkeypatch):
    service = ResearchService(settings)
    await service.store.start()
    for rid in ("allowed-run", "revoked-run"):
        await service.store.create({"run_id": rid, "owner": "alice", "status": "COMPLETED", "report": {}}, rid, rid)

    async def policy(request, run):
        return run["run_id"] != "revoked-run"

    monkeypatch.setitem(sys.modules, "test_research_acl", types.SimpleNamespace(check=policy))
    monkeypatch.setitem(sys.modules, "deerflow_extension_api", types.SimpleNamespace(resolve_principal=lambda req: types.SimpleNamespace(user_id="alice")))
    settings.access_policy = "test_research_acl:check"
    app = FastAPI()
    app.include_router(build_router(service))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/api/deepresearch")
        assert response.status_code == 200
        assert [r["run_id"] for r in response.json()] == ["allowed-run"]
        assert (await client.get("/api/deepresearch/revoked-run")).status_code == 403
