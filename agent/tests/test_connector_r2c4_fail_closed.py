"""Connector fail-closed fixes from the R2 completion 4 crew (one test per defect)."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from src.trading.connectors.binance import sdk as bn
from src.trading.connectors.etoro import instruments as et_inst
from src.trading.connectors.etoro import trading as et_trading
from src.trading.connectors.futu import sdk as ft
from src.trading.connectors.okx import sdk as ox
from src.trading.connectors.tiger import sdk as tg


# ---- futu: a live-readonly profile never unlocks the real trade context --------------------
def test_futu_live_readonly_never_unlocks() -> None:
    ctx = SimpleNamespace(unlock_trade=lambda **_: pytest.fail("unlock_trade must not be called"))
    err = ft._unlock_if_live(ft.FutuConfig(profile="live-readonly"), ctx, SimpleNamespace())
    assert err and "read-only" in err
    assert ft._unlock_if_live(ft.FutuConfig(profile="paper"), ctx, SimpleNamespace()) is None


# ---- tiger: an order that may have been accepted is never a retryable plain error ----------
def _fake_tiger(monkeypatch, place):
    class Trade:
        def place_order(self, order):
            return place(order)

    contract_utils = types.ModuleType("tigeropen.common.util.contract_utils")
    contract_utils.stock_contract = lambda **kw: object()
    order_utils = types.ModuleType("tigeropen.common.util.order_utils")
    order_utils.limit_order = order_utils.market_order = lambda **kw: SimpleNamespace(id=None, status="", reason=None)
    for name, mod in (
        ("tigeropen", types.ModuleType("tigeropen")),
        ("tigeropen.common", types.ModuleType("tigeropen.common")),
        ("tigeropen.common.util", types.ModuleType("tigeropen.common.util")),
        ("tigeropen.common.util.contract_utils", contract_utils),
        ("tigeropen.common.util.order_utils", order_utils),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(tg, "_trade_client", lambda cfg: Trade())
    monkeypatch.setattr(tg, "_assert_profile", lambda cfg: None)
    return tg.TigerConfig(tiger_id="x", account="DU123", private_key_path="/x", profile="paper")


def test_tiger_place_exception_is_outcome_unknown(monkeypatch) -> None:
    def boom(order):
        raise TimeoutError("read timed out")

    cfg = _fake_tiger(monkeypatch, boom)
    out = tg.place_order(cfg, symbol="AAPL", side="buy", quantity=1)
    assert out["status"] == "error" and out["code"] == "order_outcome_unknown"


def test_tiger_reason_on_accepted_order_is_outcome_unknown(monkeypatch) -> None:
    def accept_with_reason(order):
        order.id, order.reason = 77, "price outside of band"
        return 77

    cfg = _fake_tiger(monkeypatch, accept_with_reason)
    out = tg.place_order(cfg, symbol="AAPL", side="buy", quantity=1)
    assert out["code"] == "order_outcome_unknown" and out["order_id"] == "77"


def test_tiger_definitive_rejection_stays_plain_error(monkeypatch) -> None:
    def reject(order):
        order.id, order.status, order.reason = 78, "REJECTED", "no funds"
        return 78

    cfg = _fake_tiger(monkeypatch, reject)
    out = tg.place_order(cfg, symbol="AAPL", side="buy", quantity=1)
    assert out["status"] == "error" and "code" not in out


def test_tiger_read_is_called_once_and_keeps_account() -> None:
    calls = []

    class Trade:
        def get_assets(self, account=None):
            calls.append(account)
            return []

        def get_bars(self, symbols, period=None, limit=None):
            calls.append(("bars", period))
            raise TypeError("raised inside the SDK")

    assert tg._safe_call(Trade(), "get_assets", account="A1") == [] and calls == ["A1"]
    with pytest.raises(TypeError):  # an SDK-internal TypeError is not retried without arguments
        tg._safe_call(Trade(), "get_bars", ["X"], period="day", limit=1)
    assert calls == ["A1", ("bars", "day")]


def test_tiger_unknown_period_raises(monkeypatch) -> None:
    monkeypatch.setattr(tg, "_quote_client", lambda cfg: object())
    monkeypatch.setattr(tg, "_assert_profile", lambda cfg: None)
    cfg = tg.TigerConfig(tiger_id="x", account="DU1", private_key_path="/x", profile="paper")
    with pytest.raises(ValueError, match="unsupported Tiger period"):
        tg.get_historical_bars("AAPL", config=cfg, period="4h")


# ---- okx: the pinned UID is verified at place time ------------------------------------------
def _okx_cfg(**kw):
    return ox.OKXConfig(api_key="k", api_secret="s", passphrase="p", profile="paper", **kw)


def test_okx_place_refuses_on_uid_mismatch_or_unreadable(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(ox, "_trade_client", lambda cfg: sent.append("client") or SimpleNamespace())
    account = SimpleNamespace(get_account_config=lambda: {"code": "0", "data": [{"uid": "999"}]})
    monkeypatch.setattr(ox, "_account_client", lambda cfg: account)
    out = ox.place_order(_okx_cfg(expected_uid="123"), symbol="BTC-USDT", side="buy", quantity=1)
    assert out["status"] == "error" and "UID mismatch" in out["error"] and sent == []

    def down():
        raise ConnectionError("no route")

    monkeypatch.setattr(ox, "_account_client", lambda cfg: SimpleNamespace(get_account_config=down))
    out = ox.place_order(_okx_cfg(expected_uid="123"), symbol="BTC-USDT", side="buy", quantity=1)
    assert out["status"] == "error" and "could not be verified" in out["error"] and sent == []
    out = ox.cancel_order(_okx_cfg(expected_uid="123"), "1", symbol="BTC-USDT")
    assert out["status"] == "error" and sent == []


def test_okx_place_proceeds_when_uid_matches(monkeypatch) -> None:
    trade = SimpleNamespace(place_order=lambda **kw: {"code": "0", "data": [{"sCode": "0", "ordId": "9"}]})
    monkeypatch.setattr(ox, "_trade_client", lambda cfg: trade)
    account = SimpleNamespace(get_account_config=lambda: {"code": "0", "data": [{"uid": "123"}]})
    monkeypatch.setattr(ox, "_account_client", lambda cfg: account)
    out = ox.place_order(_okx_cfg(expected_uid="123"), symbol="BTC-USDT", side="buy", quantity=1)
    assert out["status"] == "ok" and out["order_id"] == "9"


# ---- etoro: a limit needs its price; a trade resolves only an exact symbol -------------------
@pytest.mark.parametrize("bad", [None, "x", 0, -5, float("inf")])
def test_etoro_limit_without_valid_price_is_refused(bad) -> None:
    cfg = SimpleNamespace(profile="paper", environment="paper")
    out = et_trading.place_order(cfg, symbol="1001", side="buy", quantity=1, order_type="limit", limit_price=bad)
    assert out["status"] == "error" and "limit" in out["error"]


def test_etoro_resolve_rejects_substring_match(monkeypatch) -> None:
    rows = [{"internalSymbolFull": "BTCUSDT-PERP", "instrumentId": 5, "instrumentDisplayName": "Other"}]
    monkeypatch.setattr(et_inst, "_search_by_symbol", lambda cfg, sym, limit: rows)
    monkeypatch.setattr(et_inst, "_search_by_text", lambda cfg, q, limit: rows)
    with pytest.raises(Exception, match="instrument not found"):
        et_inst.resolve_instrument_id("BTC", SimpleNamespace())


# ---- binance: the hosts the client really calls are checked on every request path ------------
def test_binance_checks_the_real_client_endpoints() -> None:
    live = SimpleNamespace(urls={"api": {"public": "https://api.binance.com/api/v3", "x": {"y": "https://api1.binance.com"}}})
    testnet = SimpleNamespace(urls={"api": {"public": "https://testnet.binance.vision/api"}})
    live_cfg = bn.BinanceConfig(api_key="k", api_secret="s", profile="live-readonly")
    paper_cfg = bn.BinanceConfig(api_key="k", api_secret="s", profile="paper")
    bn._assert_exchange_hosts(live_cfg, live)
    bn._assert_exchange_hosts(paper_cfg, testnet)
    with pytest.raises(bn.BinanceConfigError):  # paper profile, client wired to the live host
        bn._assert_exchange_hosts(paper_cfg, live)
    with pytest.raises(bn.BinanceConfigError):  # live profile, client wired to a testnet/other host
        bn._assert_exchange_hosts(live_cfg, testnet)
    with pytest.raises(bn.BinanceConfigError):  # no endpoints to verify
        bn._assert_exchange_hosts(live_cfg, SimpleNamespace(urls={}))


# ---- live gate: an order is not sent when its audit ledger cannot be written ------------------
def test_order_not_sent_when_audit_ledger_unwritable(monkeypatch) -> None:
    from src.live import sdk_order_gate as gate

    def unwritable():
        raise OSError("read-only file system")

    monkeypatch.setattr(gate, "assert_audit_writable", unwritable)
    connector = SimpleNamespace(place_order=lambda *a, **k: pytest.fail("order must not be sent"))
    out = gate._allow("okx", "s1", connector, object(), None, {"symbol": "X"}, None, None)
    assert out["status"] == "blocked" and out["reason_code"] == "audit_ledger_unavailable"
    risk_increasing = gate._execute_and_audit_live_action(
        broker="okx", session_id="s1", remote_tool="t", mandate=None, intent=None,
        execute_fn=lambda: pytest.fail("action must not run"), audit_request=None, checked=[],
        consume_daily_count=True,
    )
    assert risk_increasing["reason_code"] == "audit_ledger_unavailable"
    # a risk-reducing action (closing a position) stays possible
    reducing = gate._execute_and_audit_live_action(
        broker="okx", session_id="s1", remote_tool="t", mandate=None, intent=None,
        execute_fn=lambda: {"status": "ok"}, audit_request=None, checked=[], consume_daily_count=False,
    )
    assert reducing["status"] == "ok"
