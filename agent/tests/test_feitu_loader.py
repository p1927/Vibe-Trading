"""Contract tests for the optional FTShare market-data loader."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from backtest.loaders import feitu_loader as loader_mod
from backtest.loaders.base import NoAvailableSourceError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600000.SH", ("a_share", "600000.XSHG")),
        ("000001.XSHE", ("a_share", "000001.XSHE")),
        ("920163.BJ", ("a_share", "920163.BJSE")),
        ("700.HK", ("hk_equity", "00700.HK")),
        ("00700.hk", ("hk_equity", "00700.HK")),
        ("AAPL.US", None),
        ("BTC-USDT", None),
    ],
)
def test_normalize_symbol(raw: str, expected: tuple[str, str] | None) -> None:
    assert loader_mod._normalize_symbol(raw) == expected


def test_normalize_a_share_frame_uses_shanghai_trade_date() -> None:
    frame = pd.DataFrame(
        [
            {
                "open_ts_ms": 1782869400000,
                "open": "8.58",
                "high": "8.75",
                "low": "8.54",
                "close": "8.65",
                "volume": 53_417_711,
            }
        ]
    )

    result = loader_mod._normalize_frame(frame, market="a_share")

    assert result.index.name == "trade_date"
    assert result.index.tz is None
    assert result.index[0] == pd.Timestamp("2026-07-01")
    assert result.loc[pd.Timestamp("2026-07-01"), "close"] == pytest.approx(8.65)
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]


def test_normalize_hk_frame_sorts_dates_and_deduplicates() -> None:
    frame = pd.DataFrame(
        [
            {
                "date": "2026-07-08",
                "open": "461.2",
                "high": "482.8",
                "low": "460.6",
                "close": "478.8",
                "volume": 10,
            },
            {
                "date": "2026-07-07",
                "open": "459",
                "high": "479.8",
                "low": "457",
                "close": "461.2",
                "volume": 20,
            },
            {
                "date": "2026-07-08",
                "open": "462",
                "high": "483",
                "low": "461",
                "close": "479",
                "volume": 30,
            },
        ]
    )

    result = loader_mod._normalize_frame(frame, market="hk_equity")

    assert list(result.index) == [
        pd.Timestamp("2026-07-07"),
        pd.Timestamp("2026-07-08"),
    ]
    assert result.loc[pd.Timestamp("2026-07-08"), "close"] == pytest.approx(479)


@pytest.mark.parametrize("interval", ["1m", "1H", "2D"])
def test_unsupported_a_share_intervals_fail(interval: str) -> None:
    with pytest.raises(
        NoAvailableSourceError, match="unsupported FTShare A-share interval"
    ):
        loader_mod._a_share_interval(interval)


def test_hk_weekly_interval_fails_instead_of_changing_fidelity() -> None:
    with pytest.raises(NoAvailableSourceError, match="unsupported FTShare HK interval"):
        loader_mod._hk_interval("1W")


def test_is_available_is_import_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_sdk = SimpleNamespace(
        market_api=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("availability must not create a client")
        )
    )
    monkeypatch.setattr(loader_mod, "_require_ftshare", lambda: fake_sdk)

    assert loader_mod.DataLoader().is_available() is True


def test_fetch_routes_a_share_and_hk_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class FakeClient:
        closed = False

        def stock_ohlcs(self, **kwargs: object) -> pd.DataFrame:
            calls.append(("a_share", kwargs))
            return pd.DataFrame(
                [
                    {
                        "open_ts_ms": 1782869400000,
                        "open": "8.58",
                        "high": "8.75",
                        "low": "8.54",
                        "close": "8.65",
                        "volume": 100,
                    }
                ]
            )

        def hk_candlesticks(self, **kwargs: object) -> pd.DataFrame:
            calls.append(("hk", kwargs))
            return pd.DataFrame(
                [
                    {
                        "date": "2026-07-01",
                        "open": "460",
                        "high": "480",
                        "low": "455",
                        "close": "475",
                        "volume": 200,
                    }
                ]
            )

        def close(self) -> None:
            self.closed = True

    client = FakeClient()
    fake_sdk = SimpleNamespace(market_api=lambda **kwargs: client)
    monkeypatch.setattr(loader_mod, "_require_ftshare", lambda: fake_sdk)
    monkeypatch.setattr(
        loader_mod,
        "cached_loader_fetch",
        lambda **kwargs: kwargs["fetch"](),
    )

    result = loader_mod.DataLoader().fetch(
        ["600000.SH", "700.HK"],
        "2026-07-01",
        "2026-07-10",
    )

    assert set(result) == {"600000.SH", "700.HK"}
    assert calls == [
        (
            "a_share",
            {
                "symbol": "600000.XSHG",
                "since": "20260701",
                "until": "20260710",
                "interval": "Day",
                "adjust": "Forward",
            },
        ),
        (
            "hk",
            {
                "trade_code": "00700.HK",
                "interval_unit": "day",
                "since_date": "2026-07-01",
                "until_date": "2026-07-10",
                "interval_value": 1,
                "adjust_kind": "forward",
            },
        ),
    ]
    assert client.closed is True


def test_fetch_failure_returns_empty_map_for_runtime_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenClient:
        def stock_ohlcs(self, **kwargs: object) -> pd.DataFrame:
            raise RuntimeError("upstream unavailable")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        loader_mod,
        "_require_ftshare",
        lambda: SimpleNamespace(market_api=lambda **kwargs: BrokenClient()),
    )
    monkeypatch.setattr(
        loader_mod,
        "cached_loader_fetch",
        lambda **kwargs: kwargs["fetch"](),
    )

    assert (
        loader_mod.DataLoader().fetch(["600000.SH"], "2026-07-01", "2026-07-10") == {}
    )
