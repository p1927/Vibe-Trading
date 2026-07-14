"""Causality and ordering regressions for the shared execution loop."""

from __future__ import annotations

import pandas as pd

from backtest.engines.base import BaseEngine
from backtest.engines.crypto import CryptoEngine


class _FrictionlessEngine(BaseEngine):
    def can_execute(self, symbol, direction, bar):
        return True

    def round_size(self, raw_size, price):
        return raw_size

    def calc_commission(self, size, price, direction, is_open):
        return 0.0

    def apply_slippage(self, price, direction):
        return price


def _rotation_run(*, last_close_a: float = 100.0, code_order=None):
    dates = pd.bdate_range("2026-01-05", periods=2)
    bars_a = pd.DataFrame(
        {"open": [100.0, 100.0], "close": [100.0, last_close_a]},
        index=dates,
    )
    bars_b = pd.DataFrame(
        {"open": [100.0, 100.0], "close": [100.0, 100.0]},
        index=dates,
    )
    data_map = {"A": bars_a, "B": bars_b}
    close_df = pd.DataFrame(
        {"A": bars_a["close"], "B": bars_b["close"]},
        index=dates,
    )
    target_pos = pd.DataFrame(
        {"A": [0.5, 0.0], "B": [0.0, 0.5]},
        index=dates,
    )
    engine = _FrictionlessEngine({"initial_cash": 100_000.0})
    engine._execute_bars(
        dates,
        data_map,
        close_df,
        target_pos,
        code_order or ["A", "B"],
    )
    return engine


def test_decision_bar_close_cannot_change_open_position_size() -> None:
    baseline = _rotation_run(last_close_a=100.0)
    shocked = _rotation_run(last_close_a=200.0)

    baseline_b = next(t for t in baseline.trades if t.symbol == "B")
    shocked_b = next(t for t in shocked.trades if t.symbol == "B")

    assert baseline_b.size == 500.0
    assert shocked_b.size == baseline_b.size


def test_rotation_is_independent_of_close_open_symbol_order() -> None:
    a_first = _rotation_run(code_order=["A", "B"])
    b_first = _rotation_run(code_order=["B", "A"])

    a_first_trades = [(t.symbol, t.size, t.exit_reason) for t in a_first.trades]
    b_first_trades = [(t.symbol, t.size, t.exit_reason) for t in b_first.trades]

    assert a_first_trades == b_first_trades
    assert [symbol for symbol, _, _ in a_first_trades] == ["A", "B"]


def test_open_signal_exit_precedes_close_based_liquidation() -> None:
    dates = pd.date_range("2026-01-05", periods=2, freq="D")
    bars = pd.DataFrame(
        {
            "open": [100.0, 100.0],
            "high": [100.0, 100.0],
            "low": [100.0, 10.0],
            "close": [100.0, 10.0],
        },
        index=dates,
    )
    symbol = "BTC-USDT"
    close_df = pd.DataFrame({symbol: bars["close"]}, index=dates)
    target_pos = pd.DataFrame({symbol: [1.0, 0.0]}, index=dates)
    engine = CryptoEngine(
        {
            "initial_cash": 1_000.0,
            "leverage": 10.0,
            "maker_rate": 0.0,
            "taker_rate": 0.0,
            "slippage": 0.0,
            "funding_rate": 0.0,
        }
    )

    engine._execute_bars(
        dates,
        {symbol: bars},
        close_df,
        target_pos,
        [symbol],
    )

    assert len(engine.trades) == 1
    assert engine.trades[0].exit_reason == "signal"
    assert engine.trades[0].exit_price == 100.0
    assert engine.capital == 1_000.0
