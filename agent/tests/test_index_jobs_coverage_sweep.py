"""stock_history_coverage_sweep scheduled job.

Before this job existed, the only automatic trigger for the
stock_history coverage buckets was `StockHistory.supplement_today()`
(3 buckets, once per recording session — and itself a no-op until the
bucket-name mismatch fix in `stock_history/coverage.py`). Several
buckets (constituents, constituent_ohlcv, sector_index_daily,
equity_ohlcv, index_tape_banknifty/sensex) went stale for weeks to
over a year with nothing ever calling their working backfill handlers.
This job runs a full-coverage `backfill_into_week` sweep daily.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


def _autospec_stock_history(monkeypatch, *, summary=None, error=None):
    """Install a `StockHistory` whose every call binds against the REAL signatures.

    `create_autospec` replaces the hand-written doubles this file used to carry: one with an
    explicit keyword list that broke when `budget_seconds` was added (and, because the job's
    own `except Exception` swallowed the TypeError, failed on a downstream assertion), and two
    taking `**kwargs` that would have kept passing against any call at all
    ([[2026-09-07-coverage-sweep-test-double-stale-after-budget-change]]). An autospec needs no
    upkeep: a renamed or added parameter on the real method is a bind error at the call site.
    """
    index_jobs._ensure_trade_integrations_on_path()
    from trade_integrations.stock_history.api import StockHistory

    sh = create_autospec(StockHistory, instance=True)
    if error is not None:
        sh.backfill_into_week.side_effect = error
    else:
        sh.backfill_into_week.return_value = summary
    monkeypatch.setattr("trade_integrations.stock_history.api.StockHistory", lambda *a, **k: sh)
    monkeypatch.setattr(
        "trade_integrations.dataflows.company_research.market.india_trading_date_iso",
        lambda: "2026-08-18T00:00:00Z",
    )
    return sh


@pytest.mark.unit
def test_run_stock_history_coverage_sweep_job_calls_backfill_into_week(monkeypatch):
    sh = _autospec_stock_history(
        monkeypatch,
        summary=SimpleNamespace(had_errors=False, ok_count=5, failed_count=0, skipped_count=1),
    )

    result = index_jobs.run_stock_history_coverage_sweep_job({"include_optional": True})

    # First, so a signature drift fails HERE with the real bind error in the message, not on a
    # later assertion about a result the job never produced.
    assert "error" not in result, result.get("error")
    # Budgeted inside the executor's 30-minute dispatch timeout; see
    # [[2026-09-07-coverage-sweep-times-out-and-stops-backfilling]].
    sh.backfill_into_week.assert_called_once_with(
        week_start="2026-08-18", include_optional=True, verify_after=True, budget_seconds=1200.0,
    )
    assert result["status"] == "ok"
    assert result["ok_count"] == 5
    assert result["had_errors"] is False


@pytest.mark.unit
def test_the_autospec_rejects_an_argument_the_real_method_does_not_take():
    """Proves the double is not blind: the drift it guards against is a bind error."""
    index_jobs._ensure_trade_integrations_on_path()
    from trade_integrations.stock_history.api import StockHistory

    sh = create_autospec(StockHistory, instance=True)
    with pytest.raises(TypeError):
        sh.backfill_into_week(week_start="2026-08-18", not_a_real_parameter=1)


@pytest.mark.unit
def test_run_stock_history_coverage_sweep_job_reports_errors(monkeypatch):
    sh = _autospec_stock_history(
        monkeypatch,
        summary=SimpleNamespace(had_errors=True, ok_count=2, failed_count=3, skipped_count=0),
    )

    result = index_jobs.run_stock_history_coverage_sweep_job({})
    assert "error" not in result, result.get("error")
    sh.backfill_into_week.assert_called_once()
    assert result["status"] == "error"
    assert result["failed_count"] == 3
    assert result["had_errors"] is True


@pytest.mark.unit
def test_run_stock_history_coverage_sweep_job_never_raises(monkeypatch):
    _autospec_stock_history(monkeypatch, error=RuntimeError("hub_dir unreachable"))

    result = index_jobs.run_stock_history_coverage_sweep_job({})
    assert result["status"] == "error"
    assert "hub_dir unreachable" in result["error"]
    assert result["had_errors"] is True


@pytest.mark.unit
def test_dispatch_index_job_sync_routes_coverage_sweep(monkeypatch):
    seen = {}

    def _fake_run(config):
        seen["config"] = config
        return {"status": "ok", "had_errors": False}

    monkeypatch.setattr(index_jobs, "run_stock_history_coverage_sweep_job", _fake_run)

    job = ScheduledResearchJob(
        id="stock-history-coverage-sweep",
        prompt="Daily full-coverage backfill sweep",
        schedule="0 19 * * *",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config={"job_type": index_jobs.JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP, "include_optional": True},
    )
    index_jobs.dispatch_index_job_sync(job)

    assert seen["config"]["job_type"] == index_jobs.JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP


@pytest.mark.unit
def test_stock_history_coverage_sweep_job_type_is_a_recognised_index_job_type():
    assert (
        index_jobs.JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP in index_jobs.INDEX_JOB_TYPES
    )


@pytest.mark.unit
def test_register_default_index_jobs_includes_coverage_sweep(monkeypatch, tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    job = store.get("stock-history-coverage-sweep")
    assert job is not None
    assert job.config["job_type"] == index_jobs.JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP
