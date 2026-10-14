"""MT5 CFD notional (CEO decision 2026-10-14): lots * contract_size is only a value for forex pairs.

An index/stock CFD reports ``currency_base == "USD"`` (or its own currency) with a contract size of 1-100
units of the instrument, so ``lots * size`` understated its USD exposure by the price (5000x for an
index) and disarmed every mandate cap. Non-forex calc modes now price the units.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.trading.connectors.mt5 import sdk
from tests.test_mt5_connector import FakeMT5, _paper_config, _symbol, fake_mt5  # noqa: F401  (fixture)

pytestmark = pytest.mark.unit


def _size(symbol: str, lots: float):
    return sdk.quantity_notional_usd(_paper_config(), symbol, lots)


def _tick(mid: float) -> SimpleNamespace:
    return SimpleNamespace(bid=mid - 0.5, ask=mid + 0.5, last=0.0, time=1_750_000_000)


def test_usd_index_cfd_is_priced(fake_mt5):  # noqa: F811
    info = _symbol("US500m", base="USD", profit="USD", contract_size=1.0)
    fake_mt5.symbols["US500m"] = info
    fake_mt5.ticks["US500m"] = _tick(5000.0)
    assert _size("US500", 2.0) == pytest.approx(10_000.0)  # was 2.0 USD


def test_calc_mode_cfd_wins_over_currency_heuristic(fake_mt5):  # noqa: F811
    info = _symbol("USDXm", base="USD", profit="JPY", contract_size=10.0)
    info.trade_calc_mode = 2  # SYMBOL_CALC_MODE_CFD: a JPY-priced CFD, not a USDJPY pair
    fake_mt5.symbols["USDXm"] = info
    fake_mt5.ticks["USDXm"] = _tick(20000.0)  # JPY
    # 1 lot = 10 units * 20000 JPY = 200000 JPY -> USD through USDJPYm mid 156.12
    assert _size("USDX", 1.0) == pytest.approx(200_000.0 / 156.12)


def test_non_usd_stock_cfd_is_priced_and_converted(fake_mt5):  # noqa: F811
    fake_mt5.symbols["SAPm"] = _symbol("SAPm", base="EUR", profit="EUR", contract_size=1.0)
    fake_mt5.ticks["SAPm"] = _tick(200.0)
    assert _size("SAP", 10.0) == pytest.approx(10 * 200.0 * 1.08)


def test_cfd_without_a_price_fails_closed(fake_mt5):  # noqa: F811
    fake_mt5.symbols["US500m"] = _symbol("US500m", base="USD", profit="USD", contract_size=1.0)
    assert _size("US500", 1.0) is None


def test_forex_pairs_unchanged(fake_mt5):  # noqa: F811
    assert _size("USDJPY", 0.1) == pytest.approx(10_000.0)
    assert _size("EURUSD", 0.1) == pytest.approx(10_800.0)
