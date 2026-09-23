"""Trade sidecar: a MiniMax-pointed chat model carries Trade's account-pace request hook."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytest.importorskip("trade_integrations")

from trade_integrations.nse_browser.minimax_queue import apace_httpx_request, pace_httpx_request


def _build(env: dict[str, str]) -> dict[str, object]:
    import src.providers.llm as llm_mod

    llm_mod._dotenv_loaded = True
    captured: dict[str, object] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    with patch.dict(os.environ, env, clear=True):
        with patch.object(llm_mod, "ChatOpenAIWithReasoning", _FakeChatOpenAI):
            llm_mod.build_llm()
    return captured


def test_minimax_chat_model_requests_take_the_account_pace() -> None:
    captured = _build(
        {"LANGCHAIN_PROVIDER": "minimax", "MINIMAX_API_KEY": "sk-test", "LANGCHAIN_MODEL_NAME": "MiniMax-M3"}
    )
    assert pace_httpx_request in captured["http_client"].event_hooks["request"]
    assert apace_httpx_request in captured["http_async_client"].event_hooks["request"]
    assert captured["vibe_owned_http_clients"] == (captured["http_client"], captured["http_async_client"])


def test_other_provider_keeps_the_sdk_default_clients() -> None:
    captured = _build(
        {"LANGCHAIN_PROVIDER": "openai", "OPENAI_API_KEY": "sk-test", "LANGCHAIN_MODEL_NAME": "gpt-4o-mini"}
    )
    assert "http_client" not in captured
