"""ChatLLM: one provider call per request, and the producing provider decides think-tag parsing.

* ``chat()`` / ``stream_chat()`` wrapped the whole call in ``try ... except ImportError`` to make the
  observability span optional; an ImportError raised *inside* the call (a lazy import in the
  provider stack) was swallowed and the request re-sent, a second, billed, side-effect-bearing call.
* ``_parse_response`` read the globally configured provider, so a ``build_fallback()`` instance
  (different provider) parsed MiniMax ``<think>`` blocks wrongly in both directions.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from src.providers.chat import ChatLLM


def _chat_llm(invoke):
    llm = ChatLLM.__new__(ChatLLM)
    llm.model_name = "m"
    llm.runtime_snapshot = SimpleNamespace(provider="minimax")
    llm._llm = SimpleNamespace(invoke=invoke, bind_tools=lambda tools: SimpleNamespace(invoke=invoke))
    return llm


def test_import_error_inside_the_call_is_raised_not_retried() -> None:
    calls: list[int] = []

    def invoke(messages, config=None):
        calls.append(1)
        raise ImportError("lazy provider import failed")

    with pytest.raises(ImportError):
        _chat_llm(invoke).chat([{"role": "user", "content": "hi"}])

    assert calls == [1]


def test_chat_works_without_the_trade_observability_span(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def deny(name, *a, **k):
        if name.startswith("trade_integrations.observability"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    calls: list[int] = []

    def invoke(messages, config=None):
        calls.append(1)
        return AIMessage(content="ok")

    assert _chat_llm(invoke).chat([{"role": "user", "content": "hi"}]).content == "ok"
    assert calls == [1]


def test_think_blocks_follow_the_producing_provider_not_the_configured_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "nvidia")
    from src.config.accessor import reset_env_config

    reset_env_config()
    message = AIMessage(content="<think>plan</think>answer")

    minimax = ChatLLM._parse_response(message, "minimax")
    other = ChatLLM._parse_response(message, "nvidia")

    assert (minimax.content, minimax.reasoning_content) == ("answer", "plan")
    assert other.content == "<think>plan</think>answer"
    reset_env_config()
