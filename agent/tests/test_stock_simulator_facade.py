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
def test_backfill_dry_run_and_coverage_return_the_shapes_the_callers_read():
    c = _client()
    summary = c.backfill_into_week(
        week_start="2026-09-14", dry_run=True, max_jobs=0, verify_after=False, buckets=["macro_factors"]
    )["data"]
    for key in ("had_errors", "ok_count", "failed_count", "skipped_count", "coverage_after"):
        assert key in summary
    assert "week_start" in c.get_coverage_report(week_start="2026-09-14")["data"]
    assert isinstance(c.list_eod_refreshable_series()["series"], list)


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
