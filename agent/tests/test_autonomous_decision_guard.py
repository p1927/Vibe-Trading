"""Tests for autonomous decision guard."""

from __future__ import annotations

from src.trade.autonomous_decision_guard import (
    build_decision_retry_message,
    needs_decision_guard,
)


def test_user_text_quoting_the_scheduler_marker_is_not_a_scheduler_turn():
    # D220: the turn kind is data on the attempt; a user chat turn has none, whatever it quotes.
    cfg = {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_test"}
    assert needs_decision_guard(None, {"get_autonomous_agent_status"}, cfg) is False


def test_needs_guard_when_decision_tool_missing():
    cfg = {
        "session_kind": "autonomous_agent",
        "autonomous_agent_id": "aa_test",
    }
    assert needs_decision_guard("research", {"get_autonomous_agent_status"}, cfg) is True


def test_no_guard_when_decision_recorded():
    cfg = {
        "session_kind": "autonomous_agent",
        "autonomous_agent_id": "aa_test",
    }
    tools = {"get_autonomous_agent_status", "record_autonomous_decision"}
    assert needs_decision_guard("bootstrap", tools, cfg) is False


def test_no_guard_when_mcp_wrapped_decision_recorded():
    cfg = {
        "session_kind": "autonomous_agent",
        "autonomous_agent_id": "aa_test",
    }
    tools = {"mcp_openalgo_record_autonomous_decision"}
    assert needs_decision_guard("bootstrap", tools, cfg) is False


def test_retry_message_includes_agent_id():
    msg = build_decision_retry_message(agent_id="aa_abc")
    assert "aa_abc" in msg
    assert "record_autonomous_decision" in msg


def test_decision_retry_message_names_the_turn_kind():
    msg = build_decision_retry_message(agent_id="aa_abc", turn_kind="bootstrap")
    assert "bootstrap" in msg.lower()


def test_bootstrap_finalize_guard_keys_on_turn_kind_not_prompt_text():
    from src.trade.bootstrap_finalize_guard import needs_bootstrap_finalize_guard

    cfg = {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_test"}
    # a chat turn (no turn_kind) never counts, however bootstrap-like its text is
    assert needs_bootstrap_finalize_guard(None, {"record_autonomous_decision"}, cfg) is False
    assert needs_bootstrap_finalize_guard("research", {"record_autonomous_decision"}, cfg) is False
