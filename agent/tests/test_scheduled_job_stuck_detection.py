"""`is_stuck()` and `liveness()['stuck_jobs']` — the jobs `max_overdue_seconds` cannot see.

`liveness()` computes `max_overdue_seconds` over jobs `is_due()` accepts. `is_due()` returns
False for FAILED, EXPIRED, CANCELLED, RUNNING and paused jobs — correct for dispatch (a failed
job keeps the `next_run_at` it died on, so re-dispatching it every tick would loop) but it meant
the two states that mean "this job is permanently dead" were exactly the two the liveness field
could not report.

Measured on the real release job store on 2026-09-07, which is what these tests encode:
`nifty-hub-news-ingest-light`/`-tight` sat 105 hours past `next_run_at` with `status=failed`
while the endpoint reported `max_overdue_seconds: 6183` against an unrelated healthy-but-slow
job — and `check_dev_ports.py` passed that reading for four days. Six jobs were stuck in total;
`is_due()` matched none of them.

See `.claude/backlog/items/2026-09-07-nothing-notices-an-overdue-scheduled-job.md` and
`.claude/backlog/items/2026-09-07-india-hub-news-ingest-dead-since-2026-09-03.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.scheduled_research.executor import ScheduledResearchExecutor, is_due
from src.scheduled_research.stuck_jobs import is_stuck
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

NOW = 1_000_000_000
HOUR = 3_600_000


def _job(
    job_id: str,
    *,
    next_run_at: int,
    status: JobStatus = JobStatus.PENDING,
    paused: bool = False,
    end_at: int | None = None,
    last_error: str | None = None,
) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt=f"prompt for {job_id}",
        schedule="1000",
        next_run_at=next_run_at,
        status=status,
        created_at=0,
        paused=paused,
        end_at=end_at,
        last_error=last_error,
    )


# --- the predicate ------------------------------------------------------------------

def test_terminally_failed_job_past_next_run_is_stuck() -> None:
    """The India news-ingest shape: failed, and its next_run_at frozen 105h in the past."""
    job = _job("nifty-hub-news-ingest-light", next_run_at=NOW - 105 * HOUR, status=JobStatus.FAILED)
    assert is_stuck(job, NOW) is True
    assert is_due(job, NOW) is False, "regression guard: it must still never be re-dispatched"


def test_paused_job_past_next_run_is_stuck() -> None:
    """The 'recovered on stack shutdown' shape — paused, cadence never advanced."""
    job = _job("options-plan-refresh", next_run_at=NOW - HOUR, paused=True)
    assert is_stuck(job, NOW) is True
    assert is_due(job, NOW) is False


@pytest.mark.parametrize("status", [JobStatus.FAILED, JobStatus.EXPIRED])
def test_stuck_statuses(status: JobStatus) -> None:
    assert is_stuck(_job("j", next_run_at=NOW - HOUR, status=status), NOW) is True


def test_cancelled_is_not_stuck() -> None:
    """An operator cancelling a job is a decision, not a fault to report."""
    job = _job("j", next_run_at=NOW - HOUR, status=JobStatus.CANCELLED)
    assert is_stuck(job, NOW) is False


def test_running_is_not_stuck() -> None:
    """A long dispatch is reported through `in_flight`; it must not trip this."""
    job = _job("j", next_run_at=NOW - HOUR, status=JobStatus.RUNNING)
    assert is_stuck(job, NOW) is False


def test_healthy_overdue_job_is_not_stuck() -> None:
    """An ordinary due-but-late job is `max_overdue_seconds`' business, not this field's."""
    job = _job("ru-hub-news-ingest-full", next_run_at=NOW - 2 * HOUR)
    assert is_stuck(job, NOW) is False
    assert is_due(job, NOW) is True


def test_failed_job_not_yet_due_is_not_stuck() -> None:
    job = _job("j", next_run_at=NOW + HOUR, status=JobStatus.FAILED)
    assert is_stuck(job, NOW) is False


def test_expired_by_end_at_is_not_stuck() -> None:
    """Reaching `end_at` is finishing its life, not silently dying."""
    job = _job("j", next_run_at=NOW - HOUR, status=JobStatus.FAILED, end_at=NOW - 2 * HOUR)
    assert is_stuck(job, NOW) is False


# --- the reported field -------------------------------------------------------------

def _executor(tmp_path: Path, jobs: list[ScheduledResearchJob]) -> ScheduledResearchExecutor:
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    for job in jobs:
        store.upsert(job)
    return ScheduledResearchExecutor(store=store, dispatch=None, now_fn=lambda: NOW)


def test_liveness_reports_stuck_jobs_the_overdue_field_misses(tmp_path: Path) -> None:
    """The exact 2026-09-07 reading: a modest max_overdue beside a 105h dead job."""
    ex = _executor(
        tmp_path,
        [
            _job("ru-hub-news-ingest-full", next_run_at=NOW - 2 * HOUR),
            _job(
                "nifty-hub-news-ingest-light",
                next_run_at=NOW - 105 * HOUR,
                status=JobStatus.FAILED,
                last_error="TimeoutError: dispatch timed out after 1200000ms",
            ),
        ],
    )
    live = ex.liveness(now_ms=NOW)

    # The old field still reports only the healthy-but-late job -- unchanged behaviour.
    assert live["max_overdue_job_id"] == "ru-hub-news-ingest-full"
    assert live["max_overdue_seconds"] == pytest.approx(2 * HOUR / 1000.0)

    # The new field is the one that sees the dead one.
    assert [row["id"] for row in live["stuck_jobs"]] == ["nifty-hub-news-ingest-light"]
    assert live["max_stuck_seconds"] == pytest.approx(105 * HOUR / 1000.0)
    assert live["stuck_jobs"][0]["status"] == "failed"
    assert "TimeoutError" in live["stuck_jobs"][0]["last_error"]


def test_liveness_stuck_jobs_sorted_worst_first(tmp_path: Path) -> None:
    ex = _executor(
        tmp_path,
        [
            _job("recent", next_run_at=NOW - HOUR, paused=True),
            _job("oldest", next_run_at=NOW - 105 * HOUR, status=JobStatus.FAILED),
            _job("middle", next_run_at=NOW - 10 * HOUR, paused=True),
        ],
    )
    live = ex.liveness(now_ms=NOW)
    assert [row["id"] for row in live["stuck_jobs"]] == ["oldest", "middle", "recent"]


def test_liveness_healthy_store_reports_no_stuck_jobs(tmp_path: Path) -> None:
    ex = _executor(tmp_path, [_job("fine", next_run_at=NOW + HOUR)])
    live = ex.liveness(now_ms=NOW)
    assert live["stuck_jobs"] == []
    assert live["max_stuck_seconds"] == 0.0
