"""A fallback provider with no key of its own must not inherit the configured provider's mirrored OPENAI_API_KEY."""

from src.providers.capabilities import get_llm_credentials


def test_fallback_provider_does_not_borrow_the_mirrored_openai_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-configured-provider")

    fallback = get_llm_credentials("deepseek", "deepseek-chat", allow_generic_openai_env=False)
    assert fallback["api_key"] == ""

    primary = get_llm_credentials("deepseek", "deepseek-chat", allow_generic_openai_env=True)
    assert primary["api_key"] == "sk-configured-provider"
