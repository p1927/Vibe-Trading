"""Tests for widget guard safety net."""

from __future__ import annotations

import pytest

from src.trade.widget_guard import needs_widget_guard


class TestWidgetGuard:
    def test_triggers_on_strategy_prose_without_widget_tool(self):
        assert needs_widget_guard(
            "What options strategy for NIFTY?",
            "I recommend an iron condor at 24000 CE and 23500 PE strikes.",
            [],
        )

    def test_skips_when_widget_tool_called(self):
        assert not needs_widget_guard(
            "NIFTY options",
            "Iron condor recommended.",
            ["get_options_trade_widget"],
        )

    def test_skips_when_user_intent_none(self):
        assert not needs_widget_guard(
            "What's NIFTY doing?",
            "Iron condor max loss is limited at these strikes.",
            [],
        )

    def test_skips_when_guard_disabled(self, monkeypatch):
        monkeypatch.setenv("OPTIONS_WIDGET_GUARD_ENABLED", "false")
        assert not needs_widget_guard(
            "NIFTY strategy",
            "Try iron condor with CE strike 24000.",
            [],
        )
