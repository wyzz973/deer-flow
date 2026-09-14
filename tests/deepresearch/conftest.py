import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend/packages/harness"))

from deepresearch.config import load_settings  # noqa: E402 - source checkout paths above
from deepresearch.contracts import Finding, RawEvidence, ResearchPlan, ResearchResult, ResearchUnit  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    value = load_settings(ROOT / "deepresearch.example.yaml")
    value.data_dir = str(tmp_path / "data")
    return value


@pytest.fixture
def plan():
    return ResearchPlan(goal="研究某技术的演进路线", research_units=[ResearchUnit(id="R1", skill="technical-route", objective="比较候选技术及落地约束")])


@pytest.fixture
def result():
    raw = [
        RawEvidence(raw_id="raw-internal", title="内部技术文档", source_uri="doc://internal/1", origin="internal", source_name="internal-kb", source_level="L1", publisher="internal-org", snippet="内部测试证据"),
        RawEvidence(raw_id="raw-external", title="外部技术文档", url="https://Example.com/page?id=1&utm_source=x#part", origin="external", source_name="external-web", source_level="L2", publisher="example-org", snippet="外部测试证据"),
    ]
    return ResearchResult(unit_id="R1", findings=[Finding(claim="某项可验证结论", raw_evidence_refs=[e.raw_id for e in raw], confidence=0.8)], raw_evidences=raw, confidence=0.8, searched_origins=["internal", "external"])
