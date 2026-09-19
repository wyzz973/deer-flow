"""A supplement may cite only its own records and declared saved dependencies."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from deepresearch.contracts import Finding, ResearchError, ResearchUnit
from deepresearch.evidence import digest
from deepresearch.observations import NativeExecution
from deepresearch.runner import DeerFlowRunner, ResearchAnalysis
from deepresearch.store import Store


@pytest.mark.asyncio
async def test_supplement_keeps_parent_provenance_without_repeating_native_work(settings, result, tmp_path, monkeypatch):
    store = Store(tmp_path / "dependency.sqlite")
    await store.start()
    run = {"run_id": "r", "owner": "u", "source_names": [], "cycle": 0, "budget": settings.budget_ceiling.model_dump()}
    await store.create(run, "k", "h")
    body = result.model_dump(mode="json")
    await store.save_unit("r", "0:R1", "parent-hash", body)
    unit = ResearchUnit(id="S1", skill="technical-route", objective="Resolve remaining question", depends_on=["R1"])
    dependencies = {"R1": body["findings"]}
    execution = NativeExecution(answer="Use prior evidence", execution_id="cached-child")
    await store.cache("r", "native-unit:" + digest([0, unit.model_dump(mode="json"), dependencies]), asdict(execution))
    runner = DeerFlowRunner(settings, store)

    async def agent_config(name):
        return settings.skills[name], SimpleNamespace(tools=None, disallowed_tools=[])

    observed = []

    async def convert(_runner, _run, _name, payload, _schema, _answer, _context, *, validator, repair=None):
        observed.append({item["raw_id"] for item in payload["observed_calls"]})
        good = ResearchAnalysis(findings=[Finding(claim="Dependent conclusion", raw_evidence_refs=["raw-external"], confidence=0.8)], confidence=0.8)
        validator(good)
        with pytest.raises(ValueError, match="Unknown evidence IDs"):
            validator(ResearchAnalysis(findings=[Finding(claim="Invented", raw_evidence_refs=["not-recorded"], confidence=0.8)], confidence=0.8))
        return good

    async def unexpected(*args, **kwargs):
        pytest.fail("Cached native execution must not run tools again")

    monkeypatch.setattr(runner, "_agent_config", agent_config)
    monkeypatch.setattr("deepresearch.structured.convert_answer", convert)
    monkeypatch.setattr("deepresearch.native.execute_role", unexpected)
    actual = await runner.research(run, unit, dependencies)
    assert observed[0] == {"raw-internal", "raw-external"}
    assert {e.raw_id for e in actual.raw_evidences} == {"raw-internal", "raw-external"}
    assert actual.raw_evidences[1].url == result.raw_evidences[1].url
    # A parent record the current contract cannot keep is skipped, not fatal:
    # claims that cite it are then reported as unknown IDs and pruned.
    broken = {**body, "raw_evidences": [{**body["raw_evidences"][0], "raw_id": "raw-broken", "url": "https://user:secret@example.com/x", "origin": "external", "source_uri": None}, *body["raw_evidences"]]}
    broken["findings"] = [{**broken["findings"][0], "raw_evidence_refs": ["raw-broken", "raw-external"]}]
    await store.save_unit("r", "0:R2", "parent-hash-2", broken)
    child = ResearchUnit(id="S2", skill="technical-route", objective="Resolve another question", depends_on=["R2"])
    await store.cache("r", "native-unit:" + digest([0, child.model_dump(mode="json"), {"R2": broken["findings"]}]), asdict(execution))
    salvaged = await runner.research(run, child, {"R2": broken["findings"]})
    assert "raw-broken" not in observed[1] and "raw-external" in observed[1]
    assert "raw-broken" not in {e.raw_id for e in salvaged.raw_evidences}
    assert "raw-external" in {e.raw_id for e in salvaged.raw_evidences}

    # Same dependency ID in another cycle must not inherit previous evidence.
    run["cycle"] = 1
    await store.cache("r", "native-unit:" + digest([1, unit.model_dump(mode="json"), dependencies]), asdict(execution))
    with pytest.raises(ResearchError, match="Dependency findings"):
        await runner.research(run, unit, dependencies)
