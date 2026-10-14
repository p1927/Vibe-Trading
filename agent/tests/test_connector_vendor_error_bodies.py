"""A vendor error body is an error, never "ok" with empty data (CEO decision 2026-10-14).

Each case feeds a recorded failure reply of the vendor to the connector and asserts a typed raise:
read as empty, it reads as a flat account to a caller and to the mandate gate.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.trading.connectors.dhan import sdk as dhan
from src.trading.connectors.etoro import sdk as etoro
from src.trading.connectors.etoro.client import EtoroAPIError
from src.trading.connectors.futu import sdk as futu
from src.trading.connectors.mt5 import _client as mt5_client
from src.trading.connectors.okx import sdk as okx
from src.trading.connectors.shoonya import sdk as shoonya
from src.trading.types import ConnectorVendorError

pytestmark = pytest.mark.unit

# ---- recorded error bodies (shapes taken from each vendor's documented failure reply)
NOREN_SESSION = {"stat": "Not_Ok", "emsg": "Session Expired : Invalid Session Key", "request_time": "10:01:02 14-10-2026"}
NOREN_EMPTY = {"stat": "Not_Ok", "emsg": "Error Occurred : 5 : \"no data\""}
DHAN_FAILURE = {"status": "failure", "remarks": {"error_code": "DH-901", "error_type": "Invalid_Authentication"}, "data": {}}
OKX_ERROR = {"code": "50111", "msg": "Invalid OK-ACCESS-KEY", "data": []}


def test_shoonya_error_body_raises_but_no_data_is_empty():
    with pytest.raises(ConnectorVendorError, match="Session Expired"):
        shoonya._as_list(NOREN_SESSION)
    assert shoonya._as_list(NOREN_EMPTY) == []
    assert shoonya._as_list(None) == []
    assert shoonya._as_list([{"tsym": "X"}]) == [{"tsym": "X"}]


def test_shoonya_positions_and_limits_raise_on_error_body(monkeypatch):
    api = SimpleNamespace(get_positions=lambda: NOREN_SESSION, get_limits=lambda: NOREN_SESSION)
    monkeypatch.setattr(shoonya, "_login", lambda cfg: api)
    cfg = shoonya.ShoonyaConfig()
    with pytest.raises(ConnectorVendorError):
        shoonya.get_positions(cfg)
    with pytest.raises(ConnectorVendorError):
        shoonya.get_account_snapshot(cfg)


def test_dhan_failure_body_raises_on_every_read(monkeypatch):
    client = SimpleNamespace(
        get_positions=lambda: DHAN_FAILURE,
        get_order_list=lambda: DHAN_FAILURE,
        get_fund_limits=lambda: DHAN_FAILURE,
    )
    monkeypatch.setattr(dhan, "_dhan_client", lambda cfg: client)
    cfg = dhan.DhanConfig()
    for read in (dhan.get_positions, dhan.get_open_orders, dhan.get_account_snapshot):
        with pytest.raises(ConnectorVendorError, match="DH-901"):
            read(cfg)
    with pytest.raises(ConnectorVendorError):
        dhan._checked("<html>502</html>", "x")


def test_dhan_position_pnl_fields_are_not_swapped(monkeypatch):
    row = {"tradingSymbol": "X", "unrealizedProfit": 12.5, "realizedProfit": -3.0}
    client = SimpleNamespace(get_positions=lambda: {"status": "success", "data": [row]})
    monkeypatch.setattr(dhan, "_dhan_client", lambda cfg: client)
    out = dhan.get_positions(dhan.DhanConfig())["positions"][0]
    assert out["unrealized_pnl"] == 12.5 and out["realized_pnl"] == -3.0


def test_futu_non_ok_return_code_raises(monkeypatch):
    monkeypatch.setattr(futu, "_require_futu", lambda: SimpleNamespace(RET_OK=0))
    with pytest.raises(ConnectorVendorError, match="ret=-1"):
        futu._unwrap((-1, "Nonexistent account"))
    assert futu._unwrap((0, [1])) == [1]


def test_okx_error_code_and_missing_data_raise():
    with pytest.raises(ConnectorVendorError, match="50111"):
        okx._extract_data(OKX_ERROR)
    with pytest.raises(ConnectorVendorError):
        okx._extract_data({"code": "0"})
    assert okx._extract_data({"code": "0", "data": []}) == []


def test_mt5_none_is_an_error_unless_last_error_says_success():
    lost = SimpleNamespace(last_error=lambda: (-10004, "No IPC connection"))
    with pytest.raises(mt5_client.MT5ConnectionError, match="No IPC"):
        mt5_client._rows(lost, None, "positions_get")
    quiet = SimpleNamespace(last_error=lambda: (1, "Success"))
    assert mt5_client._rows(quiet, None, "positions_get") == ()
    assert mt5_client._rows(quiet, [1, 2], "positions_get") == (1, 2)
    broken = SimpleNamespace(last_error=lambda: (_ for _ in ()).throw(RuntimeError("dead")))
    with pytest.raises(mt5_client.MT5ConnectionError):
        mt5_client._rows(broken, None, "orders_get")


def test_etoro_unrecognised_portfolio_reply_is_an_error():
    error_body = {"errorCode": "Unauthorized", "errorMessage": "Invalid keys"}
    with pytest.raises(EtoroAPIError):
        etoro._extract_positions(error_body)
    with pytest.raises(EtoroAPIError):
        etoro._extract_portfolio_orders(error_body)
    assert etoro._extract_positions({"clientPortfolio": {"credit": 100.0}}) == []
    assert etoro._extract_positions({"positions": []}) == []
