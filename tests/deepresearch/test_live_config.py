"""Live acceptance setup must not mutate operator data or persist API keys."""

from deepresearch.live import _write_yaml, build_config, private_config


def test_inline_credentials_are_kept_only_in_the_child_environment():
    original = {"models": [{"name": "configured", "api_key": "private-value"}], "headers": {"Authorization": "Bearer private"}, "nested": {"password": "$EXISTING_PASSWORD"}}
    environ = {}
    safe = private_config(original, environ)
    assert "private-value" not in str(safe) and "Bearer private" not in str(safe)
    assert safe["nested"]["password"] == "$EXISTING_PASSWORD"
    assert environ["DEERFLOW_ACCEPTANCE_SECRET_1"] == "private-value"
    assert original["models"][0]["api_key"] == "private-value"


def test_live_copy_preserves_host_models_and_isolates_storage(tmp_path):
    base = {
        "models": [{"name": "configured", "api_key": "$KEY"}],
        "tools": [{"name": "web_search", "use": "original:tool"}],
        "database": {"backend": "postgres", "postgres_url": "$DATABASE"},
        "subagents": {"custom_agents": {"untouched": {"model": "configured"}}},
    }
    fragment = {"subagents": {"custom_agents": {"technical-researcher": {"tools": ["placeholder"], "model": "inherit"}}}}
    value = build_config(base, fragment, tmp_path, "configured")
    assert value["models"] == base["models"] and value["tools"] == base["tools"]
    assert base["database"]["backend"] == "postgres"
    assert value["database"]["sqlite_dir"] == str(tmp_path / "database")
    assert value["database"]["postgres_url"] == ""
    from pydantic import TypeAdapter

    from deerflow.config.app_config import AppConfig

    # Validate against the host contract, not just this launcher's dictionary.
    TypeAdapter(AppConfig.model_fields["database"].annotation).validate_python(value["database"])
    assert "untouched" in value["subagents"]["custom_agents"]
    assert value["subagents"]["custom_agents"]["acceptance-technical-researcher"]["tools"] is None


def test_explicit_live_limits_reach_the_native_model_and_subagent_policy(tmp_path):
    from deerflow.config.subagents_config import SubagentsAppConfig

    base = {
        "models": [{"name": "configured", "max_tokens": 8192, "when_thinking_disabled": {"max_tokens": 4096}}],
        "subagents": {"agents": {"untouched": {"token_budget": {"enabled": True, "max_tokens": 20000}}}},
    }
    fragment = {"subagents": {"custom_agents": {"technical-researcher": {"description": "research", "system_prompt": "research"}}}}
    value = build_config(base, fragment, tmp_path, "configured", max_output_tokens=393216, unlimited_budget=True)
    assert value["models"][0]["max_tokens"] == 393216
    assert value["models"][0]["when_thinking_disabled"]["max_tokens"] == 393216
    policies = SubagentsAppConfig.model_validate(value["subagents"])
    assert not policies.get_token_budget_for("acceptance-technical-researcher", summarization_enabled=True).enabled
    assert policies.get_token_budget_for("untouched").enabled
    assert base["models"][0]["max_tokens"] == 8192
    assert "acceptance-technical-researcher" not in base["subagents"]["agents"]


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
