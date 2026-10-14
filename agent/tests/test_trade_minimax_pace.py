"""Trade sidecar: a MiniMax-pointed chat model carries Trade's account-pace request hook."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytest.importorskip("trade_integrations")

from trade_integrations.dataflows.model_adapters.minimax_queue import apace_httpx_request, pace_httpx_request


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


def test_agent_loop_runs_labelled_for_the_pace_wait_measurement() -> None:
    """Trade D288: the agent loop's thread claims pace slots as `interactive` for a person's
    message and `agent_turn` when the dispatcher passed a turn kind; unlabelled code is `batch`."""
    from trade_integrations.rate_limit import _PACE_CALLER_KIND

    from src.session.service_hooks import run_as_pace_caller

    assert run_as_pace_caller(None, _PACE_CALLER_KIND.get) == "interactive"
    assert run_as_pace_caller("strategy_revision", _PACE_CALLER_KIND.get) == "agent_turn"
    assert _PACE_CALLER_KIND.get() == "batch"


def test_standalone_vibe_trading_is_left_unpaced_and_unlabelled() -> None:
    """No Trade stack (trade_repo_root is None): everything runs exactly as upstream does."""
    import contextlib

    from src.providers import trade_minimax_pace as pace

    sentinel = (object(), object())
    with patch("src.trade.hub_bridge.trade_repo_root", return_value=None):
        assert pace.in_trade_stack() is False
        assert pace.with_trade_minimax_pace(sentinel, "https://api.minimax.io/v1") is sentinel
        assert isinstance(pace.trade_llm_span("minimax", "m"), type(contextlib.nullcontext({})))
        from src.session.service_hooks import run_as_pace_caller

        assert run_as_pace_caller(None, lambda: 7) == 7


@pytest.mark.parametrize(
    "module",
    [
        "trade_integrations.dataflows.model_adapters.minimax_queue",
        "trade_integrations.observability.hooks",
        "trade_integrations.rate_limit",
    ],
)
def test_broken_trade_integrations_inside_the_monorepo_raises_instead_of_dropping_the_pace(module: str) -> None:
    """Inside the monorepo an ImportError is a broken install, not 'standalone': the old fallback
    silently bypassed MiniMax's account pace (and lost the call log)."""
    import sys

    from src.providers import trade_minimax_pace as pace
    from src.session.service_hooks import run_as_pace_caller

    assert pace.in_trade_stack() is True
    with patch.dict(sys.modules, {module: None}):  # a None entry makes `import module` raise ImportError
        if module.endswith("minimax_queue"):
            with pytest.raises(ImportError):
                pace.with_trade_minimax_pace(None, "https://api.minimax.io/v1")
        elif module.endswith("hooks"):
            with pytest.raises(ImportError):
                pace.trade_llm_span("minimax", "m")
        else:
            with pytest.raises(ImportError):
                run_as_pace_caller(None, lambda: 1)
