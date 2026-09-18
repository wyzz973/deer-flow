"""Live acceptance setup must not mutate operator data or persist API keys."""

from deepresearch.live import _write_yaml, build_config, private_config, research_model, web_sources


def test_inline_credentials_are_kept_only_in_the_child_environment():
    original = {"models": [{"name": "configured", "api_key": "private-value"}], "headers": {"Authorization": "Bearer private"}, "nested": {"password": "$EXISTING_PASSWORD"}}
    environ = {}
    safe = private_config(original, environ)
    assert "private-value" not in str(safe) and "Bearer private" not in str(safe)
    assert safe["nested"]["password"] == "$EXISTING_PASSWORD"
    assert environ["DEERFLOW_ACCEPTANCE_SECRET_1"] == "private-value"
    assert original["models"][0]["api_key"] == "private-value"


def test_live_engine_copy_isolates_storage_and_adds_no_research_agents(tmp_path):
    base = {
        "models": [{"name": "configured", "api_key": "$KEY"}],
        "tools": [{"name": "web_search", "use": "original:tool"}],
        "database": {"backend": "postgres", "postgres_url": "$DATABASE"},
        "subagents": {"custom_agents": {"untouched": {"model": "configured"}}},
    }
    value = build_config(base, tmp_path)
    assert value["models"] == base["models"] and value["tools"] == base["tools"]
    assert base["database"]["backend"] == "postgres"
    assert value["database"]["sqlite_dir"] == str(tmp_path / "database")
    assert value["database"]["postgres_url"] == ""
    from pydantic import TypeAdapter

    from deerflow.config.app_config import AppConfig

    # Validate against the host contract, not just this launcher's dictionary.
    TypeAdapter(AppConfig.model_fields["database"].annotation).validate_python(value["database"])
    # Research roles are defined in the research configuration, not as host agents.
    assert value["subagents"]["custom_agents"] == {"untouched": {"model": "configured"}}
    assert value["plugins"][0]["config"]["config_path"] == str(tmp_path / "research.yaml")


def test_live_research_config_owns_its_model_and_failover_sources(tmp_path):
    from deepresearch.config import Settings, load_settings
    from deerflow.config.subagents_config import SubagentsAppConfig

    host_model = {
        "name": "deepseek-v4-flash",
        "display_name": "DeepSeek V4 Flash",
        "use": "deerflow.models.patched_deepseek:PatchedChatDeepSeek",
        "model": "deepseek-v4-flash",
        "api_key": "$DEEPSEEK_API_KEY",
        "timeout": 600.0,
        "max_retries": 2,
        "max_tokens": 8192,
        "context_window": 128000,
        "supports_thinking": True,
        "when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}},
    }
    model = research_model(host_model, max_output_tokens=393216)
    assert model == {
        "name": "deepseek-v4-flash",
        "display_name": "DeepSeek V4 Flash",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "api_key": "$DEEPSEEK_API_KEY",
        "max_tokens": 393216,
        "context_window": 128000,
        "supports_thinking": True,
        "max_retries": 2,
        "timeout_seconds": 600.0,
    }
    sources = web_sources({"SERPER_API_KEY": "x", "JINA_API_KEY": "y"}, jina_key=False)
    assert [provider["id"] for provider in sources[0]["providers"]] == ["serper", "duckduckgo"]
    assert sources[1]["providers"] == [{"id": "jina", "type": "jina_reader"}, {"id": "direct", "type": "direct"}]
    base = load_settings().model_dump(mode="json")
    research = Settings.model_validate({**base, "runner": "deerflow", "models": [model], "default_model": model["name"], "extraction_model": model["name"], "sources": sources, "source_fallback": [], "require_dual_source": False})
    assert research.sources[0].kind == "channel" and research.models[0].provider == "deepseek"
    engine = build_config({"subagents": {"agents": {"untouched": {"token_budget": {"enabled": True, "max_tokens": 20000}}}}}, tmp_path, research.skills, unlimited_budget=True)
    policies = SubagentsAppConfig.model_validate(engine["subagents"])
    assert not policies.get_token_budget_for("deepresearch-technical-route", summarization_enabled=True).enabled
    assert policies.get_token_budget_for("untouched").enabled


def test_code_only_resume_preserves_config_and_rejects_config_drift(tmp_path):
    import pytest

    path = tmp_path / "research.yaml"
    _write_yaml(path, {"model": "configured"})
    original = path.read_bytes()
    _write_yaml(path, {"model": "configured"}, reuse=True)
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="configuration changed"):
        _write_yaml(path, {"model": "different"}, reuse=True)
    assert path.read_bytes() == original


def test_resume_accepts_prices_added_to_the_private_config(tmp_path):
    import pytest
    import yaml

    path = tmp_path / "research.yaml"
    _write_yaml(path, {"model": "configured"})
    path.write_text(path.read_text(encoding="utf-8") + "pricing:\n  flash:\n    input_per_million: 1.0\n    output_per_million: 2.0\n", encoding="utf-8")
    _write_yaml(path, {"model": "configured"}, reuse=True, operator_keys=("pricing",))
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["pricing"]["flash"]["output_per_million"] == 2.0
    with pytest.raises(ValueError, match="configuration changed"):
        _write_yaml(path, {"model": "different"}, reuse=True, operator_keys=("pricing",))
