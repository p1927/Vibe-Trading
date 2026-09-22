"""D220: an autonomous scheduler turn gets its turn kind's fixed tool list and the agent prompt."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from src.agent.context import ContextBuilder
from src.agent.memory import WorkspaceMemory
from src.agent.skills import SkillsLoader
from src.agent.tools import ToolRegistry
from src.session import autonomous_agent_profile as profile

_AGENT = {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_t", "agent_mode": "trade"}


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} does things"
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.repeatable = False
        self.is_readonly = True

    def to_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def _registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for n in names:
        reg.register(_Tool(n))
    return reg


def _no_capability_resolver(monkeypatch) -> None:
    """Take the resolver-unavailable path (static widget/execute strip) so the test needs no trade stack."""

    def _raise() -> None:
        raise RuntimeError("no trade stack in this test")

    monkeypatch.setattr("src.trade.hub_bridge.ensure_trade_stack_path", _raise)


def test_scheduler_turn_keeps_only_its_kind_tools_in_registry_order(monkeypatch) -> None:
    _no_capability_resolver(monkeypatch)
    reg = _registry(
        "run_swarm", "mcp_openalgo_record_autonomous_decision", "backtest", "load_skill",
        "mcp_openalgo_submit_partial_close", "mcp_openalgo_get_us_quote", "mcp_openalgo_get_order_book",
    )
    research = profile.filter_registry_for_autonomous_agent(reg, {**_AGENT, "turn_kind": "research"})
    assert list(research._tools) == [
        "mcp_openalgo_record_autonomous_decision", "load_skill",
        "mcp_openalgo_submit_partial_close", "mcp_openalgo_get_us_quote", "mcp_openalgo_get_order_book",
    ]
    watch = profile.filter_registry_for_autonomous_agent(reg, {**_AGENT, "turn_kind": "watch_report"})
    assert "mcp_openalgo_submit_partial_close" not in watch._tools  # observe: no exits/orders
    chat = profile.filter_registry_for_autonomous_agent(reg, _AGENT)  # user chat turn: whole registry
    assert list(chat._tools) == list(reg._tools)
    with pytest.raises(ValueError):
        profile.filter_registry_for_autonomous_agent(reg, {**_AGENT, "turn_kind": "made_up"})


def test_critical_tools_are_on_every_trading_turn_kind() -> None:
    """D54: decision, status, watches, positions and the execution/exit path are never cut."""
    critical = {"record_autonomous_decision", "get_autonomous_agent_status", "set_agent_watch_spec",
                "get_position_book", "load_skill"}
    act = {"execute_autonomous_basket", "submit_bridge_execution_intent", "submit_partial_close"}
    for kind in profile.TURN_KINDS:
        assert critical <= profile.TURN_KIND_TOOLS[kind], kind
        if kind != "watch_report":
            assert act <= profile.TURN_KIND_TOOLS[kind], kind


def test_every_listed_tool_still_exists() -> None:
    """A renamed or removed tool must fail here, not silently vanish from an agent's turn."""
    from src.tools import _discover_subclasses

    local = {cls.name for cls in _discover_subclasses()}
    mcp_dir = Path(__file__).resolve().parents[3] / "openalgo" / "mcp"
    if not mcp_dir.is_dir():
        pytest.skip("openalgo submodule not checked out; MCP tool names cannot be checked")
    mcp_src = "\n".join(p.read_text(encoding="utf-8") for p in mcp_dir.glob("*.py"))
    mcp = set(re.findall(r"^\s*(?:async\s+)?def\s+([a-z_0-9]+)\s*\(", mcp_src, re.M))
    listed = set().union(*profile.TURN_KIND_TOOLS.values())
    assert sorted(listed - local - mcp) == []


def test_scheduler_turn_system_prompt_is_the_agent_prompt(monkeypatch) -> None:
    monkeypatch.setattr("src.trade.hub_bridge.agent_now_utc", lambda: __import__("datetime").datetime(2026, 9, 1))
    reg = _registry("load_skill", "mcp_openalgo_get_quote")
    cfg = {**_AGENT, "turn_kind": "strategy_revision"}
    prompt = ContextBuilder(reg, WorkspaceMemory(), SkillsLoader(), session_config=cfg).build_system_prompt()
    assert "autonomous trading agent" in prompt and "`strategy_revision` turn" in prompt
    assert "mcp_openalgo_get_quote" not in prompt  # no prose tool list; the schemas carry it
    assert "Backtest" not in prompt and "Shadow Account" not in prompt  # no research-agent routing
    assert "- options-strategy:" in prompt and "- trade-journal:" not in prompt  # turn-kind skills only
    chat = ContextBuilder(reg, WorkspaceMemory(), SkillsLoader(), session_config=_AGENT).build_system_prompt()
    assert "mcp_openalgo_get_quote" not in chat  # agent chat turns drop the duplicate prose list too
