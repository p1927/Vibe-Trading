"""Wilder RSI is seed-dominated on a short window; daily-RSI callers must fetch the full warmup."""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.tools.technical_indicator_tool import TechnicalIndicatorTool, _compute_rsi
from src.trade.technicals import RSI_WARMUP_CALENDAR_DAYS


@pytest.fixture
def walk():
    rng = np.random.default_rng(7)
    close = 100 + rng.normal(0, 1, 300).cumsum()
    return pd.Series(close, index=pd.date_range("2024-01-01", periods=300, freq="B"), name="close")


def _run(monkeypatch, walk, **kw):
    seen = {}

    def _fetch(**kwargs):
        seen.update(kwargs)
        return {"AAPL": pd.DataFrame({"close": walk})}

    monkeypatch.setattr("src.tools.technical_indicator_tool.fetch_market_data", _fetch)
    return json.loads(TechnicalIndicatorTool().execute(symbol="AAPL", **kw)), seen


def test_rsi_independent_of_lookback(monkeypatch, walk):
    small, _ = _run(monkeypatch, walk, lookback=10)
    big, _ = _run(monkeypatch, walk, lookback=200)
    assert small["indicators"]["rsi_14"] == big["indicators"]["rsi_14"]


def test_rsi_uses_whole_fetch_not_the_tail(monkeypatch, walk):
    res, _ = _run(monkeypatch, walk, lookback=30)
    assert res["indicators"]["rsi_14"] == pytest.approx(_compute_rsi(walk))
    assert res["indicators"]["rsi_14"] != pytest.approx(_compute_rsi(walk.tail(30)))


def test_daily_fetch_covers_the_warmup_even_for_tiny_lookback(monkeypatch, walk):
    _, seen = _run(monkeypatch, walk, lookback=10)
    assert (date.today() - date.fromisoformat(seen["start_date"])).days >= RSI_WARMUP_CALENDAR_DAYS


def test_callers_use_the_one_warmup_constant():
    from src.shadow_account import extractor, scanner

    assert extractor._PRICE_LOOKBACK_DAYS == RSI_WARMUP_CALENDAR_DAYS
    assert scanner._DEFAULT_HISTORY_DAYS == RSI_WARMUP_CALENDAR_DAYS


def test_warmup_constant_covers_trades_warmup_sessions():
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from trade_integrations.dataflows.index_research.technical_features import TECHNICAL_WARMUP_SESSIONS

    assert RSI_WARMUP_CALENDAR_DAYS >= TECHNICAL_WARMUP_SESSIONS * 365 / 252
