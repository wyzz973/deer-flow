"""Research settings: editable overrides, per-run snapshots, secrets and the settings API."""

import asyncio
import sys
import types

import httpx
import pytest
from fastapi import FastAPI

pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from deepresearch import profile  # noqa: E402 - after the optional dependency check
from deepresearch.api import build_router  # noqa: E402
from deepresearch.contracts import CreateResearch, ResearchError  # noqa: E402
from deepresearch.runner import DemoRunner  # noqa: E402
from deepresearch.secrets import SECRETS  # noqa: E402
from deepresearch.service import ResearchService  # noqa: E402


async def settle(service, run_id):
    for _ in range(1000):
        if run_id not in service.tasks:
            return await service.store.get(run_id)
        await asyncio.sleep(0.01)
    raise AssertionError("workflow did not settle")


class PromptRecorder(DemoRunner):
    seen = []

    async def plan(self, run, proposed=None, request=None):
        self.seen.append(("plan", run["run_id"], self.settings.prompts.plan))
        return await super().plan(run, proposed, request=request)

    async def research(self, run, unit, dependencies):
        self.seen.append(("research", run["run_id"], self.settings.prompts.research))
        return await super().research(run, unit, dependencies)


def overrides(service, **changes):
    view = profile.editable_view(service.settings)
    view.update(changes)
    return view


@pytest.mark.asyncio
async def test_runs_keep_the_settings_they_started_with(settings):
    service = ResearchService(settings)
    recorder = PromptRecorder(settings, service.store)
    recorder.seen = []
    service.runner = recorder
    await service.start()
    try:
        before = await service.create("u", CreateResearch(query="比较两种数据库的运维成本"), "before")
        before = await settle(service, before["run_id"])
        assert before["status"] == "AWAITING_PLAN_CONFIRMATION" and before["profile"]["version"] == 0
        prompts = {**profile.editable_view(service.settings)["prompts"], "plan": "NEW PLAN PROMPT", "research": "NEW RESEARCH PROMPT"}
        record = await service.update_profile(overrides(service, prompts=prompts, max_report_sections=5), 0, "admin")
        assert record["version"] == 1 and service.settings.prompts.plan == "NEW PLAN PROMPT"
        # Only real differences from the operator file are stored.
        assert set((await service.store.profile())["overrides"]) == {"prompts", "max_report_sections"}
        assert set((await service.store.profile())["overrides"]["prompts"]) == {"plan", "research"}
        # The earlier run executes with its own snapshot even after the edit.
        await service.decision(before["run_id"], 1, "approve")
        assert (await settle(service, before["run_id"]))["status"] == "COMPLETED"
        after = await service.create("u", CreateResearch(query="新的研究问题：缓存选型"), "after")
        after = await settle(service, after["run_id"])
        assert after["profile"]["version"] == 1 and after["profile"]["hash"] != before["profile"]["hash"]
        by_run = {}
        for stage, run_id, text in recorder.seen:
            by_run.setdefault(run_id, set()).add((stage, text))
        assert all(text != "NEW PLAN PROMPT" and text != "NEW RESEARCH PROMPT" for _, text in by_run[before["run_id"]])
        assert ("plan", "NEW PLAN PROMPT") in by_run[after["run_id"]]
        with pytest.raises(ResearchError) as stale:
            await service.update_profile(overrides(service, max_report_sections=4), 0, "admin")
        assert stale.value.code == "PROFILE_VERSION"
    finally:
        await service.stop()
    # After a restart, snapshots come back from the store.
    again = ResearchService(settings)
    await again.start()
    try:
        assert again.profile_version == 1 and again.settings.max_report_sections == 5
        run = await again.store.get(before["run_id"])
        restored = await again.load_settings_for(run)
        assert restored.prompts.plan != "NEW PLAN PROMPT" and restored.skills["technical-route"].methodology
        await again.reset_profile(["prompts"], 1, "admin")
        assert again.settings.prompts.plan != "NEW PLAN PROMPT" and again.settings.max_report_sections == 5
        await again.restore_profile(1, 2, "admin")
        assert again.settings.prompts.plan == "NEW PLAN PROMPT"
        assert [item["version"] for item in await again.store.profile_history()] == [3, 2, 1]
    finally:
        await again.stop()


def test_snapshots_are_self_contained_and_invalid_edits_are_rejected(settings):
    body = profile.snapshot(settings)
    assert all(spec["methodology"] and spec["path"] is None for spec in body["skills"].values())
    assert not set(profile.OPERATOR_ONLY) & set(body)
    restored = profile.restore(settings, body)
    # Skill-file front matter is metadata, not methodology: snapshots and the settings page omit it.
    assert settings.read_skill("deepresearch").startswith("---")
    assert restored.skills["deepresearch"].methodology == settings.methodology("deepresearch")
    assert not restored.skills["deepresearch"].methodology.startswith("---") and "# DeepResearch / Planner" in restored.skills["deepresearch"].methodology
    with pytest.raises(ValueError, match="Unknown research model"):
        profile.effective(settings, {"default_model": "not-a-configured-model"})
    with pytest.raises(ValueError, match="enabled researcher"):
        skills = {name: {**spec.model_dump(), "enabled": name in {"deepresearch", "report-synthesis"}} for name, spec in settings.skills.items()}
        profile.effective(settings, {"skills": skills})
    # Operator-owned fields are ignored even if a client sends them.
    assert profile.effective(settings, {"runner": "deerflow", "budget_ceiling": {"max_units": 64}}).runner == settings.runner


def test_a_snapshot_survives_a_field_that_a_later_version_removed(settings):
    """An upgrade that renames or drops a setting must not strand existing runs."""
    body = profile.snapshot(settings)
    body["compaction"] = {**body["compaction"], "keep_messages": 16}
    body["a_field_from_the_future"] = True
    restored = profile.restore(settings, body)
    assert restored.compaction.keep_fraction == settings.compaction.keep_fraction
    # Saved settings-page overrides get the same treatment.
    overrides = {"compaction": {"trigger_fraction": 0.3, "keep_messages": 8}}
    assert profile.effective(settings, overrides).compaction.trigger_fraction == 0.3
    # A real mistake still fails loudly.
    with pytest.raises(ValueError, match="Unknown research model"):
        profile.effective(settings, {"default_model": "not-a-configured-model"})


@pytest.mark.asyncio
async def test_settings_api_is_admin_only_for_changes_and_never_returns_secret_values(settings, monkeypatch):
    service = ResearchService(settings)
    await service.start()
    principals = {"admin": types.SimpleNamespace(user_id="admin", is_admin=True), "member": types.SimpleNamespace(user_id="member", is_admin=False)}
    monkeypatch.setitem(sys.modules, "deerflow_extension_api", types.SimpleNamespace(resolve_principal=lambda request: principals.get(request.headers.get("x-test-user"))))
    app = FastAPI()
    app.include_router(build_router(service))
    admin, member = {"x-test-user": "admin"}, {"x-test-user": "member"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            view = (await client.get("/api/deepresearch/settings", headers=member)).json()
            assert view["editable"] is False and view["version"] == 0
            assert {item["key"] for item in view["catalog"]["prompts"]} >= {"rewrite", "plan", "research", "section"}
            assert view["settings"]["skills"]["technical-route"]["methodology"] and view["settings"]["models"][0]["name"] == "deepseek-flash"
            changed = {**view["settings"], "max_concurrency": 2}
            assert (await client.post("/api/deepresearch/settings", json={"version": 0, "settings": changed}, headers=member)).status_code == 403
            saved = await client.post("/api/deepresearch/settings", json={"version": 0, "settings": changed}, headers=admin)
            assert saved.status_code == 200 and saved.json()["version"] == 1 and saved.json()["overridden"] == ["max_concurrency"]
            conflict = await client.post("/api/deepresearch/settings", json={"version": 0, "settings": changed}, headers=admin)
            assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "PROFILE_VERSION"
            invalid = {**changed, "models": [{"name": "m", "model": "x", "api_key": "sk-should-not-echo"}]}
            rejected = await client.post("/api/deepresearch/settings", json={"version": 1, "settings": invalid}, headers=admin)
            assert rejected.status_code == 422 and "sk-should-not-echo" not in rejected.text

            secret = await client.post("/api/deepresearch/settings/secrets", json={"name": "bocha-key", "value": "sk-saved-bocha"}, headers=admin)
            assert secret.status_code == 200 and "sk-saved-bocha" not in secret.text and "bocha-key" in secret.json()["secrets"]["saved"]
            web = next(source for source in changed["sources"] if source["tool"] == "web_search")
            web["providers"].insert(0, {"id": "bocha", "type": "bocha", "api_key": "secret:bocha-key"})
            saved = await client.post("/api/deepresearch/settings", json={"version": 1, "settings": changed}, headers=admin)
            assert saved.status_code == 200 and saved.json()["secrets"]["references"]["secret:bocha-key"] == "set"
            assert (await service.store.secrets()) == {"bocha-key": "sk-saved-bocha"} and "sk-saved-bocha" not in str(await service.store.profile())
            history = (await client.get("/api/deepresearch/settings/history", headers=member)).json()["items"]
            assert [item["version"] for item in history] == [2, 1] and history[0]["fields"] == ["max_concurrency", "sources"]
            reset = await client.post("/api/deepresearch/settings/reset", json={"version": 2, "fields": ["sources"]}, headers=admin)
            assert reset.json()["overridden"] == ["max_concurrency"]
            removed = await client.post("/api/deepresearch/settings/secrets", json={"name": "bocha-key"}, headers=admin)
            assert "bocha-key" not in removed.json()["secrets"]["saved"]
            assert (await client.get("/api/deepresearch/settings/health", headers=member)).status_code == 200
    finally:
        SECRETS.load({})
        await service.stop()
