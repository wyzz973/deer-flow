"""Offline deployment templates stay valid and consistent with each other."""

from pathlib import Path

import yaml

from deepresearch.config import load_settings
from deepresearch.contracts import ResearchPlan, ResearchUnit

ROOT = Path(__file__).resolve().parents[2]
OFFLINE = ROOT / "examples/deepresearch/offline"


def test_offline_research_config_is_valid_and_offline_safe():
    settings = load_settings(OFFLINE / "research.yaml")
    for name in settings.skills:
        assert settings.read_skill(name)
    assert settings.runner == "deerflow" and not settings.require_dual_source and not settings.favicons
    assert {source.origin for source in settings.sources} == {"internal"} and settings.source_fallback == ["internal-knowledge"]


def test_offline_host_fragment_validates_and_matches_the_research_config():
    from deerflow.config.app_config import AppConfig

    base = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    fragment = yaml.safe_load((OFFLINE / "host-config.fragment.yaml").read_text(encoding="utf-8"))
    config = AppConfig.model_validate({**base, **fragment})
    settings = load_settings(OFFLINE / "research.yaml")

    models = {model.name for model in config.models}
    assert {spec.model for spec in settings.skills.values()} | {settings.extraction_model} <= models
    agents = config.subagents.custom_agents
    assert {spec.agent for spec in settings.skills.values()} <= set(agents)
    assert all(agents[spec.agent].model in models and agents[spec.agent].timeout_seconds >= spec.timeout_seconds for spec in settings.skills.values())
    tools = {tool.name for tool in config.tools}
    assert {source.tool for source in settings.sources} <= tools and "read_file" in tools
    assert not tools & {"web_search", "web_fetch", "image_search"}
    assert config.subagent_runtime.max_running >= settings.max_concurrency
    (plugin,) = config.plugins
    assert (plugin.use, plugin.config["config_path"]) == ("deepresearch.extension:install", "deepresearch.local.yaml")


def test_single_origin_deployments_drop_origins_no_source_serves(settings):
    offline = load_settings(OFFLINE / "research.yaml")
    plan = ResearchPlan(
        goal="比较内部知识库中的方案",
        research_units=[
            ResearchUnit(id="R1", skill="technical-route", objective="默认要求内外部来源"),
            ResearchUnit(id="R2", skill="technical-route", objective="只要求外部来源", source_strategy={"required_origins": ["external"]}),
        ],
    )
    offline.fit_origins(plan)
    assert [unit.source_strategy.required_origins for unit in plan.research_units] == [["internal"], ["internal"]]

    # Deployments that require both origins keep the planner's requirement.
    dual = ResearchPlan(goal="双来源部署", research_units=[ResearchUnit(id="R1", skill="technical-route", objective="默认要求内外部来源")])
    settings.fit_origins(dual)
    assert dual.research_units[0].source_strategy.required_origins == ["internal", "external"]
