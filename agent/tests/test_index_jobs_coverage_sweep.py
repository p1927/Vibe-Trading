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

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


@pytest.mark.unit
def test_run_stock_history_coverage_sweep_job_calls_backfill_into_week(monkeypatch):
    calls = {}

    class _FakeStockHistory:
        def backfill_into_week(self, *, week_start, include_optional, verify_after):
            calls["week_start"] = week_start
            calls["include_optional"] = include_optional
            calls["verify_after"] = verify_after
            return SimpleNamespace(had_errors=False, ok_count=5, failed_count=0, skipped_count=1)

    monkeypatch.setattr(
        "trade_integrations.stock_history.api.StockHistory", _FakeStockHistory
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.company_research.market.india_trading_date_iso",
        lambda: "2026-08-18T00:00:00Z",
    )

    result = index_jobs.run_stock_history_coverage_sweep_job({"include_optional": True})

    assert calls["include_optional"] is True
    assert calls["verify_after"] is True
    assert calls["week_start"] == "2026-08-18"
    assert result["status"] == "ok"
    assert result["ok_count"] == 5
    assert result["had_errors"] is False


@pytest.mark.unit
def test_run_stock_history_coverage_sweep_job_reports_errors(monkeypatch):
    class _FakeStockHistory:
        def backfill_into_week(self, **kwargs):
            return SimpleNamespace(had_errors=True, ok_count=2, failed_count=3, skipped_count=0)

    monkeypatch.setattr(
        "trade_integrations.stock_history.api.StockHistory", _FakeStockHistory
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.company_research.market.india_trading_date_iso",
        lambda: "2026-08-18T00:00:00Z",
    )

    result = index_jobs.run_stock_history_coverage_sweep_job({})
    assert result["status"] == "error"
    assert result["failed_count"] == 3
    assert result["had_errors"] is True


@pytest.mark.unit
def test_run_stock_history_coverage_sweep_job_never_raises(monkeypatch):
    class _FakeStockHistory:
        def backfill_into_week(self, **kwargs):
            raise RuntimeError("hub_dir unreachable")

    monkeypatch.setattr(
        "trade_integrations.stock_history.api.StockHistory", _FakeStockHistory
    )

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
