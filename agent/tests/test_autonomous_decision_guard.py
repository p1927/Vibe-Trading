"""Tests for autonomous decision guard."""

from __future__ import annotations

from src.trade.autonomous_decision_guard import (
    build_decision_retry_message,
    is_autonomous_scheduler_turn,
    needs_decision_guard,
)


def test_detects_scheduler_turn():
    assert is_autonomous_scheduler_turn("# Autonomous agent turn (bootstrap)")
    assert not is_autonomous_scheduler_turn("What is NIFTY doing?")


def test_needs_guard_when_decision_tool_missing():
    cfg = {
        "session_kind": "autonomous_agent",
        "autonomous_agent_id": "aa_test",
    }
    prompt = "# Autonomous agent turn (research)\nDo work."
    assert needs_decision_guard(prompt, {"get_autonomous_agent_status"}, cfg) is True


def test_no_guard_when_decision_recorded():
    cfg = {
        "session_kind": "autonomous_agent",
        "autonomous_agent_id": "aa_test",
    }
    prompt = "# Autonomous agent turn (bootstrap)"
    tools = {"get_autonomous_agent_status", "record_autonomous_decision"}
    assert needs_decision_guard(prompt, tools, cfg) is False


def test_retry_message_includes_agent_id():
    msg = build_decision_retry_message(agent_id="aa_abc")
    assert "aa_abc" in msg
    assert "record_autonomous_decision" in msg
