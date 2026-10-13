"""The direct-order guard in ``AgentLoop._invoke_tool`` must never fail open.

``_invoke_tool`` imported ``assert_direct_order_tool_allowed`` inside ``try ... except ImportError:
pass``, so a broken import inside the Trade stack silently switched the guard off and a raw
broker order tool ran in an autonomous agent's session. Inside the monorepo a failing import has
to stop the call.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from src.agent.loop import AgentLoop
from src.agent.tools import BaseTool, ToolRegistry
from tests.scripted_llm_helpers import FakeSearchSymbolTool, ScriptedChatLLM, llm_text, llm_tool_call

RAW_ORDER_TOOL = "mcp_openalgo_place_order"


class _RawOrderTool(BaseTool):
    name = RAW_ORDER_TOOL
    description = "Place a raw broker order."
    parameters = {"type": "object", "properties": {}, "additionalProperties": True}
    is_readonly = False

    def __init__(self) -> None:
        self.executed = 0

    def execute(self, **kwargs: Any) -> str:
        self.executed += 1
        return '{"status": "ok"}'


def _run(tmp_path: Path, session_config: dict) -> tuple[_RawOrderTool, dict]:
    order = _RawOrderTool()
    registry = ToolRegistry()
    registry.register(order)
    registry.register(FakeSearchSymbolTool())
    llm = ScriptedChatLLM(
        [llm_tool_call(RAW_ORDER_TOOL, {}), llm_text("done")]  # no symbol: nothing for grounding to block
    )
    agent = AgentLoop(registry=registry, llm=llm, max_iterations=4)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    agent.memory.run_dir = str(run_dir)
    try:
        result = agent.run(user_message="place the order", session_config=session_config)
    except ImportError as exc:  # failing loud is also fine
        result = {"status": "raised", "error": str(exc)}
    return order, result


@pytest.fixture(autouse=True)
def _wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The system prompt asks the simulator for "now" (D75); this test is about the order guard."""
    from datetime import datetime, timezone

    monkeypatch.setattr("src.trade.hub_bridge.agent_now_utc", lambda: datetime.now(timezone.utc))


AGENT_SESSION = {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_test"}


def test_autonomous_agent_session_cannot_run_a_raw_order_tool(tmp_path: Path) -> None:
    order, _ = _run(tmp_path, AGENT_SESSION)
    assert order.executed == 0


def test_broken_guard_import_does_not_switch_the_guard_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "trade_integrations.execution.enforce", None)  # import -> ImportError
    order, _ = _run(tmp_path, AGENT_SESSION)
    assert order.executed == 0


def test_chat_session_may_still_run_the_tool(tmp_path: Path) -> None:
    order, _ = _run(tmp_path, {})
    assert order.executed == 1
