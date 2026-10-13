"""The LLM preflight must see a .env loaded after the config singleton was cached."""

from src import preflight


def _stub_provider(monkeypatch, loader) -> None:
    import src.providers.llm as llm

    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "gpt-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr(llm, "_ensure_dotenv", loader)
    monkeypatch.setattr(llm, "_sync_provider_env", lambda: None)
    monkeypatch.setattr(
        llm,
        "provider_diagnostics",
        lambda: {"base_url": "https://example.test/v1", "timeout_seconds": 1, "max_retries": 0, "proxy": {}},
    )


def test_cached_empty_config_is_rebuilt_after_dotenv_load(monkeypatch) -> None:
    from src.config.accessor import get_env_config, reset_env_config

    monkeypatch.setenv("LANGCHAIN_PROVIDER", "")
    reset_env_config()
    assert get_env_config().llm.langchain_provider.strip() == ""  # cached with no provider

    _stub_provider(monkeypatch, lambda: monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai"))
    result = preflight._check_llm_provider(ping=False)
    assert result.status != "not_configured", result
    reset_env_config()


def test_still_not_configured_when_dotenv_has_no_provider(monkeypatch) -> None:
    from src.config.accessor import reset_env_config

    monkeypatch.setenv("LANGCHAIN_PROVIDER", "")
    reset_env_config()
    _stub_provider(monkeypatch, lambda: None)
    assert preflight._check_llm_provider(ping=False).status == "not_configured"
    reset_env_config()
