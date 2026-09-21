"""build_llm(provider=X) for a provider other than the configured one must not inherit the
configured provider's base URL (via the OPENAI_BASE_URL that _sync_provider_env mirrors)."""

from src.providers import llm as llm_mod


def test_override_provider_uses_own_default_base_url(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "k-minimax")
    monkeypatch.setenv("NVIDIA_API_KEY", "k-nvidia")
    monkeypatch.delenv("NVIDIA_BASE_URL", raising=False)
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    llm_mod.build_llm(model_name="MiniMax-M3")  # mirrors minimax URL into OPENAI_BASE_URL
    fallback = llm_mod.build_llm(model_name="nvidia/nemotron-3-ultra-550b-a55b", provider="nvidia")
    assert "nvidia.com" in str(fallback.openai_api_base)
