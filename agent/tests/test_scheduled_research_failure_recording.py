"""Every failed scheduled run must leave `failure_kind` and `consecutive_failures` set.

D32's failed-jobs check (`.claude/check_dev_ports.py`) reads only those two fields. Regression for
.claude/backlog/items/2026-09-11-job-errors-recorded-as-success.md. Four paths recorded a failed
run as success, or not at all:

(a) stale-watchdog recovery reset the job to PENDING with no failure. The real timeout's own
    failure write was then refused, because the record was no longer RUNNING. Two job types also
    had a watchdog window shorter than their dispatch timeout.
(b) a resume kept an old `last_error`, and the next run's start did not clear it.
(c) shutdown/boot recovery marked no failure, and wrote its note only when `last_error` was empty.
(d) the eval handlers returned `had_errors: True` instead of raising, so the run was `completed`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from src.scheduled_research import dst_eval_jobs, index_jobs
from src.scheduled_research.executor import ScheduledResearchExecutor
from src.scheduled_research.lifecycle import recover_persisted_scheduler_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.pause_control import set_job_enabled
from src.scheduled_research.run_outcome import JobRunHadErrorsError
from src.scheduled_research.staleness import dispatch_timeout_ms_for, stale_running_ms_for
from src.scheduled_research.store import ScheduledResearchJobStore

OLD_ERROR = "MlflowException: Detected out-of-date database schema (found version b7e2c1a4d9f3)"


@pytest.fixture(autouse=True)
def _isolated_cancel_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The dispatchers bind a job-scoped cancel flag under TRADE_STACK_ROOT/log; keep it in tmp.
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


def _job(job_id: str = "job-1", **fields) -> ScheduledResearchJob:
    fields.setdefault("status", JobStatus.PENDING)
    fields.setdefault("schedule", "1000")
    fields.setdefault("next_run_at", 0)
    fields.setdefault("config", {})
    return ScheduledResearchJob(id=job_id, prompt="p", created_at=0, **fields)


async def _ok(job: ScheduledResearchJob) -> None:
    return None


def _assert_recorded_failure(saved: ScheduledResearchJob, *, streak: int, error_contains: str) -> None:
    assert saved.status == JobStatus.PENDING
    assert saved.failure_kind == "dispatch"
    assert saved.consecutive_failures == streak
    assert error_contains in (saved.last_error or "")
    assert OLD_ERROR not in (saved.last_error or ""), "an older run's error survived"


# --- (a) stale watchdog -------------------------------------------------------------------------


def test_stale_watchdog_recovery_records_the_run_as_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _job(status=JobStatus.RUNNING, last_run_at=1000, last_error=OLD_ERROR,
               config={"job_type": "index_calibration"})
    store.upsert(job)
    executor = ScheduledResearchExecutor(store, _ok)

    now = 1000 + stale_running_ms_for(job) + 1
    assert executor.recover_stale_running(now) == 1
    _assert_recorded_failure(store.get("job-1"), streak=1, error_contains="stale")


def test_startup_recovery_records_the_interrupted_run_as_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(status=JobStatus.RUNNING, last_run_at=1000, consecutive_failures=1,
                      failure_kind="dispatch", last_error=OLD_ERROR))
    executor = ScheduledResearchExecutor(store, _ok)

    assert executor.recover_stale_running(2000, startup=True) == 1
    _assert_recorded_failure(store.get("job-1"), streak=2, error_contains="executor start")


@pytest.mark.parametrize(
    ("job_type", "schedule"),
    [("index_plan_refresh", "60000"), ("autonomous_agent_watch", "60000"), ("hub_news_ingest", "1000")],
)
def test_watchdog_never_fires_before_the_dispatch_timeout(job_type: str, schedule: str) -> None:
    """Both short-window types used to return early, unfloored. A 60 s watch got a 120 s stale
    window against its 5-minute timeout, so the watchdog recovered it mid-run."""
    job = _job(schedule=schedule, config={"job_type": job_type})
    assert stale_running_ms_for(job) > dispatch_timeout_ms_for(job)


# --- (b) stale last_error -----------------------------------------------------------------------


def test_a_resumed_jobs_old_error_does_not_survive_into_its_next_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(consecutive_failures=3, failure_kind="dispatch", last_error=OLD_ERROR,
                      paused=True, auto_paused_reason="auto-paused: 3 consecutive dispatch failures"))
    set_job_enabled("job-1", True, store=store)
    # Resume keeps the error as history (deliberate; see test_scheduled_research_lifecycle).
    assert store.get("job-1").last_error == OLD_ERROR
    assert store.get("job-1").failure_kind is None

    seen_while_running: list[str | None] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        seen_while_running.append(store.get(job.id).last_error)

    asyncio.run(ScheduledResearchExecutor(store, dispatch).tick(1500))

    assert seen_while_running == [None], "the resumed job's old error was written back with RUNNING"
    saved = store.get("job-1")
    assert saved.status == JobStatus.COMPLETED
    assert saved.last_error is None and saved.failure_kind is None and saved.consecutive_failures == 0


def test_a_live_failures_error_stays_visible_while_the_retry_runs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(consecutive_failures=1, failure_kind="dispatch",
                      last_error="TimeoutError: dispatch timed out after 1800000ms"))
    seen: list[str | None] = []

    async def dispatch(job: ScheduledResearchJob) -> None:
        seen.append(store.get(job.id).last_error)

    asyncio.run(ScheduledResearchExecutor(store, dispatch).tick(1500))
    assert seen == ["TimeoutError: dispatch timed out after 1800000ms"]
    assert store.get("job-1").last_error is None, "a successful run must clear last_error"


# --- (c) shutdown / boot recovery ---------------------------------------------------------------


def test_executor_shutdown_recovery_records_a_failure_over_an_older_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job(status=JobStatus.RUNNING, next_run_at=10, last_error=OLD_ERROR))
    executor = ScheduledResearchExecutor(store, _ok)

    assert executor.recover_all_running_on_shutdown(5000) == 1
    _assert_recorded_failure(store.get("job-1"), streak=1, error_contains="recovered on executor shutdown")


@pytest.mark.parametrize(
    ("mode", "reason"),
    [("all_running", "recovered on stack shutdown"), ("stale", "recovered on stack boot (stale running)")],
)
def test_stack_recovery_records_a_failure_over_an_older_error(tmp_path: Path, mode: str, reason: str) -> None:
    store = _store(tmp_path)
    store.upsert(_job(status=JobStatus.RUNNING, last_run_at=1, last_error=OLD_ERROR))

    assert recover_persisted_scheduler_jobs(store, mode=mode, reason=reason, auto_pause=True) == 1
    _assert_recorded_failure(store.get("job-1"), streak=1, error_contains=reason)


# --- (d) handler summaries that report had_errors -----------------------------------------------


def test_news_eval_had_errors_is_a_failed_run_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """The exact release shape: the handler swallows the MLflow error into its summary."""
    monkeypatch.setattr(
        index_jobs, "run_news_quality_eval_job",
        lambda config: {"status": "error", "error": "No Experiment with id=2 exists", "had_errors": True},
    )
    store = _store(tmp_path)
    store.upsert(_job("nifty-news-quality-eval", config={"job_type": "news_quality_eval"}))

    async def dispatch(job: ScheduledResearchJob) -> None:
        index_jobs.dispatch_index_job_sync(job)

    asyncio.run(ScheduledResearchExecutor(store, dispatch).tick(1500))

    saved = store.get("nifty-news-quality-eval")
    assert saved.status == JobStatus.PENDING
    assert saved.failure_kind == "dispatch"
    assert saved.consecutive_failures == 1
    assert "No Experiment with id=2 exists" in saved.last_error
    assert "JobRunHadErrorsError" in saved.last_error
    # The failing run's own summary is kept, not the previous run's.
    assert (saved.last_result_summary or {}).get("status") == "error"


def test_news_eval_partial_or_ok_summary_is_not_a_failure(monkeypatch) -> None:
    for summary in ({"status": "ok"}, {"status": "partial", "skipped_case_count": 2}):
        monkeypatch.setattr(index_jobs, "run_news_quality_eval_job", lambda config, s=summary: s)
        index_jobs.dispatch_index_job_sync(_job(config={"job_type": "news_quality_eval"}))


def test_dst_eval_had_errors_raises_with_each_sub_evals_error(monkeypatch) -> None:
    monkeypatch.setattr(
        dst_eval_jobs, "run_index_research_eval_job",
        lambda config: {
            "status": "error",
            "had_errors": True,
            "results": {
                "extractor": {"status": "error", "error": "Detected out-of-date database schema"},
                "prediction_ledger": {"status": "ok"},
            },
        },
    )
    job = _job("dst-eval-index-research", config={"job_type": "index_research_eval"})
    with pytest.raises(JobRunHadErrorsError, match="extractor: Detected out-of-date database schema"):
        dst_eval_jobs.dispatch_dst_eval_job_sync(job)
    summary = job.config["_last_result_summary"]
    assert summary["had_errors"] is True
    assert summary["results"]["extractor"]["status"] == "error"
    assert summary["results"]["prediction_ledger"] == {"status": "ok"}


def test_dst_eval_ok_summary_is_not_a_failure(monkeypatch) -> None:
    monkeypatch.setattr(dst_eval_jobs, "run_recorder_dst_job", lambda config: {"status": "ok", "had_errors": False})
    job = _job("dst-eval-recorder-dst", config={"job_type": "recorder_dst"})
    dst_eval_jobs.dispatch_dst_eval_job_sync(job)
    assert job.config["_last_result_summary"]["status"] == "ok"
