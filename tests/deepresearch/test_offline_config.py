"""Offline deployment templates stay valid, self-contained and consistent."""

from pathlib import Path

import yaml

from deepresearch.config import load_settings
from deepresearch.contracts import ResearchPlan, ResearchUnit

ROOT = Path(__file__).resolve().parents[2]
OFFLINE = ROOT / "examples/deepresearch/offline"
PUBLIC_PROVIDERS = {"tavily", "serper", "brave", "exa", "bocha", "jina_search", "duckduckgo", "jina_reader", "tavily_extract", "firecrawl", "direct"}


def test_offline_research_config_is_self_contained_and_offline_safe():
    settings = load_settings(OFFLINE / "research.yaml")
    for name in settings.skills:
        assert settings.read_skill(name)
    assert settings.runner == "deerflow" and not settings.require_dual_source and not settings.favicons
    assert {source.origin for source in settings.sources} == {"internal"} and settings.source_fallback == ["internal-knowledge"]
    # Every model reference resolves inside the research configuration itself.
    models = {model.name for model in settings.models}
    references = {settings.default_model, settings.extraction_model} | {spec.model for spec in settings.skills.values() if spec.model}
    assert references <= models and all(spec.agent is None for spec in settings.skills.values())
    providers = [provider.type for source in settings.sources for provider in source.providers]
    assert providers and not set(providers) & PUBLIC_PROVIDERS
    assert all(model.api_key is None or model.api_key.startswith("$") for model in settings.models)


def test_offline_engine_fragment_only_tunes_the_engine_and_registers_the_extension():
    from deerflow.config.app_config import AppConfig

    base = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    fragment = yaml.safe_load((OFFLINE / "host-config.fragment.yaml").read_text(encoding="utf-8"))
    # Research needs nothing from the host's models, tools or subagents.
    assert not {"models", "tools", "subagents"} & set(fragment)
    config = AppConfig.model_validate({**base, **fragment, "models": []})
    settings = load_settings(OFFLINE / "research.yaml")
    assert config.subagent_runtime.max_running >= settings.max_concurrency
    (plugin,) = config.plugins
    assert (plugin.use, plugin.config["config_path"]) == ("deepresearch.extension:install", "deepresearch.local.yaml")
    online = yaml.safe_load((ROOT / "examples/deepresearch/host-config.fragment.yaml").read_text(encoding="utf-8"))
    assert set(online) == {"plugins"}


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
