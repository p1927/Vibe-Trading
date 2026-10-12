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


# --- degraded vs failed, end to end through the real refresh and the real executor (D402) -------------


def _hub_helpers():
    """The Trade test module that seeds a real temp hub (factor store, NIFTY cold tier); one seeding
    implementation, not a copy. Only the forecaster is stubbed below."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "tests" / "test_forecast_engine_refresh.py"
    if not path.is_file():
        pytest.skip("Trade's tests/test_forecast_engine_refresh.py is not next to this submodule checkout")
    spec = importlib.util.spec_from_file_location("trade_forecast_engine_refresh_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_refresh_on(monkeypatch, tmp_path, *, young: tuple[str, ...], engine=None):
    """Point the job body's refresh at a hermetic hub where the `young` recipes are too young to evaluate."""
    from datetime import date
    from functools import partial

    import trade_integrations.forecast_engine.refresh as refresh_module

    h = _hub_helpers()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path / "hub"))
    monkeypatch.delenv("TIMESCALE_ENABLED", raising=False)
    monkeypatch.delenv("TIMESCALE_DATABASE_URL", raising=False)
    sessions = h._sessions(140, date.today())  # the job asks for lookback_days=90
    client = h._seed(tmp_path / "hub", sessions=sessions, skip=h._young(young, sessions))
    real = refresh_module.refresh_all_recipes
    monkeypatch.setattr(
        refresh_module, "refresh_all_recipes",
        partial(real, engine=engine or h._DriftEngine(), checkpoint="test-engine", client=client,
                lookback_days=30, eval_window_days=40, eval_lookback_days=5),
    )
    return h


def _run_daily(tmp_path, monkeypatch, *, days: int, max_failures: int = 3):
    import asyncio

    from src.scheduled_research.executor import ScheduledResearchExecutor

    monkeypatch.setenv("STACK_PROFILE", "release")
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    store.upsert(_job(dispatch_timeout_ms=600_000))
    seen: list[tuple[int, str | None]] = []

    async def dispatch(job):
        index_jobs.dispatch_index_job_sync(job)

    async def scenario():
        executor = ScheduledResearchExecutor(
            store, dispatch, max_consecutive_failures=max_failures, retry_base_delay_ms=1, retry_max_delay_ms=1
        )
        for day in range(days):
            await executor.tick(100 + day * 10_000)
            job = store.get("nifty-forecast-engine-refresh")
            seen.append((job.consecutive_failures, job.failure_kind))

    asyncio.run(scenario())
    return store.get("nifty-forecast-engine-refresh"), seen


@pytest.mark.unit
@pytest.mark.parametrize("young", [("nifty_derivatives_v1",), ("nifty_derivatives_v1", "nifty_fii_dii_5d_v1")])
def test_a_recipe_not_yet_evaluable_degrades_every_daily_run_and_never_auto_pauses(tmp_path, monkeypatch, young):
    """One recipe too young + one ok, and both too young: five simulated daily runs through the real
    refresh and the real executor. Each is recorded completed (degraded, reason + ETA in the run record),
    the failure counter never moves, and the job is never auto-paused."""
    _real_refresh_on(monkeypatch, tmp_path, young=young)

    job, seen = _run_daily(tmp_path, monkeypatch, days=5, max_failures=2)

    assert seen == [(0, None)] * 5
    assert job.paused is False and job.auto_paused_reason is None and job.consecutive_failures == 0
    assert job.last_error is None and job.status == JobStatus.COMPLETED
    summary = job.last_result_summary
    assert summary["status"] == "degraded" and summary["had_errors"] is False
    assert set(summary["insufficient_data"]) == set(young)
    assert all("earliest" in text for text in summary["insufficient_data"].values())


@pytest.mark.unit
def test_a_real_exception_still_fails_each_run_and_auto_pauses_at_the_threshold(tmp_path, monkeypatch):
    class Boom:
        def forecast(self, **_kw):
            raise RuntimeError("model blew up")

    _real_refresh_on(monkeypatch, tmp_path, young=(), engine=Boom())

    job, seen = _run_daily(tmp_path, monkeypatch, days=5, max_failures=3)

    assert [n for n, _ in seen[:3]] == [1, 2, 3] and all(kind == "dispatch" for _, kind in seen[:3])
    assert job.paused is True and "auto-paused: 3 consecutive" in job.auto_paused_reason
    assert "model blew up" in job.last_error


@pytest.mark.unit
def test_a_recipe_that_never_matures_fails_the_run_and_counts(tmp_path, monkeypatch):
    """Variant: insufficient_data past its own promised date is an error (D402), so it fails and counts."""
    from datetime import date, timedelta

    from trade_integrations.forecast_engine.artifact import PartOutcome, RefreshStatus, write_refresh_status

    _real_refresh_on(monkeypatch, tmp_path, young=("nifty_derivatives_v1",))
    stale = PartOutcome("insufficient_data", "young", (date.today() - timedelta(days=30)).isoformat())
    write_refresh_status("NIFTY", RefreshStatus("nifty_derivatives_v1", "2026-01-01T00:00:00+00:00", stale, stale))

    job, seen = _run_daily(tmp_path, monkeypatch, days=1)

    assert seen == [(1, "dispatch")] and "expected evaluable by" in job.last_error
