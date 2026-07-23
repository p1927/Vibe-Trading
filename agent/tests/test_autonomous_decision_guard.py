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


def test_decision_retry_message_matches_scheduler_turn_for_compact_defer():
    msg = build_decision_retry_message(agent_id="aa_abc", turn_kind="bootstrap")
    assert is_autonomous_scheduler_turn(msg)
    assert "bootstrap" in msg.lower()


def test_decision_retry_message_gets_defer_compact_policy():
    from src.agent.loop import COMPACT_POLICY_DEFER, _resolve_compact_policy

    msg = build_decision_retry_message(agent_id="aa_abc", turn_kind="bootstrap")
    policy = _resolve_compact_policy(
        msg,
        {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_abc"},
    )
    assert policy == COMPACT_POLICY_DEFER


def test_infer_scheduler_turn_kind_from_bootstrap_prompt():
    from src.trade.autonomous_decision_guard import infer_scheduler_turn_kind

    assert infer_scheduler_turn_kind("# Autonomous agent turn (bootstrap)\n## Bootstrap checklist") == "bootstrap"
    assert infer_scheduler_turn_kind("# Autonomous agent turn (research)") == "research"
