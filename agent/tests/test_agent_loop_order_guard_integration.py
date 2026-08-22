"""Kill switch blocks a live order requested through the FULL AgentLoop.

test_killswitch_blocks_orders.py already proves the gate itself is correct by
calling ``LiveOrderGuardTool.execute(...)`` directly. That's the right test
for the gate in isolation, but it never exercises the path a real attack or a
hallucinating/compromised LLM would actually take: asking the ReAct loop for
a tool call by name, with LLM-controlled arguments, and trusting the loop's
own tool-dispatch code to route it through the gate. This test drives that
full path — AgentLoop.run() with a scripted LLM turn requesting the live
order tool — using the ScriptedChatLLM harness in scripted_llm_helpers.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.live.paths as paths
from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry
from src.live.halt import trip_halt
from src.live.mandate.model import (
    MANDATE_SCHEMA_VERSION,
)
from src.live.order_guard import LiveOrderGuardTool
from src.tools.mcp import MCPRemoteToolSpec
from tests.scripted_llm_helpers import (
    FakeSearchSymbolTool,
    ScriptedChatLLM,
    llm_text,
    llm_tool_call,
)


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


class _MockAdapter:
    def __init__(self) -> None:
        self.server_name = "robinhood"
        self.calls: list[str] = []

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        self.calls.append(remote_name)
        return {"status": "ok", "order_id": "rh_x", "state": "accepted"}


def _order_spec() -> MCPRemoteToolSpec:
    return MCPRemoteToolSpec(
        server_name="robinhood",
        remote_name="place_equity_order",
        local_name="mcp_robinhood_place_equity_order",
        description="Place an order.",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
    )


def _write_mandate(live_runtime: Path) -> None:
    broker = live_runtime / "live" / "robinhood"
    broker.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    payload = {
        "schema_version": MANDATE_SCHEMA_VERSION,
        "hard_caps": {
            "account_funding_usd": 5000.0,
            "max_order_notional_usd": 750.0,
            "max_total_exposure_usd": 5000.0,
            "max_leverage": 1.0,
            "allowed_instruments": ["equity", "etf"],
            "max_trades_per_day": 5,
        },
        "universe": {
            "asset_classes": ["us_equity", "us_etf"],
            "min_market_cap_usd": None,
            "min_avg_daily_volume_usd": None,
            "exclude_symbols": [],
        },
        "consent": {
            "created_at": created.isoformat(),
            "consent_token_sha256": "deadbeef",
            "broker": "robinhood",
            "account_ref": "acct_ref",
            "expires_at": (created + timedelta(days=30)).isoformat(),
        },
    }
    (broker / "mandate.json").write_text(json.dumps(payload), encoding="utf-8")


def test_llm_requested_order_blocked_by_halt_through_full_loop(live_runtime: Path) -> None:
    """A scripted LLM turn asks the loop to place a live order while HALT is
    tripped. The loop must dispatch the call through the gate (not bypass it),
    the gate must refuse, and the mock adapter must never see the order call —
    end to end, not just at the gate's own unit test.

    The scripted run resolves ``AAPL`` via ``search_symbol`` in its own turn
    first — skip that and ``AgentLoop``'s ``GroundingLedger`` identity gate
    blocks the order tool call *before it ever reaches the guard*, for
    "unresolved instrument identity", regardless of halt state. Confirmed by
    running this scenario without tripping halt at all: the adapter still saw
    zero calls — meaning the un-fixed version of this test passed for the
    wrong reason and would keep passing even if the halt check itself were
    deleted from `LiveOrderGuardTool.execute`. See
    `.claude/backlog/items/2026-08-21-autonomous-watcher-dst-lite-tests.md`
    for the write-up.
    """
    _write_mandate(live_runtime)
    trip_halt(by="file", reason="test halt")

    adapter = _MockAdapter()
    guard = LiveOrderGuardTool(adapter, _order_spec(), broker="robinhood", session_id="s1")

    registry = ToolRegistry()
    registry.register(guard)
    registry.register(FakeSearchSymbolTool())

    llm = ScriptedChatLLM(
        [
            llm_tool_call("search_symbol", {"query": "AAPL"}),
            llm_tool_call(
                guard.name,
                {"symbol": "AAPL", "side": "buy", "instrument_type": "equity", "notional_usd": 100.0},
            ),
            llm_text("The order was blocked by the kill switch, as expected."),
        ]
    )

    agent = AgentLoop(registry=registry, llm=llm, max_iterations=5)
    run_dir = live_runtime / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    agent.memory.run_dir = str(run_dir)

    agent.run(user_message="Buy 1 share of AAPL")

    # The gate must have refused, and the adapter — the only path to a real
    # order — must never have been called, regardless of what the LLM asked.
    assert adapter.calls == []
