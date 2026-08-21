"""stock_simulator loader: recorded-data adaptation + coverage/availability gating.

Tests inject a fake ``StockHistory``-like object via ``_ensure_stock_history`` so
no real ``trade_integrations`` cross-package import is needed — mirrors
``test_india_broker_loader.py``'s fake-SDK pattern.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.loaders import stock_simulator_loader as mod
from backtest.loaders.stock_simulator_loader import DataLoader, _resolve_symbol


class _FakeBar:
    def __init__(self, ts_ist, trading_day, open_, high, low, close, volume):
        self.ts_ist = pd.Timestamp(ts_ist)
        self.trading_day = trading_day
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


class _FakeStockHistory:
    def __init__(self, bars_by_symbol=None, recorded_days_by_symbol=None, chain=None):
        self._bars = bars_by_symbol or {}
        self._recorded = recorded_days_by_symbol or {}
        self._chain = chain
        self.calls: list[dict] = []

    def index_history(self, *, symbol, exchange, since_ist, until_ist):
        self.calls.append({"symbol": symbol, "exchange": exchange})
        return self._bars.get(symbol, [])

    def recorded_index_days(self, *, symbol, exchange):
        return self._recorded.get(symbol, [])

    def option_chain_at(self, **kwargs):
        return self._chain


def test_symbol_resolution() -> None:
    assert _resolve_symbol("RELIANCE.NS") == ("RELIANCE", "NSE")
    assert _resolve_symbol("500325.BO") == ("500325", "BSE")
    assert _resolve_symbol("NIFTY") == ("NIFTY", "NSE_INDEX")
    assert _resolve_symbol("BANKNIFTY") == ("BANKNIFTY", "NSE_INDEX")
    assert _resolve_symbol("SENSEX") == ("SENSEX", "BSE_INDEX")


def test_unavailable_when_no_stock_history(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_ensure_stock_history", lambda: None)
    loader = DataLoader()
    assert loader.is_available() is False
    assert loader.fetch(["RELIANCE.NS"], "2024-04-01", "2024-04-02") == {}


def test_fetch_aggregates_1min_bars_into_daily_ohlcv(monkeypatch) -> None:
    bars = [
        _FakeBar("2024-04-01T09:15:00", "2024-04-01", 100, 101, 99, 100.2, 100),
        _FakeBar("2024-04-01T09:16:00", "2024-04-01", 100.2, 100.5, 100, 100.3, 50),
        _FakeBar("2024-04-02T09:15:00", "2024-04-02", 105, 106, 104, 105.5, 200),
    ]
    fake = _FakeStockHistory(
        bars_by_symbol={"RELIANCE": bars},
        recorded_days_by_symbol={"RELIANCE": ["2024-04-01", "2024-04-02"]},
    )
    monkeypatch.setattr(mod, "_ensure_stock_history", lambda: fake)
    loader = DataLoader()
    assert loader.is_available() is True

    out = loader.fetch(["RELIANCE.NS"], "2024-04-01", "2024-04-02")
    assert "RELIANCE.NS" in out
    df = out["RELIANCE.NS"]
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "trade_date"
    assert len(df) == 2

    day1 = df.loc["2024-04-01"]
    assert day1["open"] == 100
    assert day1["high"] == 101
    assert day1["low"] == 99
    assert day1["close"] == 100.3
    assert day1["volume"] == 150

    day2 = df.loc["2024-04-02"]
    assert day2["open"] == 105
    assert day2["close"] == 105.5

    assert fake.calls[0]["symbol"] == "RELIANCE"
    assert fake.calls[0]["exchange"] == "NSE"


def test_partial_coverage_omits_symbol(monkeypatch) -> None:
    """2024-04-01/02 are both business days; only one is recorded -> omit."""
    bars = [_FakeBar("2024-04-01T09:15:00", "2024-04-01", 100, 101, 99, 100, 10)]
    fake = _FakeStockHistory(
        bars_by_symbol={"RELIANCE": bars},
        recorded_days_by_symbol={"RELIANCE": ["2024-04-01"]},
    )
    monkeypatch.setattr(mod, "_ensure_stock_history", lambda: fake)
    loader = DataLoader()

    out = loader.fetch(["RELIANCE.NS"], "2024-04-01", "2024-04-02")
    assert out == {}


def test_unsupported_interval_does_not_silently_fetch_daily(monkeypatch) -> None:
    fake = _FakeStockHistory()
    monkeypatch.setattr(mod, "_ensure_stock_history", lambda: fake)
    loader = DataLoader()
    assert loader.fetch(["RELIANCE.NS"], "2024-04-01", "2024-04-02", interval="4H") == {}
    assert fake.calls == []


def test_fetch_option_chain_reshapes_flat_legs_to_nested() -> None:
    raw_chain = {
        "underlying": "NIFTY",
        "underlying_ltp": 22500.0,
        "expiry_date": "2024-04-25",
        "total_call_oi": 1000,
        "total_put_oi": 500,
        "source": "hf_replay",
        "chain": [
            {
                "strike": 22400.0,
                "ce_ltp": 150.0, "pe_ltp": 40.0,
                "ce_oi": 600, "pe_oi": 300,
                "ce_iv": 18.5, "pe_iv": 17.2,
                "ce_delta": 0.6, "pe_delta": -0.4,
                "ce_gamma": 0.001, "pe_gamma": 0.001,
                "ce_theta": -5.0, "pe_theta": -4.0,
                "ce_vega": 10.0, "pe_vega": 9.5,
            },
            {
                "strike": 22600.0,
                "ce_ltp": 60.0, "pe_ltp": 120.0,
                "ce_oi": 400, "pe_oi": 200,
                "ce_iv": 17.0, "pe_iv": 18.0,
                "ce_delta": 0.4, "pe_delta": -0.6,
                "ce_gamma": 0.001, "pe_gamma": 0.001,
                "ce_theta": -4.5, "pe_theta": -4.8,
                "ce_vega": 9.0, "pe_vega": 9.8,
            },
        ],
    }
    fake = _FakeStockHistory(chain=raw_chain)

    import backtest.loaders.stock_simulator_loader as loader_mod
    orig = loader_mod._ensure_stock_history
    loader_mod._ensure_stock_history = lambda: fake
    try:
        result = loader_mod.fetch_option_chain(
            underlying="NIFTY", exchange="NSE_INDEX", spot=22510.0, sim_now=None,
        )
    finally:
        loader_mod._ensure_stock_history = orig

    assert result is not None
    assert result["atm_strike"] == 22400.0 or result["atm_strike"] == 22600.0
    # 22510 is closer to 22400 (110 away) than 22600 (90 away) -> nearest is 22600
    assert result["atm_strike"] == 22600.0
    assert result["pcr"] == pytest.approx(500 / 1000)
    leg = result["chain"][0]
    assert set(leg.keys()) == {"strike", "ce", "pe"}
    assert leg["ce"]["ltp"] == 150.0
    assert leg["ce"]["iv"] == 18.5
    assert leg["ce"]["delta"] == 0.6
    assert leg["pe"]["oi"] == 300
