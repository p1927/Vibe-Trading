"""Compaction cascade: tool-batch-aware tail cuts and at-most-once auto compact per attempt.

Autonomous scheduler turns compact like any other turn (D220); the old defer policy is gone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.agent.loop as loop_mod
from src.agent.compaction_policy import adjust_cut_idx_for_tool_batches
from src.agent.loop import AgentLoop
from src.agent.trace import TraceWriter


class _SummaryLLM:
    model_name = "stub"

    class _Resp:
        content = "## Goal\ncompressed"

    def chat(self, messages: list[dict[str, Any]], **_: Any) -> _Resp:
        return self._Resp()


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
    assert adjust_cut_idx_for_tool_batches(body, 2) == 1


def test_auto_compact_runs_at_most_once_per_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop_mod, "_token_threshold", lambda: 50)

    agent = AgentLoop(registry=MagicMock(), llm=_SummaryLLM(), max_iterations=1)
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
