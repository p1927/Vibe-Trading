"""Tests for widget intent classification."""

from __future__ import annotations

import pytest

from src.trade.widget_intent import classify_widget_intent


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("Show RELIANCE option chain", "none"),
        ("What's NIFTY doing today?", "index_outlook"),
        ("Iron condor on NIFTY", "options_strategy"),
        ("Best option strategy for RELIANCE", "options_strategy"),
        ("Should I buy RELIANCE shares?", "stock_trade"),
        ("Finalize and execute the plan", "execute_refresh"),
        ("NIFTY events this week", "none"),
    ],
)
def test_classify_widget_intent(msg: str, expected: str) -> None:
    assert classify_widget_intent(msg) == expected


def test_browse_overrides_strategy_keywords() -> None:
    assert classify_widget_intent("Browse NIFTY strikes and OI") == "none"


def test_options_beats_index_on_mixed() -> None:
    assert classify_widget_intent("NIFTY direction and iron condor") == "options_strategy"
