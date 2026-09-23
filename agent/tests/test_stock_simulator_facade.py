"""Runnable check for the D117 in-process facade adapter: real local-mode client, no mocks.

Reads the ambient hub (`TRADE_STACK_HUB_DIR`); skips when the Trade stack or hub is absent.
"""
from __future__ import annotations

import pytest

facade = pytest.importorskip("src.trade.stock_simulator_facade")


def _client():
    try:
        return facade.sim_client()
    except Exception as exc:  # noqa: BLE001 - standalone checkout without the Trade stack
        pytest.skip(f"trade stack not importable: {exc}")


@pytest.mark.unit
def test_coverage_and_eod_series_return_the_shapes_the_callers_read():
    c = _client()
    assert "week_start" in c.get_coverage_report(week_start="2026-09-14")["data"]
    assert isinstance(c.list_eod_refreshable_series()["series"], list)


@pytest.mark.unit
def test_the_backfill_run_api_the_routes_proxy_answers_through_the_facade():
    """Every UI backfill goes through stock_simulator's background run API (D195/D204): the
    `/trade/hub/stock-history/backfill-runs` routes call these client methods by name. Read-only:
    asks for the active run, starts nothing."""
    from trade_integrations.stock_simulator.client import StockSimulatorClientError

    c = _client()
    for name in ("start_backfill_run", "get_backfill_run", "get_active_backfill_run", "cancel_backfill_run"):
        assert callable(getattr(c, name)), name
    try:
        active = c.get_active_backfill_run()
    except StockSimulatorClientError as exc:
        pytest.skip(f"stock_simulator service not usable from this checkout: {exc}")
    assert "run" in active


@pytest.mark.unit
def test_facade_history_reads_recorded_bars_as_attribute_objects():
    from datetime import datetime, timedelta

    h = facade.FacadeHistory()
    days = h.recorded_index_days(symbol="NIFTY", exchange="NSE_INDEX")
    if not days:
        pytest.skip("no recorded NIFTY days in this hub")
    since = datetime.fromisoformat(days[-1])
    bars = h.index_history(symbol="NIFTY", exchange="NSE_INDEX", since_ist=since, until_ist=since + timedelta(days=1))
    assert bars and bars[0].trading_day == days[-1] and isinstance(bars[0].ts_ist, datetime)
