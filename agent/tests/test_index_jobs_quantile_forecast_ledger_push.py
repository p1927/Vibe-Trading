"""quantile_forecast_ledger_push scheduled job.

Wires `quantile_forecast.forecast.forecast_nifty_range` +
`quantile_forecast.ledger_bridge.push_forecast_to_ledger` (Phase 7 of
.claude/backlog/items/2026-08-25-multi-factor-causal-forecast-platform.md) into a real
scheduled job — held back until
.claude/backlog/items/2026-08-26-quantile-forecast-live-wiring-pending-real-coverage.md's
real-coverage gate was met (the 14-factor causal graph's promoted, validated backtest).
"""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


@pytest.mark.unit
def test_run_quantile_forecast_ledger_push_job_calls_forecast_and_push(monkeypatch):
    forecast_calls = {}
    push_calls = {}

    def _fake_forecast(*, as_of, factor_start):
        forecast_calls["as_of"] = as_of
        forecast_calls["factor_start"] = factor_start
        return {"as_of_date": "2026-08-26", "spot_at_as_of": 24000.0, "horizons": []}

    def _fake_push(result):
        push_calls["result"] = result
        return 3

    monkeypatch.setattr(
        "trade_integrations.quantile_forecast.forecast.forecast_nifty_range", _fake_forecast
    )
    monkeypatch.setattr(
        "trade_integrations.quantile_forecast.ledger_bridge.push_forecast_to_ledger", _fake_push
    )

    result = index_jobs.run_quantile_forecast_ledger_push_job({"as_of": "2026-08-26", "factor_start": "2021-01-01"})

    assert forecast_calls["as_of"] == "2026-08-26"
    assert forecast_calls["factor_start"] == "2021-01-01"
    assert push_calls["result"]["as_of_date"] == "2026-08-26"
    assert result == {"status": "ok", "rows_appended": 3, "as_of_date": "2026-08-26"}


@pytest.mark.unit
def test_run_quantile_forecast_ledger_push_job_defaults_factor_start(monkeypatch):
    calls = {}

    def _fake_forecast(*, as_of, factor_start):
        calls["factor_start"] = factor_start
        return {"as_of_date": "2026-08-26", "spot_at_as_of": 24000.0, "horizons": []}

    monkeypatch.setattr(
        "trade_integrations.quantile_forecast.forecast.forecast_nifty_range", _fake_forecast
    )
    monkeypatch.setattr(
        "trade_integrations.quantile_forecast.ledger_bridge.push_forecast_to_ledger", lambda result: 0
    )

    index_jobs.run_quantile_forecast_ledger_push_job({})
    assert calls["factor_start"] == "2020-01-01"


@pytest.mark.unit
def test_run_quantile_forecast_ledger_push_job_returns_error_status_on_failure(monkeypatch):
    """Matches this file's own convention (e.g. news_dedup_quality_eval): a scheduled
    job function never raises, it reports a structured error for the dispatcher to log."""

    def _raise(*, as_of, factor_start):
        raise RuntimeError("no promoted causal graph")

    monkeypatch.setattr("trade_integrations.quantile_forecast.forecast.forecast_nifty_range", _raise)

    result = index_jobs.run_quantile_forecast_ledger_push_job({})
    assert result["status"] == "error"
    assert "no promoted causal graph" in result["error"]


@pytest.mark.unit
def test_dispatch_index_job_sync_routes_quantile_forecast_ledger_push(monkeypatch):
    seen = {}

    def _fake_run(config):
        seen["config"] = config
        return {"status": "ok"}

    monkeypatch.setattr(index_jobs, "run_quantile_forecast_ledger_push_job", _fake_run)

    job = ScheduledResearchJob(
        id="nifty-quantile-forecast-ledger-push",
        prompt="Push a live quantile-conformal Nifty range forecast into the prediction ledger",
        schedule="0 9 * * 1-5",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config={"job_type": index_jobs.JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH, "ticker": "NIFTY"},
    )
    index_jobs.dispatch_index_job_sync(job)

    assert seen["config"]["job_type"] == index_jobs.JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH


@pytest.mark.unit
def test_quantile_forecast_ledger_push_job_type_is_a_recognised_index_job_type():
    assert index_jobs.JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH in index_jobs.INDEX_JOB_TYPES


@pytest.mark.unit
def test_register_default_index_jobs_includes_quantile_forecast_ledger_push(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    job = store.get("nifty-quantile-forecast-ledger-push")
    assert job is not None
    assert job.config["job_type"] == index_jobs.JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH
    assert job.status == JobStatus.PENDING
    assert job.timezone == "Asia/Kolkata"
