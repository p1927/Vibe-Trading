"""Compact policy for autonomous scheduler turns — defer mid-attempt compaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.agent.loop as loop_mod
from src.agent.loop import (
    COMPACT_POLICY_DEFER,
    COMPACT_POLICY_NORMAL,
    AgentLoop,
    _adjust_cut_idx_for_tool_batches,
    _resolve_compact_policy,
)
from src.agent.trace import TraceWriter


class _SummaryLLM:
    model_name = "stub"

    class _Resp:
        content = "## Goal\ncompressed"

    def chat(self, messages: list[dict[str, Any]], **_: Any) -> _Resp:
        return self._Resp()


def test_resolve_compact_policy_defers_for_bootstrap_scheduler_turn() -> None:
    policy = _resolve_compact_policy(
        "# Autonomous agent turn (bootstrap)\n## Bootstrap checklist",
        {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_test"},
    )
    assert policy == COMPACT_POLICY_DEFER


def test_resolve_compact_policy_normal_for_user_chat() -> None:
    policy = _resolve_compact_policy(
        "What is NIFTY doing today?",
        {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_test"},
    )
    assert policy == COMPACT_POLICY_NORMAL


def test_adjust_cut_idx_keeps_tool_batch_in_tail() -> None:
    body = [
        {"role": "user", "content": "old"},
        {
            "role": "assistant",
            "content": "parallel reads",
            "tool_calls": [{"id": "c1", "function": {"name": "a"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"status":"ok"}'},
        {"role": "tool", "tool_call_id": "c2", "content": '{"status":"ok"}'},
    ]
    # Cut between assistant and first tool result would orphan tools
    assert _adjust_cut_idx_for_tool_batches(body, 2) == 1


def test_scheduler_turn_defers_auto_compact_below_hard_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_mod, "_token_threshold", lambda: 100)
    monkeypatch.setattr(loop_mod, "_compact_hard_cap", lambda: 500_000)

    agent = AgentLoop(registry=MagicMock(), llm=_SummaryLLM(), max_iterations=1)
    agent._compact_policy = COMPACT_POLICY_DEFER
    agent._auto_compact_count = 0
    agent._emergency_compact_used = False
    agent._tail_token_budget = loop_mod.TAIL_TOKEN_BUDGET

    messages = [
        {"role": "system", "content": "sys " + ("x" * 400)},
        {"role": "user", "content": "bootstrap " + ("y" * 800)},
    ]
    trace = TraceWriter(tmp_path / "trace")
    try:
        agent._apply_context_pressure_management(messages, tmp_path / "run", trace, iteration=1)
    finally:
        trace.close()

    assert agent._auto_compact_count == 0
    assert len(messages) == 2
    assert "Conversation compressed" not in messages[-1]["content"]


def test_auto_compact_runs_at_most_once_per_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_mod, "_token_threshold", lambda: 50)

    agent = AgentLoop(registry=MagicMock(), llm=_SummaryLLM(), max_iterations=1)
    agent._compact_policy = COMPACT_POLICY_NORMAL
    agent._auto_compact_count = 0
    agent._tail_token_budget = 200

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "older context " + ("a" * 5_000)},
        {"role": "user", "content": "large block " + ("z" * 20_000)},
    ]
    trace = TraceWriter(tmp_path / "trace")
    try:
        agent._apply_context_pressure_management(messages, tmp_path / "run", trace, iteration=1)
        first_count = agent._auto_compact_count
        agent._apply_context_pressure_management(messages, tmp_path / "run", trace, iteration=2)
    finally:
        trace.close()

    assert first_count == 1
    assert agent._auto_compact_count == 1
    assert "Conversation compressed" in messages[1]["content"]


def test_bootstrap_parallel_tool_results_survive_deferred_compact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduler defer keeps status/research payloads in context (no compact at ~45k)."""
    monkeypatch.setattr(loop_mod, "_token_threshold", lambda: 40_000)
    monkeypatch.setattr(loop_mod, "_compact_hard_cap", lambda: 120_000)

    agent = AgentLoop(registry=MagicMock(), llm=_SummaryLLM(), max_iterations=1)
    agent._compact_policy = COMPACT_POLICY_DEFER
    agent._auto_compact_count = 0

    status_payload = '{"status":"ok","agent":{"id":"aa_x"}}'
    messages = [
        {"role": "system", "content": "tools " + ("t" * 30_000)},
        {"role": "user", "content": "# Autonomous agent turn (bootstrap)\n## Bootstrap checklist"},
        {
            "role": "assistant",
            "content": "parallel reads",
            "tool_calls": [
                {"id": "call_a", "function": {"name": "get_autonomous_agent_status"}},
                {"id": "call_b", "function": {"name": "get_research_status"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_a", "name": "get_autonomous_agent_status", "content": status_payload},
        {"role": "tool", "tool_call_id": "call_b", "name": "get_research_status", "content": '{"status":"ok"}'},
    ]
    trace = TraceWriter(tmp_path / "trace")
    try:
        agent._apply_context_pressure_management(messages, tmp_path / "run", trace, iteration=4)
    finally:
        trace.close()

    assert agent._auto_compact_count == 0
    tool_contents = [m["content"] for m in messages if m.get("role") == "tool"]
    assert status_payload in tool_contents
    assert all("Result from earlier context" not in c for c in tool_contents)
