"""Historical Binance USD-M data-contract tests."""

from __future__ import annotations

import json

import pytest
import pandas as pd

from backtest.loaders.base import make_loader_cache_key
from backtest.loaders.ccxt_loader import _parse_ccxt_symbol


def _hourly_rows(opens: list[float]) -> list[list[float]]:
    start = int(pd.Timestamp("2024-01-01 00:00:00").timestamp() * 1000)
    hour = 3_600_000
    return [
        [start + i * hour, value, value + 2, value - 2, value + 1, 10 + i]
        for i, value in enumerate(opens)
    ]


_DEFAULT_BRACKETS = [
    {"bracket": 1, "notionalCap": 50_000, "maintMarginRatio": 0.004, "cum": 0.0},
    {"bracket": 2, "notionalCap": 250_000, "maintMarginRatio": 0.005, "cum": 50.0},
]


class _PerpetualExchange:
    def __init__(
        self,
        *,
        mark_rows: list[list[float]] | None = None,
        funding_rows: list[dict[str, object]] | None = None,
        brackets: list[dict[str, object]] | None = None,
    ) -> None:
        self.trade_rows = _hourly_rows([100.0, 101.0])
        self.mark_rows = mark_rows if mark_rows is not None else _hourly_rows([99.0, 100.0])
        start = int(pd.Timestamp("2024-01-01 00:00:00").timestamp() * 1000)
        self.funding_rows = (
            funding_rows
            if funding_rows is not None
            else [{"timestamp": start, "fundingRate": 0.0}]
        )
        self.brackets = _DEFAULT_BRACKETS if brackets is None else brackets
        self.calls: list[dict[str, object]] = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        self.calls.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "since": since,
            "limit": limit,
            "params": params,
        })
        return self.mark_rows if params == {"price": "mark"} else self.trade_rows

    def fetch_funding_rate_history(self, symbol, since=None, limit=None):
        self.calls.append({
            "funding_symbol": symbol,
            "since": since,
            "limit": limit,
        })
        return self.funding_rows

    def fetch_leverage_tiers(self, symbols):
        self.calls.append({"bracket_symbols": symbols})
        [symbol] = symbols
        return {
            symbol: [
                {
                    "tier": row["bracket"],
                    "minNotional": 0,
                    "maxNotional": row["notionalCap"],
                    "maintenanceMarginRate": row["maintMarginRatio"],
                    "maxLeverage": 1,
                    "info": row,
                }
                for row in self.brackets
            ]
        }


def test_spot_symbol_keeps_existing_ccxt_contract() -> None:
    assert _parse_ccxt_symbol("BTC-USDT") == ("BTC/USDT", "spot")


def test_perpetual_symbol_maps_to_binance_usdm_contract() -> None:
    assert _parse_ccxt_symbol("BTC-USDT-PERP") == ("BTC/USDT:USDT", "swap")


@pytest.mark.parametrize("code", ["BTC-PERP", "-USDT-PERP", "BTC--PERP"])
def test_malformed_perpetual_symbol_is_rejected(code: str) -> None:
    with pytest.raises(ValueError, match="USD-M perpetual symbol"):
        _parse_ccxt_symbol(code)


def test_spot_and_perpetual_cache_keys_cannot_collide() -> None:
    common = {
        "source": "ccxt",
        "timeframe": "1H",
        "start_date": "2024-01-01",
        "end_date": "2024-01-31",
        "fields": None,
    }
    spot = make_loader_cache_key(symbol="BTC-USDT", **common)
    perpetual = make_loader_cache_key(symbol="BTC-USDT-PERP", **common)
    assert spot != perpetual


def test_perpetual_fetch_separates_execution_and_mark_prices(monkeypatch) -> None:
    exchange = _PerpetualExchange()
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    frame = DataLoader().fetch(
        ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
    )["BTC-USDT-PERP"]

    assert frame["execution_open"].tolist() == [100.0, 101.0]
    assert frame["mark_open"].tolist() == [99.0, 100.0]
    assert frame["mark_high"].tolist() == [101.0, 102.0]
    assert frame["mark_low"].tolist() == [97.0, 98.0]
    assert frame["mark_close"].tolist() == [100.0, 101.0]
    assert exchange.calls[0]["symbol"] == "BTC/USDT:USDT"
    assert exchange.calls[0]["params"] is None
    assert exchange.calls[1]["params"] == {"price": "mark"}


def test_perpetual_fetch_rejects_unsynchronized_mark_rows(monkeypatch) -> None:
    mark_rows = _hourly_rows([99.0, 100.0])
    mark_rows[1][0] += 60_000
    exchange = _PerpetualExchange(mark_rows=mark_rows)
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    with pytest.raises(ValueError, match="mark-price timestamps"):
        DataLoader().fetch(
            ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
        )


def test_perpetual_fetch_aligns_explicit_zero_funding_settlement(monkeypatch) -> None:
    exchange = _PerpetualExchange()
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    frame = DataLoader().fetch(
        ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
    )["BTC-USDT-PERP"]

    assert frame["funding_rate"].tolist() == [0.0, 0.0]
    assert frame["funding_settlement_time"].iloc[0] == pd.Timestamp("2024-01-01")
    assert pd.isna(frame["funding_settlement_time"].iloc[1])


def test_perpetual_fetch_rejects_missing_required_funding_settlement(monkeypatch) -> None:
    exchange = _PerpetualExchange(funding_rows=[])
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    with pytest.raises(ValueError, match="funding settlement"):
        DataLoader().fetch(
            ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
        )


def test_perpetual_fetch_rejects_duplicate_funding_settlement(monkeypatch) -> None:
    start = int(pd.Timestamp("2024-01-01 00:00:00").timestamp() * 1000)
    exchange = _PerpetualExchange(funding_rows=[
        {"timestamp": start, "fundingRate": 0.0001},
        {"timestamp": start, "fundingRate": 0.0002},
    ])
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    with pytest.raises(ValueError, match="duplicate funding settlement"):
        DataLoader().fetch(
            ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
        )


def test_perpetual_fetch_attaches_versioned_maintenance_brackets(monkeypatch) -> None:
    exchange = _PerpetualExchange()
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    frame = DataLoader().fetch(
        ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
    )["BTC-USDT-PERP"]

    versions = frame["maintenance_bracket_version"].unique().tolist()
    assert len(versions) == 1
    assert isinstance(versions[0], str) and versions[0]

    brackets = json.loads(frame["maintenance_brackets"].iloc[0])
    assert brackets == [
        {
            "bracket_tier": 1,
            "notional_cap": 50_000.0,
            "maintenance_rate": 0.004,
            "cumulative_maintenance_amount": 0.0,
        },
        {
            "bracket_tier": 2,
            "notional_cap": 250_000.0,
            "maintenance_rate": 0.005,
            "cumulative_maintenance_amount": 50.0,
        },
    ]
    assert frame["maintenance_brackets"].nunique() == 1


def test_perpetual_fetch_bracket_version_changes_with_bracket_contents(monkeypatch) -> None:
    from backtest.loaders.ccxt_loader import DataLoader

    exchange_a = _PerpetualExchange()
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange_a,
    )
    frame_a = DataLoader().fetch(
        ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
    )["BTC-USDT-PERP"]

    exchange_b = _PerpetualExchange(brackets=[
        {"bracket": 1, "notionalCap": 50_000, "maintMarginRatio": 0.005, "cum": 0.0},
    ])
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange_b,
    )
    frame_b = DataLoader().fetch(
        ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
    )["BTC-USDT-PERP"]

    assert (
        frame_a["maintenance_bracket_version"].iloc[0]
        != frame_b["maintenance_bracket_version"].iloc[0]
    )


def test_perpetual_fetch_rejects_empty_maintenance_brackets(monkeypatch) -> None:
    exchange = _PerpetualExchange(brackets=[])
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    with pytest.raises(ValueError, match="maintenance bracket"):
        DataLoader().fetch(
            ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
        )


def test_perpetual_fetch_rejects_maintenance_bracket_missing_field(monkeypatch) -> None:
    exchange = _PerpetualExchange(brackets=[
        {"bracket": 1, "notionalCap": 50_000, "maintMarginRatio": 0.004},
    ])
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    with pytest.raises(ValueError, match="maintenance bracket"):
        DataLoader().fetch(
            ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
        )


def test_perpetual_fetch_rejects_non_monotonic_maintenance_brackets(monkeypatch) -> None:
    exchange = _PerpetualExchange(brackets=[
        {"bracket": 1, "notionalCap": 250_000, "maintMarginRatio": 0.004, "cum": 0.0},
        {"bracket": 2, "notionalCap": 50_000, "maintMarginRatio": 0.005, "cum": 50.0},
    ])
    monkeypatch.setattr(
        "backtest.loaders.ccxt_loader.DataLoader._get_exchange",
        lambda _self, instrument_type="spot": exchange,
    )

    from backtest.loaders.ccxt_loader import DataLoader

    with pytest.raises(ValueError, match="maintenance bracket"):
        DataLoader().fetch(
            ["BTC-USDT-PERP"], "2024-01-01", "2024-01-01", interval="1H"
        )
