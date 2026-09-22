"""Trade D75: the agent's "Today is" line comes from the simulator clock, never the wall clock."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.context import ContextBuilder
from src.agent.memory import WorkspaceMemory
from src.agent.tools import ToolRegistry
from src.trade import hub_bridge

SIM_NOW = datetime(2025, 1, 6, 4, 30, tzinfo=timezone.utc)  # a replayed Monday


@pytest.mark.parametrize(
    "session_config",
    [{}, {"agent_mode": "observe"}, {"session_kind": "news_scenario_advisor"}],
)
def test_system_prompt_date_is_sim_clock(monkeypatch, session_config) -> None:
    monkeypatch.setattr(hub_bridge, "agent_now_utc", lambda: SIM_NOW)
    prompt = ContextBuilder(ToolRegistry(), WorkspaceMemory(), session_config=session_config).build_system_prompt()
    assert "Monday, January 06, 2025 04:30 UTC" in prompt


def test_agent_now_utc_reads_replay_clock(monkeypatch) -> None:
    if hub_bridge.trade_repo_root() is None:
        pytest.skip("standalone vibetrading: no Trade stack, wall clock by design")
    hub_bridge.ensure_trade_stack_path()
    from trade_integrations.autonomous_agents import replay_clock

    monkeypatch.setattr(replay_clock, "current_utc", lambda *a, **k: SIM_NOW)
    assert hub_bridge.agent_now_utc() == SIM_NOW
