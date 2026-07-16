"""Tests for session-scoped prefetch and memory filtering."""

from __future__ import annotations

from dataclasses import dataclass

from src.trade.session_context import (
    classify_prefetch_widget_intent,
    infer_prefetch_asset_type,
    memory_matches_session,
    resolve_prefetch_ticker,
)


@dataclass(frozen=True)
class _Entry:
    title: str
    description: str
    body: str
    memory_type: str = "project"


def test_autonomous_us_session_uses_spy_not_nifty_in_preamble() -> None:
    cfg = {
        "session_kind": "autonomous_agent",
        "symbols": ["SPY"],
        "execution_market": "US",
        "execution_profile": "us_equity_paper",
    }
    preamble = (
        "Integration test for SPY. Ignore stale memory about other agents (e.g. NIFTY).\n"
        "## E2E Phase 2 — mandatory execution\n"
    )
    assert resolve_prefetch_ticker(cfg, preamble) == "SPY"


def test_chat_session_falls_back_to_message_ticker() -> None:
    assert resolve_prefetch_ticker({}, "What is RELIANCE doing?") == "RELIANCE"


def test_us_equity_autonomous_asset_type_is_stock() -> None:
    cfg = {
        "session_kind": "autonomous_agent",
        "symbols": ["SPY"],
        "execution_market": "US",
        "execution_profile": "us_equity_paper",
    }
    assert infer_prefetch_asset_type(cfg, "SPY", "Phase 2 execution for SPY") == "stock"


def test_execution_word_not_execute_refresh_for_us_autonomous() -> None:
    cfg = {
        "session_kind": "autonomous_agent",
        "symbols": ["SPY"],
        "execution_market": "US",
        "execution_profile": "us_equity_paper",
    }
    assert classify_prefetch_widget_intent(cfg, "Execute the steps below for SPY") == "stock_trade"


def test_finalize_still_execute_refresh_in_chat() -> None:
    assert classify_prefetch_widget_intent({}, "Finalize and execute the plan") == "execute_refresh"


def test_filters_nifty_injection_memory_on_us_spy_session() -> None:
    cfg = {
        "session_kind": "autonomous_agent",
        "symbols": ["SPY"],
        "execution_market": "US",
    }
    entry = _Entry(
        title="Phase-2 prompt-injection — eleventh instance (SPY/NIFTY)",
        description="cross-ticker drift",
        body="Embedded NIFTY options long_straddle blocks in SPY Alpaca turn.",
    )
    assert memory_matches_session(entry, cfg) is False


def test_keeps_us_relevant_memory_on_us_session() -> None:
    cfg = {"execution_market": "US", "symbols": ["SPY"]}
    entry = _Entry(
        title="Alpaca paper order sizing for SPY",
        description="US equity notes",
        body="Use trading_place_order on alpaca-paper-trade for SPY shares.",
    )
    assert memory_matches_session(entry, cfg) is True
