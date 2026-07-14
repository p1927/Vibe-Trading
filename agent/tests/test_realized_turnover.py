"""Regression tests for execution-derived turnover metrics."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.engines.base import BaseEngine
from backtest.engines.china_a import ChinaAEngine
from backtest.metrics import calc_metrics, calc_trade_turnover_series


class _RoundedEngine(BaseEngine):
    def can_execute(self, symbol, direction, bar):
        return True

    def round_size(self, raw_size, price):
        return float(int(raw_size))

    def calc_commission(self, size, price, direction, is_open):
        return 0.0

    def apply_slippage(self, price, direction):
        return price


def test_turnover_uses_rounded_fills_instead_of_targets() -> None:
    dates = pd.bdate_range("2026-01-05", periods=2)
    bars = pd.DataFrame({"open": [60.0, 60.0], "close": [60.0, 60.0]}, index=dates)
    close_df = pd.DataFrame({"TEST": bars["close"]}, index=dates)
    targets = pd.DataFrame({"TEST": [0.55, 0.0]}, index=dates)
    engine = _RoundedEngine({"initial_cash": 1_000.0})

    engine._execute_bars(dates, {"TEST": bars}, close_df, targets, ["TEST"])
    equity = pd.Series(
        [snapshot.equity for snapshot in engine.equity_snapshots],
        index=dates,
    )
    turnover = calc_trade_turnover_series(engine.trades, equity)

    # The target asks for 550, but integer sizing fills 9 * 60 = 540.
    assert turnover.tolist() == pytest.approx([0.27, 0.27])
    metrics = calc_metrics(
        equity,
        engine.trades,
        1_000.0,
        positions=targets,
        turnover_series=turnover,
    )
    assert metrics["total_turnover"] == pytest.approx(0.54)
    assert metrics["avg_turnover"] == pytest.approx(0.27)


def test_rejected_target_has_zero_reported_turnover(tmp_path: Path) -> None:
    dates = pd.bdate_range("2026-01-05", periods=3)
    bars = pd.DataFrame(
        {
            "open": [10.0, 10.0, 10.0],
            "high": [10.0, 10.0, 10.0],
            "low": [10.0, 10.0, 10.0],
            "close": [10.0, 10.0, 10.0],
            "volume": [1_000, 1_000, 1_000],
        },
        index=dates,
    )

    class FakeLoader:
        def fetch(self, *args, **kwargs):
            return {"000001.SZ": bars.copy()}

    class ShortSignal:
        def generate(self, data_map):
            return {"000001.SZ": pd.Series(-1.0, index=dates)}

    engine = ChinaAEngine({"initial_cash": 1_000_000.0})
    metrics = engine.run_backtest(
        {
            "codes": ["000001.SZ"],
            "start_date": "2026-01-05",
            "end_date": "2026-01-07",
            "source": "tushare",
            "initial_cash": 1_000_000.0,
        },
        FakeLoader(),
        ShortSignal(),
        tmp_path,
    )

    # China A-shares reject short opens. The target frame changes, but no fill
    # occurs, so execution-derived turnover must remain zero.
    assert engine.trades == []
    assert metrics["total_turnover"] == 0.0
    assert metrics["avg_turnover"] == 0.0
