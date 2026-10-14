"""L6 — real Robinhood catalog + quantity→quote pricing (SPEC.md §4, §7.5).

Covers:

* The frozen canonical Robinhood read/write catalog.
* The extractor's finalized ``place_equity_order`` field mapping (symbol/side/size,
  unknown keys ignored, ambiguity → ``None``).
* Quantity-only quote derivation: broker ``get_equity_quotes`` preferred, data-loader
  fallback, fail-closed DENY when no quote is obtainable. Never hits the network
  (the loader path is stubbed).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import src.live.enforcement as enforcement
import src.live.order_guard as order_guard
import src.live.paths as paths
from src.live.classification import ToolClass
from src.trading.connectors.robinhood.extractor import extract_order_intent
from src.live.mandate.model import (
    MANDATE_SCHEMA_VERSION,
    AssetClass,
    InstrumentType,
)
from src.trading.connectors.robinhood.classification import ROBINHOOD_TOOL_CLASS
from src.tools.mcp import MCPRemoteToolSpec


# --------------------------------------------------------------------------- #
# C1 / L6 — frozen canonical catalog                                          #
# --------------------------------------------------------------------------- #

_CANONICAL_READ = {
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_equity_quotes",
    "get_equity_orders",
}
_CANONICAL_WRITE = {"place_equity_order", "cancel_equity_order"}


def test_catalog_is_exactly_the_canonical_set() -> None:
    reads = {n for n, c in ROBINHOOD_TOOL_CLASS.items() if c is ToolClass.READ}
    writes = {n for n, c in ROBINHOOD_TOOL_CLASS.items() if c is ToolClass.WRITE}
    assert reads == _CANONICAL_READ
    assert writes == _CANONICAL_WRITE
    # No stale names beyond the frozen catalog.
    assert set(ROBINHOOD_TOOL_CLASS) == _CANONICAL_READ | _CANONICAL_WRITE


# --------------------------------------------------------------------------- #
# L6 — extractor field mapping finalized + defensive                          #
# --------------------------------------------------------------------------- #


def test_extractor_maps_notional_order() -> None:
    intent = extract_order_intent(
        "place_equity_order",
        {"symbol": "aapl", "side": "buy", "instrument_type": "stock", "notional_usd": 250.0},
    )
    assert intent is not None
    assert intent.symbol == "AAPL"
    assert intent.side == "buy"
    assert intent.notional_usd == 250.0
    assert intent.quantity is None
    assert intent.instrument_type is InstrumentType.EQUITY


def test_extractor_maps_quantity_and_dollar_amount_alias() -> None:
    intent = extract_order_intent(
        "place_equity_order",
        {"ticker": "NVDA", "action": "sell", "type": "equity", "quantity": 3, "dollar_amount": 600},
    )
    assert intent is not None
    assert intent.symbol == "NVDA"
    assert intent.side == "sell"
    assert intent.quantity == 3.0
    # dollar_amount is accepted as a notional alias.
    assert intent.notional_usd == 600.0


def test_extractor_ignores_unknown_extra_keys() -> None:
    intent = extract_order_intent(
        "place_equity_order",
        {
            "symbol": "MSFT", "side": "buy", "instrument_type": "equity",
            "quantity": 1, "time_in_force": "gtc", "client_tag": "x", "extended_hours": True,
        },
    )
    assert intent is not None
    assert intent.symbol == "MSFT"
    assert intent.quantity == 1.0


def test_extractor_rejects_non_order_tool() -> None:
    assert extract_order_intent("cancel_equity_order", {"order_id": "x"}) is None
    assert extract_order_intent("get_equity_quotes", {"symbol": "AAPL"}) is None


def test_extractor_rejects_missing_or_ambiguous_fields() -> None:
    # Missing side.
    assert extract_order_intent("place_equity_order", {"symbol": "AAPL", "instrument_type": "equity", "notional_usd": 10}) is None
    # Unknown instrument.
    assert extract_order_intent("place_equity_order", {"symbol": "AAPL", "side": "buy", "instrument_type": "warrant", "notional_usd": 10}) is None
    # No size at all.
    assert extract_order_intent("place_equity_order", {"symbol": "AAPL", "side": "buy", "instrument_type": "equity"}) is None


# --------------------------------------------------------------------------- #
# L6 — quantity-only quote derivation through the gate                         #
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


def _spec() -> MCPRemoteToolSpec:
    return MCPRemoteToolSpec(
        server_name="robinhood",
        remote_name="place_equity_order",
        local_name="mcp_robinhood_place_equity_order",
        description="Place an order.",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
    )


def _write_mandate(live_runtime: Path, *, max_order_notional_usd: float = 750.0) -> None:
    broker = live_runtime / "live" / "robinhood"
    broker.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    payload = {
        "schema_version": MANDATE_SCHEMA_VERSION,
        "hard_caps": {
            "account_funding_usd": 100000.0,
            "max_order_notional_usd": max_order_notional_usd,
            "max_total_exposure_usd": 1e9,
            "max_leverage": 100.0,
            "allowed_instruments": ["equity", "etf"],
            "max_trades_per_day": 50,
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


class _BrokerQuoteAdapter:
    """Adapter whose ``get_equity_quotes`` returns a price; loader path is never needed."""

    def __init__(self, *, price: float) -> None:
        self.server_name = "robinhood"
        self._price = price
        self.order_calls: list[dict[str, Any]] = []
        self.quote_calls = 0

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        if remote_name == "get_equity_positions":
            return {"positions": [], "status": "ok"}
        if remote_name == "get_portfolio":
            return {"equity": 100000.0, "status": "ok"}
        if remote_name == "get_equity_quotes":
            self.quote_calls += 1
            return {"status": "ok", "results": [{"symbol": arguments.get("symbol"), "last_price": self._price}]}
        self.order_calls.append({"remote": remote_name, "arguments": arguments})
        return {"status": "ok", "order_id": "rh_q", "state": "accepted"}


class _NoBrokerQuoteAdapter:
    """Adapter whose ``get_equity_quotes`` errors — forces the data-loader fallback."""

    def __init__(self) -> None:
        self.server_name = "robinhood"
        self.order_calls: list[dict[str, Any]] = []

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        if remote_name == "get_equity_positions":
            return {"positions": [], "status": "ok"}
        if remote_name == "get_portfolio":
            return {"equity": 100000.0, "status": "ok"}
        if remote_name == "get_equity_quotes":
            return {"status": "error", "error": "quotes unavailable"}
        self.order_calls.append({"remote": remote_name, "arguments": arguments})
        return {"status": "ok", "order_id": "rh_q", "state": "accepted"}


def _guard(adapter):
    return order_guard.LiveOrderGuardTool(adapter, _spec(), broker="robinhood", session_id="s1")


def test_quantity_only_uses_broker_quote_and_enforces_notional(live_runtime: Path) -> None:
    _write_mandate(live_runtime, max_order_notional_usd=750.0)
    adapter = _BrokerQuoteAdapter(price=100.0)  # 10 * 100 = 1000 > 750
    guard = _guard(adapter)
    out = json.loads(
        guard.execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0)
    )
    assert adapter.quote_calls == 1
    assert out["status"] == "blocked"
    assert out["breach"]["limit"] == "max_order_notional_usd"
    assert out["breach"]["attempted_value"] == 1000.0
    assert adapter.order_calls == []


def test_quantity_only_in_mandate_forwards(live_runtime: Path) -> None:
    _write_mandate(live_runtime, max_order_notional_usd=2000.0)
    adapter = _BrokerQuoteAdapter(price=100.0)  # 10 * 100 = 1000 <= 2000
    guard = _guard(adapter)
    out = json.loads(
        guard.execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0)
    )
    assert out.get("status") == "ok"
    assert len(adapter.order_calls) == 1


def test_quantity_falls_back_to_data_loader(live_runtime: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the broker quote tool errors, the gate derives price from the data
    loaders (stubbed — no network)."""
    _write_mandate(live_runtime, max_order_notional_usd=750.0)
    monkeypatch.setattr(order_guard, "last_price_usd", lambda sym, ac: 100.0)
    adapter = _NoBrokerQuoteAdapter()
    guard = _guard(adapter)
    out = json.loads(
        guard.execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0)
    )
    assert out["status"] == "blocked"
    assert out["breach"]["limit"] == "max_order_notional_usd"
    assert adapter.order_calls == []


def test_quantity_no_quote_anywhere_denies_fail_closed(live_runtime: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quantity order + broker quote errors + loader returns nothing → DENY,
    never waved through."""
    _write_mandate(live_runtime)
    monkeypatch.setattr(order_guard, "last_price_usd", lambda sym, ac: None)
    adapter = _NoBrokerQuoteAdapter()
    guard = _guard(adapter)
    out = json.loads(
        guard.execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0)
    )
    assert out["status"] == "blocked"
    assert out["decision"] == "deny"
    assert "priced" in out["reason"].lower()
    assert adapter.order_calls == []


def test_last_price_usd_fail_closed_on_loader_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loader-backed price helper denies (returns None) when no loader is
    available — never a network call in tests."""
    def _raise(_ac):
        raise enforcement.UniverseDataUnavailable("no loader")

    monkeypatch.setattr(enforcement, "_resolve_loader", _raise)
    assert enforcement.last_price_usd("AAPL", AssetClass.US_EQUITY) is None


# --------------------------------------------------------------------------- #
# Normalization must not drop security-relevant intent fields                  #
# --------------------------------------------------------------------------- #


def test_normalization_preserves_asset_class(live_runtime: Path) -> None:
    """``_normalize_intent_notional`` rebuilds the intent; asset_class must survive.

    ``check_mandate`` prefers an explicit ``intent.asset_class`` over the
    instrument-type default (enforcement.py:524) precisely so a multi-market
    connector's HK/A-share order buckets correctly. If the rebuild drops it, an
    HK order falls back to the EQUITY default (us_equity) and passes a mandate
    that permits only us_equity — a fail-open on the universe check.
    """
    _write_mandate(live_runtime, max_order_notional_usd=10_000.0)
    guard = _guard(_BrokerQuoteAdapter(price=100.0))
    intent = enforcement.OrderIntent(
        symbol="0700.HK",
        side="buy",
        notional_usd=None,
        quantity=10.0,
        instrument_type=InstrumentType.EQUITY,
        asset_class=AssetClass.HK_EQUITY,
    )
    normalized = guard._normalize_intent_notional(intent)
    assert normalized is not None
    assert normalized.asset_class is AssetClass.HK_EQUITY
    # And the notional reconciliation it exists for still happened.
    assert normalized.notional_usd == pytest.approx(1000.0)


def test_normalization_preserves_a_none_asset_class(live_runtime: Path) -> None:
    """The Robinhood extractor never sets one; None must stay None, not crash."""
    _write_mandate(live_runtime, max_order_notional_usd=10_000.0)
    guard = _guard(_BrokerQuoteAdapter(price=100.0))
    intent = enforcement.OrderIntent(
        symbol="AAPL",
        side="buy",
        notional_usd=None,
        quantity=10.0,
        instrument_type=InstrumentType.EQUITY,
    )
    normalized = guard._normalize_intent_notional(intent)
    assert normalized is not None
    assert normalized.asset_class is None


# --------------------------------------------------------------------------- #
# R9 — real adapter envelope, limit-price sizing, pre-send audit, lock scope   #
# --------------------------------------------------------------------------- #


class _EnvelopeAdapter(_BrokerQuoteAdapter):
    """Reads come back the way ``MCPServerAdapter.call_tool`` really returns them: nested under ``data``."""

    def __init__(self, *, price: float, positions_data: Any = None) -> None:
        super().__init__(price=price)
        self._positions_data = {"positions": []} if positions_data is None else positions_data
        self.events: list[str] = []

    def call_tool(self, remote_name: str, arguments: dict, *, local_name: str | None = None) -> dict:
        self.events.append(remote_name)
        if remote_name == "get_equity_positions":
            return {"status": "ok", "data": self._positions_data, "server": "robinhood", "tool": remote_name}
        if remote_name == "get_equity_quotes":
            return {"status": "ok", "data": {"quotes": [{"symbol": arguments.get("symbol"), "last_price": self._price}]}}
        return super().call_tool(remote_name, arguments, local_name=local_name)


def test_real_adapter_envelope_positions_are_parsed(live_runtime: Path) -> None:
    _write_mandate(live_runtime, max_order_notional_usd=2000.0)
    adapter = _EnvelopeAdapter(price=100.0)
    out = json.loads(_guard(adapter).execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0))
    assert out.get("status") == "ok", out
    assert len(adapter.order_calls) == 1


def test_enveloped_positions_without_records_still_fail_closed(live_runtime: Path) -> None:
    _write_mandate(live_runtime, max_order_notional_usd=2000.0)
    adapter = _EnvelopeAdapter(price=100.0, positions_data={"message": "ok but no records"})
    out = json.loads(_guard(adapter).execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0))
    assert out["status"] == "blocked"
    assert adapter.order_calls == []


def test_extractor_reads_limit_price_and_denies_a_malformed_one() -> None:
    base = {"symbol": "AAPL", "side": "buy", "instrument_type": "equity", "quantity": 10}
    assert extract_order_intent("place_equity_order", {**base, "limit_price": 200}).limit_price == 200.0
    assert extract_order_intent("place_equity_order", {**base, "limit_price": 150, "price": 400}).limit_price == 400.0
    assert extract_order_intent("place_equity_order", {**base, "limit_price": None}).limit_price is None
    assert extract_order_intent("place_equity_order", base).limit_price is None
    for bad in ("abc", -1, 0, float("nan")):
        assert extract_order_intent("place_equity_order", {**base, "limit_price": bad}) is None


def test_buy_limit_quantity_order_is_sized_at_the_limit(live_runtime: Path) -> None:
    _write_mandate(live_runtime, max_order_notional_usd=1500.0)
    adapter = _BrokerQuoteAdapter(price=100.0)
    out = json.loads(
        _guard(adapter).execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0, limit_price=200.0)
    )
    assert out["status"] == "blocked" and out["breach"]["limit"] == "max_order_notional_usd"
    assert out["breach"]["attempted_value"] == 2000.0
    assert adapter.order_calls == []
    # a sell limit does not create exposure: the quote stands (1000 <= 1500) and it forwards
    sell = json.loads(
        _guard(adapter).execute(symbol="AAPL", side="sell", instrument_type="equity", quantity=10.0, limit_price=200.0)
    )
    assert sell.get("status") == "ok"


def test_order_not_sent_when_audit_ledger_unwritable(live_runtime: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.live.audit as audit_mod

    _write_mandate(live_runtime, max_order_notional_usd=2000.0)

    def unwritable() -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(audit_mod, "assert_audit_writable", unwritable)
    adapter = _BrokerQuoteAdapter(price=100.0)
    out = json.loads(_guard(adapter).execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0))
    assert out["status"] == "blocked" and "audit ledger" in out["reason"]
    assert adapter.order_calls == []


def test_count_persist_failure_after_an_accepted_order_does_not_raise(
    live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.live.daily_count import DailyCountError

    _write_mandate(live_runtime, max_order_notional_usd=2000.0)

    def boom(broker: str, action_id: str | None = None) -> int:
        raise DailyCountError("daily order count could not be persisted")

    monkeypatch.setattr(order_guard, "increment_daily_count", boom)
    adapter = _BrokerQuoteAdapter(price=100.0)
    out = json.loads(_guard(adapter).execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0))
    assert out.get("status") == "ok"  # the broker accepted it; raising would have the caller re-send
    assert len(adapter.order_calls) == 1
    assert out["live_action"]["gate_decision"]["reason_code"] == "daily_action_accounting_failed"


def test_positions_are_read_while_the_order_lock_is_held(live_runtime: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    _write_mandate(live_runtime, max_order_notional_usd=2000.0)
    adapter = _EnvelopeAdapter(price=100.0)
    real_lock = order_guard.daily_order_lock

    @contextmanager
    def recording_lock(broker: str):
        adapter.events.append("lock+")
        with real_lock(broker):
            yield
        adapter.events.append("lock-")

    monkeypatch.setattr(order_guard, "daily_order_lock", recording_lock)
    out = json.loads(_guard(adapter).execute(symbol="AAPL", side="buy", instrument_type="equity", quantity=10.0))
    assert out.get("status") == "ok"
    events = adapter.events
    assert events.index("lock+") < events.index("get_equity_positions") < events.index("lock-")
