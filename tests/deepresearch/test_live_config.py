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


def test_a_setting_added_by_an_upgrade_does_not_block_resuming_at_its_default(tmp_path):
    import pytest

    from deepresearch.live import setting_defaults

    path = tmp_path / "research.yaml"
    _write_yaml(path, {"model": "configured"})
    defaults = setting_defaults()
    assert defaults["tool_receipt_ledger"] is False and defaults["nodes"] == {} and "coverage" in defaults["supplement_gap_codes"]
    # The stored file predates the setting and already runs with its default.
    _write_yaml(path, {"model": "configured", "tool_receipt_ledger": False, "nodes": {}, "supplement_gap_codes": defaults["supplement_gap_codes"]}, reuse=True, defaults=defaults)
    # Any other value changes execution, as does a key without a known default.
    for changed in ({"tool_receipt_ledger": True}, {"nodes": {"plan": {"temperature": 0}}}, {"unknown_setting": False}):
        with pytest.raises(ValueError, match="configuration changed"):
            _write_yaml(path, {"model": "configured", **changed}, reuse=True, defaults=defaults)


def test_the_engine_admits_as_many_roles_at_once_as_research_runs(tmp_path):
    """The engine queues native roles beyond ``subagent_runtime.max_running`` (3
    in the host example). A research configured for six parallel steps then ran
    three at a time: three steps waited 203-292 s of a ten-minute run, eight
    seconds short of the engine's admission timeout, and no research metric
    showed the queue."""
    import pytest
    import yaml

    engine = build_config({"subagent_runtime": {"max_running": 3, "queue_timeout_seconds": 300}}, tmp_path, concurrency=8)
    assert engine["subagent_runtime"] == {"max_running": 8, "queue_timeout_seconds": 300}
    # A host that already allows more keeps its value; without a need nothing is touched.
    assert build_config({"subagent_runtime": {"max_running": 16}}, tmp_path, concurrency=8)["subagent_runtime"]["max_running"] == 16
    assert "subagent_runtime" not in build_config({}, tmp_path)

    # An existing acceptance directory picks the capacity up on its next start:
    # it changes how many roles run at once, not what a research run is.
    path = tmp_path / "host.yaml"
    _write_yaml(path, {"models": ["m"], "subagent_runtime": {"max_running": 3}})
    _write_yaml(path, {"models": ["m"], "subagent_runtime": {"max_running": 8}}, reuse=True, refresh=("subagent_runtime",))
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"models": ["m"], "subagent_runtime": {"max_running": 8}}
    with pytest.raises(ValueError, match="configuration changed"):
        _write_yaml(path, {"models": ["other"], "subagent_runtime": {"max_running": 8}}, reuse=True, refresh=("subagent_runtime",))
