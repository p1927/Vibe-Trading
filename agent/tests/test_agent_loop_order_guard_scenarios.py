"""More adversarial full-AgentLoop order-guard scenarios (Domain B starter set).

Extends ``test_agent_loop_order_guard_integration.py``'s pattern — a scripted
LLM turn requesting the live order tool, driven through a real
``AgentLoop.run()``, asserting the mock broker adapter never sees the real
order call — with the next scenarios named in the testing-strategy backlog
(``.claude/backlog/items/2026-08-21-autonomous-watcher-dst-lite-tests.md``):
mandate expiry and an order that exceeds a mandate cap. ("Concurrent
scheduled + manual trigger" is covered separately at the scheduler/triggers
layer — see ``test_scheduler_triggers_dst_lite.py`` — since that's a
dispatch-dedup concern, not something the order guard itself decides.)

Unlike the halt scenario (which denies before any read), a cap-breach denial
happens *after* the gate reads positions/balance (see
``LiveOrderGuardTool.execute``'s order: mandate → expiry → halt → intent →
reads → ``check_mandate``) — so those tests assert on the specific
``place_equity_order`` remote call, not an empty call list, and additionally
assert a read call *did* happen, proving the denial came from the breach
check rather than an earlier short-circuit swallowing the assertion's
intent.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.live.paths as paths
from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry
from src.live.mandate.model import MANDATE_SCHEMA_VERSION
from src.live.order_guard import LiveOrderGuardTool
from src.tools.mcp import MCPRemoteToolSpec
from tests.scripted_llm_helpers import (
    FakeSearchSymbolTool,
    ScriptedChatLLM,
    llm_text,
    llm_tool_call,
)

_ORDER_TOOL_REMOTE_NAME = "place_equity_order"


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
        remote_name=_ORDER_TOOL_REMOTE_NAME,
        local_name="mcp_robinhood_place_equity_order",
        description="Place an order.",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
    )


def _write_mandate(
    live_runtime: Path,
    *,
    max_order_notional_usd: float = 750.0,
    expires_at: datetime | None = None,
) -> None:
    broker = live_runtime / "live" / "robinhood"
    broker.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    if expires_at is None:
        expires_at = created + timedelta(days=30)
    payload = {
        "schema_version": MANDATE_SCHEMA_VERSION,
        "hard_caps": {
            "account_funding_usd": 5000.0,
            "max_order_notional_usd": max_order_notional_usd,
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
            "expires_at": expires_at.isoformat(),
        },
    }
    (broker / "mandate.json").write_text(json.dumps(payload), encoding="utf-8")


def _run_order_request(
    live_runtime: Path, *, notional_usd: float
) -> tuple[_MockAdapter, LiveOrderGuardTool]:
    adapter = _MockAdapter()
    guard = LiveOrderGuardTool(adapter, _order_spec(), broker="robinhood", session_id="s1")

    registry = ToolRegistry()
    registry.register(guard)
    registry.register(FakeSearchSymbolTool())

    llm = ScriptedChatLLM(
        [
            # Resolve AAPL's identity in its own turn FIRST — required by
            # AgentLoop's GroundingLedger before the order tool call below
            # will even be dispatched. See FakeSearchSymbolTool's docstring.
            llm_tool_call("search_symbol", {"query": "AAPL"}),
            llm_tool_call(
                guard.name,
                {
                    "symbol": "AAPL",
                    "side": "buy",
                    "instrument_type": "equity",
                    "notional_usd": notional_usd,
                },
            ),
            llm_text("Order request completed."),
        ]
    )

    agent = AgentLoop(registry=registry, llm=llm, max_iterations=5)
    run_dir = live_runtime / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    agent.memory.run_dir = str(run_dir)

    agent.run(user_message="Buy 1 share of AAPL")
    return adapter, guard


def test_expired_mandate_blocked_through_full_loop(live_runtime: Path) -> None:
    """A mandate whose `consent.expires_at` has already passed must deny the
    order before any broker call — including the positions/balance reads,
    since expiry is checked before halt/intent/reads in the gate's fixed
    order (see `LiveOrderGuardTool.execute`'s docstring)."""
    already_expired = datetime.now(timezone.utc) - timedelta(days=1)
    _write_mandate(live_runtime, expires_at=already_expired)

    adapter, _guard = _run_order_request(live_runtime, notional_usd=100.0)

    assert adapter.calls == [], (
        f"expired mandate should deny before any broker call (reads included), "
        f"got calls={adapter.calls!r}"
    )


def test_order_exceeding_notional_cap_blocked_through_full_loop(live_runtime: Path) -> None:
    """An order whose notional exceeds `hard_caps.max_order_notional_usd`
    must never reach the real broker order call, even though (unlike the
    halt/expiry denials) the gate has already read positions/balance by the
    time this check runs."""
    _write_mandate(live_runtime, max_order_notional_usd=750.0)

    # Comfortably over the 750.0 cap — not a boundary case, so this can't be
    # mistaken for float-rounding noise in the comparison.
    adapter, _guard = _run_order_request(live_runtime, notional_usd=5000.0)

    assert _ORDER_TOOL_REMOTE_NAME not in adapter.calls, (
        f"order exceeding max_order_notional_usd reached the real broker "
        f"call — cap enforcement bypassed. calls={adapter.calls!r}"
    )
    # The read snapshot (positions/balance) DID happen — proves this denial
    # came from the breach check, not an earlier short-circuit (mandate /
    # expiry / halt) that would make this test pass for the wrong reason.
    assert adapter.calls, (
        "expected at least the positions/balance read calls before the "
        "breach check ran; got no calls at all — the gate short-circuited "
        "earlier than the notional-cap check this test targets."
    )
