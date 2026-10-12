"""forecast_engine_refresh scheduled job: the daily TimesFM refresh of every forecast_engine recipe's
cached artifacts (`trade_integrations.forecast_engine.refresh.refresh_all_recipes`, the same function
`scripts/run_forecast_engine_latest.py` calls). Release-only (a collection job type), runs in a child
process (loads a 1.3 GB checkpoint), fails loud when any recipe errors or is deferred."""
from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.job_tier_policy import collection_job_dispatch_enabled, is_collection_job
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.run_outcome import JobRunHadErrorsError
from src.scheduled_research.store import ScheduledResearchJobStore

TYPE = index_jobs.JOB_TYPE_FORECAST_ENGINE_REFRESH


def _job(**config) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id="nifty-forecast-engine-refresh", prompt="p", schedule="1000", next_run_at=0,
        status=JobStatus.PENDING, created_at=0, config={"job_type": TYPE, "ticker": "NIFTY", **config},
    )


@pytest.mark.unit
def test_job_type_is_an_index_job_run_in_a_child_and_release_only():
    assert TYPE in index_jobs.INDEX_JOB_TYPES
    assert TYPE in index_jobs.CHILD_PROCESS_JOB_TYPES
    assert is_collection_job(TYPE)
    assert collection_job_dispatch_enabled("release") and not collection_job_dispatch_enabled("dev")


@pytest.mark.unit
def test_default_job_is_registered_daily_after_the_close_with_a_deadline_budget(tmp_path):
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    job = store.get("nifty-forecast-engine-refresh")
    assert job is not None and job.status == JobStatus.PENDING
    assert job.config["job_type"] == TYPE
    assert job.timezone == "Asia/Kolkata"
    assert job.schedule == "30 20 * * 1-5"  # 20:30 IST, after the 17:30 bhavcopy job and the evening flow data
    assert job.config["dispatch_timeout_ms"] == index_jobs._FORECAST_ENGINE_REFRESH_DISPATCH_TIMEOUT_MS


@pytest.mark.unit
def test_dispatch_passes_the_75_percent_deadline_and_records_the_summary(monkeypatch):
    seen = {}

    def _fake(config, *, time_budget_s):
        seen["budget"], seen["config"] = time_budget_s, config
        return {"status": "ok", "had_errors": False, "recipes": {}}

    monkeypatch.setattr(index_jobs, "run_forecast_engine_refresh_job", _fake)
    job = _job(dispatch_timeout_ms=40 * 60 * 1000)

    index_jobs.dispatch_index_job_sync(job)

    assert seen["budget"] == pytest.approx(0.75 * 40 * 60)
    assert job.config["_last_result_summary"]["status"] == "ok"


@pytest.mark.unit
@pytest.mark.parametrize("summary", [
    {"status": "error", "had_errors": True, "error": "nifty_derivatives_v1: only 0 usable observation(s)"},
    {"status": "error", "error": "boom"},  # the bare-status convention is failed too
])
def test_a_recipe_failure_fails_the_run_instead_of_recording_completed(monkeypatch, summary):
    monkeypatch.setattr(index_jobs, "run_forecast_engine_refresh_job", lambda config, *, time_budget_s: summary)
    with pytest.raises(JobRunHadErrorsError):
        index_jobs.dispatch_index_job_sync(_job())


@pytest.mark.unit
def test_run_job_forwards_config_to_the_one_refresh_function(monkeypatch):
    seen = {}

    def _refresh(**kw):
        seen.update(kw)
        return {"status": "ok", "had_errors": False}

    monkeypatch.setattr("trade_integrations.forecast_engine.refresh.refresh_all_recipes", _refresh)

    index_jobs.run_forecast_engine_refresh_job({"ticker": "NIFTY", "horizon": 3, "skip_evaluation": True}, time_budget_s=99.0)

    assert seen["ticker"] == "NIFTY" and seen["horizon"] == 3 and seen["skip_evaluation"] is True
    assert seen["time_budget_s"] == 99.0


@pytest.mark.unit
@pytest.mark.parametrize(("profile", "blocked"), [("dev", True), ("", True), ("release", False)])
def test_the_executor_gate_blocks_it_outside_release(monkeypatch, profile, blocked):
    from src.scheduled_research.executor import _collection_dispatch_blocked

    monkeypatch.setenv("STACK_PROFILE", profile)
    assert _collection_dispatch_blocked(_job()) is blocked


@pytest.mark.unit
def test_registering_the_defaults_twice_keeps_exactly_one_refresh_job(tmp_path):
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)
    index_jobs.register_default_index_jobs(store)
    assert [j.id for j in store.list_jobs() if j.config.get("job_type") == TYPE] == ["nifty-forecast-engine-refresh"]
