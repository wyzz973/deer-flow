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


def test_offline_template_with_the_mcp_fragment_is_tuned_per_node_and_cites_without_originals(monkeypatch):
    """The offline template plus the MCP fragment: MCP tools only, nothing that opens an original."""
    from deepresearch.config import Settings
    from deepresearch.doctor import node_status, retrieval_status
    from deepresearch.models import CHAT_COMPLETIONS, engine_model, legacy_token_param
    from deepresearch.report_policy import results_citable

    base = yaml.safe_load((OFFLINE / "research.yaml").read_text(encoding="utf-8"))
    fragment = yaml.safe_load((ROOT / "examples/deepresearch/mcp-sources.fragment.yaml").read_text(encoding="utf-8"))
    gateway = {"name": "gateway", "provider": "openai", "model": "served-model", "base_url": "https://llm-gateway.example/v1", "api_key": "$GATEWAY_API_KEY", "max_tokens": 8192}
    merged = {**base, **fragment, "source_fallback": [source["name"] for source in fragment["sources"]], "models": [gateway], "default_model": "gateway", "extraction_model": None}
    settings = Settings.model_validate(merged)
    assert {provider.type for source in settings.active_sources() for provider in source.providers} == {"mcp"}
    assert not any(source.role == "read" for source in settings.active_sources()) and results_citable(settings)
    # Every MCP tool research can reach is on its server's allowlist, and credentials are references.
    for _, server, tool in settings.mcp_bindings():
        assert tool in settings.mcp_servers[server].allowed_tools
    assert all("$" in value or value.startswith("secret:") for server in settings.mcp_servers.values() for value in server.headers.values())
    status = node_status(settings)
    assert status["plan"]["temperature"] == 0 and status["conversion"]["max_tokens"] == 6000 and status["section"]["temperature"] == 0.5
    assert "open-questions" not in settings.supplement_gap_codes
    notes = retrieval_status(settings)
    assert notes["search_results_citable"] and len(notes["notes"]) >= 2  # no reader; one source is an opaque kind: mcp
    # A gateway behind base_url gets the earlier protocol's parameter name and reports streamed usage.
    monkeypatch.setenv("GATEWAY_API_KEY", "test-only")
    profile = engine_model(settings.models[0])
    assert legacy_token_param(settings.models[0]) and profile.use == CHAT_COMPLETIONS and profile.model_extra["stream_usage"] is True


def test_the_mcp_stub_overlay_stays_a_valid_mcp_only_configuration():
    """The acceptance overlay next to the MCP stub server loads over the example file."""
    from deepresearch.config import Settings
    from deepresearch.report_policy import results_citable

    base = yaml.safe_load((ROOT / "deepresearch.example.yaml").read_text(encoding="utf-8"))
    overlay = yaml.safe_load((ROOT / "examples/deepresearch/mcp-stub/research-overlay.yaml").read_text(encoding="utf-8"))
    settings = Settings.model_validate({**base, **overlay, "runner": "deerflow"})
    assert [source.name for source in settings.active_sources()] == ["kb-docs", "kb-wiki", "kb-tickets"] and results_citable(settings)
    assert {tool for _, _, tool in settings.mcp_bindings()} == set(settings.mcp_servers["kb"].allowed_tools)
    assert "delete_page" not in settings.mcp_servers["kb"].allowed_tools
    corpus = (ROOT / "examples/deepresearch/mcp-stub/corpus.json").read_text(encoding="utf-8")
    assert len(yaml.safe_load(corpus)) >= 20
