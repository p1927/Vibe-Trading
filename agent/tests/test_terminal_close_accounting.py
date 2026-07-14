"""Regression tests for end-of-backtest liquidation accounting."""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.engines.base import BaseEngine


class _TerminalCostEngine(BaseEngine):
    def can_execute(self, symbol, direction, bar):
        return True

    def round_size(self, raw_size, price):
        return raw_size

    def calc_commission(self, size, price, direction, is_open):
        return 0.0 if is_open else 7.0

    def apply_slippage(self, price, direction):
        return price + direction if self.positions else price


@pytest.mark.parametrize(
    ("target_weight", "expected_exit"),
    [(0.5, 99.0), (-0.5, 101.0)],
)
def test_terminal_close_costs_reach_final_equity(
    target_weight: float,
    expected_exit: float,
) -> None:
    dates = pd.DatetimeIndex(["2026-01-05"])
    bars = pd.DataFrame({"open": [100.0], "close": [100.0]}, index=dates)
    close_df = pd.DataFrame({"TEST": bars["close"]}, index=dates)
    target_pos = pd.DataFrame({"TEST": [target_weight]}, index=dates)
    engine = _TerminalCostEngine({"initial_cash": 1_000.0})

    engine._execute_bars(
        dates,
        {"TEST": bars},
        close_df,
        target_pos,
        ["TEST"],
    )

    assert len(engine.trades) == 1
    trade = engine.trades[0]
    assert trade.exit_reason == "end_of_backtest"
    assert trade.exit_price == expected_exit
    assert trade.commission == 7.0
    assert engine.capital == pytest.approx(988.0)

    final_snapshot = engine.equity_snapshots[-1]
    assert final_snapshot.capital == pytest.approx(engine.capital)
    assert final_snapshot.equity == pytest.approx(engine.capital)
    assert final_snapshot.unrealized == 0.0
    assert final_snapshot.positions == 0
