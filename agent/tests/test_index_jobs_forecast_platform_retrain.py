"""forecast_platform_retrain scheduled job.

Wires trade_integrations.forecast_platform_retrain.retrain_forecast_platform (causal
graph + regime model + quantile forecast, Phase 4-6 of
.claude/backlog/items/2026-08-25-multi-factor-causal-forecast-platform.md) into a real
scheduled job, closing a confirmed 2026-08-26 gap: run_causal_discovery/
run_regime_detection/run_quantile_forecast had zero call sites anywhere in the repo, not
even in tests, before this job existed — exactly the "built but never scheduled" pattern
.claude/backlog/items/2026-08-25-investigation-built-but-never-scheduled-pattern.md
already documented 4 prior instances of.
"""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


@pytest.mark.unit
def test_run_forecast_platform_retrain_job_calls_retrain_forecast_platform(monkeypatch):
    calls = {}

    def _fake_retrain(*, end, factor_start):
        calls["end"] = end
        calls["factor_start"] = factor_start
        return {"status": "ok", "causal_graph": {"status": "ok"}}

    monkeypatch.setattr(
        "trade_integrations.forecast_platform_retrain.retrain_forecast_platform", _fake_retrain
    )

    result = index_jobs.run_forecast_platform_retrain_job({"end": "2026-08-24", "factor_start": "2021-01-01"})

    assert calls["end"] == "2026-08-24"
    assert calls["factor_start"] == "2021-01-01"
    assert result["status"] == "ok"


@pytest.mark.unit
def test_run_forecast_platform_retrain_job_defaults_factor_start(monkeypatch):
    calls = {}

    def _fake_retrain(*, end, factor_start):
        calls["factor_start"] = factor_start
        return {"status": "ok"}

    monkeypatch.setattr(
        "trade_integrations.forecast_platform_retrain.retrain_forecast_platform", _fake_retrain
    )

    index_jobs.run_forecast_platform_retrain_job({})
    assert calls["factor_start"] == "2020-01-01"


@pytest.mark.unit
def test_dispatch_index_job_sync_routes_forecast_platform_retrain(monkeypatch):
    seen = {}

    def _fake_run(config):
        seen["config"] = config
        return {"status": "ok"}

    monkeypatch.setattr(index_jobs, "run_forecast_platform_retrain_job", _fake_run)

    job = ScheduledResearchJob(
        id="nifty-forecast-platform-retrain",
        prompt="Retrain the multi-factor causal forecast platform",
        schedule="0 5 * * 1",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config={"job_type": index_jobs.JOB_TYPE_FORECAST_PLATFORM_RETRAIN, "ticker": "NIFTY"},
    )
    index_jobs.dispatch_index_job_sync(job)

    assert seen["config"]["job_type"] == index_jobs.JOB_TYPE_FORECAST_PLATFORM_RETRAIN


@pytest.mark.unit
def test_forecast_platform_retrain_job_type_is_a_recognised_index_job_type():
    assert index_jobs.JOB_TYPE_FORECAST_PLATFORM_RETRAIN in index_jobs.INDEX_JOB_TYPES


@pytest.mark.unit
def test_register_default_index_jobs_includes_forecast_platform_retrain(tmp_path):
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    job = store.get("nifty-forecast-platform-retrain")
    assert job is not None
    assert job.config["job_type"] == index_jobs.JOB_TYPE_FORECAST_PLATFORM_RETRAIN
    assert job.status == JobStatus.PENDING
