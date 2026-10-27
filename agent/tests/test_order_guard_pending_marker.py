"""The MCP order guard writes the same ownership marker as the direct-SDK gate, before it forwards.

A timeout after the broker accepted the order used to be audited `order_rejected` with no daily count spent,
so the agent could resend it. Now an ambiguous failure leaves a marker, spends the count and refuses a repeat
until it is recovered. Hermetic: a stub adapter stands in for the broker; nothing reaches a live broker.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.live.paths as paths
from src.live import pending_action
from src.live.daily_count import read_daily_count
from src.live.mandate.model import MANDATE_SCHEMA_VERSION
from src.live.order_guard import LiveOrderGuardTool
from src.tools.mcp import MCPRemoteToolSpec

_ORDER = {"symbol": "AAPL", "side": "buy", "instrument_type": "equity", "notional_usd": 100.0}


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    broker = tmp_path / "live" / "robinhood"
    broker.mkdir(parents=True)
    created = datetime.now(timezone.utc)
    (broker / "mandate.json").write_text(json.dumps({
        "schema_version": MANDATE_SCHEMA_VERSION,
        "hard_caps": {"account_funding_usd": 5000.0, "max_order_notional_usd": 750.0,
                      "max_total_exposure_usd": 5000.0, "max_leverage": 1.0,
                      "allowed_instruments": ["equity", "etf"], "max_trades_per_day": 5},
        "universe": {"asset_classes": ["us_equity", "us_etf"], "min_market_cap_usd": None,
                     "min_avg_daily_volume_usd": None, "exclude_symbols": []},
        "consent": {"created_at": created.isoformat(), "consent_token_sha256": "deadbeef",
                    "broker": "robinhood", "account_ref": "acct_ref",
                    "expires_at": (created + timedelta(days=30)).isoformat()},
    }), encoding="utf-8")
    return tmp_path


class _Adapter:
    server_name = "robinhood"

    def __init__(self, order_reply: dict) -> None:
        self.order_reply = order_reply
        self.orders = 0

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        if remote_name == "place_equity_order":
            self.orders += 1
            return self.order_reply
        return {"status": "ok", "positions": [], "buying_power": 5000.0, "equity": 5000.0}


def _guard(order_reply: dict) -> tuple[LiveOrderGuardTool, _Adapter]:
    adapter = _Adapter(order_reply)
    spec = MCPRemoteToolSpec(
        server_name="robinhood", remote_name="place_equity_order", local_name="mcp_robinhood_place_equity_order",
        description="Place an order.", parameters={"type": "object", "properties": {}, "additionalProperties": True},
    )
    return LiveOrderGuardTool(adapter, spec, broker="robinhood", session_id="s1"), adapter


def _kind(out: str) -> str | None:
    return (json.loads(out).get("live_action") or {}).get("kind")


def test_timeout_after_send_is_unresolved_counted_and_blocks_a_repeat(runtime: Path) -> None:
    guard, adapter = _guard({"status": "error", "error": "timeout"})
    out = guard.execute(**_ORDER)
    assert _kind(out) == "order_unresolved"
    assert pending_action.load_pending_action("robinhood") is not None
    assert read_daily_count("robinhood") == 1
    repeat = guard.execute(**_ORDER)
    assert json.loads(repeat)["status"] == "blocked"
    assert adapter.orders == 1  # the second identical intent never reached the broker


def test_definite_rejection_clears_the_marker_and_spends_nothing(runtime: Path) -> None:
    guard, _ = _guard({"status": "error", "error": "insufficient buying power"})
    assert _kind(guard.execute(**_ORDER)) == "order_rejected"
    assert pending_action.load_pending_action("robinhood") is None
    assert read_daily_count("robinhood") == 0


def test_accepted_order_counts_once_and_clears_the_marker(runtime: Path) -> None:
    guard, adapter = _guard({"status": "ok", "order_id": "rh_1"})
    assert _kind(guard.execute(**_ORDER)) == "order_placed"
    assert pending_action.load_pending_action("robinhood") is None
    assert read_daily_count("robinhood") == 1
    assert _kind(guard.execute(**_ORDER)) == "order_placed"  # a clean first order does not block the next
    assert adapter.orders == 2
