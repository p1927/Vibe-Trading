"""Jobs that have fallen OUT of the schedule — the ones `is_due()` cannot express.

Fork-only sidecar (docs/FORK_CONVENTIONS.md): `executor.py` is upstream-owned and
high-conflict, so this behaviour lives in a file only the fork owns and `executor.py` carries
one import plus one call.

**The gap this closes.** `ScheduledResearchExecutor.liveness()` reports `max_overdue_seconds`,
computed over jobs `is_due()` accepts. `is_due()` returns False for FAILED, EXPIRED, CANCELLED,
RUNNING and paused jobs — correct for dispatch (a failed job keeps the `next_run_at` it died on,
so re-dispatching it every tick would loop) but it meant the two states that mean *this job is
permanently dead* were exactly the two the liveness field could not see.

Measured on the real release job store, 2026-09-07: `nifty-hub-news-ingest-light` and
`-tight` sat **105 hours** past `next_run_at` with `status=failed`, while the endpoint reported
`max_overdue_seconds: 6183` against an unrelated healthy-but-slow job — and
`.claude/check_dev_ports.py` passed that reading for four days. Six jobs were stuck in total
(the two above plus four `auto-paused: recovered on stack shutdown`); `is_due()` matched none.

Reporting is deliberately separate from dispatch: nothing here makes a stuck job run again. That
stays a deliberate act (`POST /scheduled-runs/{id}/resume`), because the reason a job is terminal
may be the very thing that would loop if it were retried automatically.

See `.claude/backlog/items/2026-09-07-nothing-notices-an-overdue-scheduled-job.md` and
`.claude/backlog/items/2026-09-07-india-hub-news-ingest-dead-since-2026-09-03.md`.
"""

from __future__ import annotations

from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob

#: Statuses that make a job permanently undispatchable while it still carries a `next_run_at`.
#: CANCELLED is deliberately absent: an operator cancelling a job is a decision, not a fault.
#: RUNNING is absent too — it is already reported through `in_flight`, and a legitimately long
#: dispatch must not trip this. A RUNNING job wedged for days is a real failure but a different
#: one, with its own recovery path (`is_job_stale_running`/startup recovery).
STUCK_STATUSES = frozenset({JobStatus.FAILED, JobStatus.EXPIRED})


def is_stuck(job: ScheduledResearchJob, now_ms: int) -> bool:
    """Return whether *job* is past its `next_run_at` and can never fire again on its own.

    Deliberately NOT the exact complement of `is_due()`: `end_at` expiry and a PENDING/SENDING
    delivery row are ordinary lifecycle states, not faults, so they are not reported here.
    """
    next_run_at = job.next_run_at
    if next_run_at is None or next_run_at > now_ms:
        return False
    if job.end_at is not None and now_ms > job.end_at:
        return False  # reaching end_at is finishing its life, not silently dying
    return bool(job.paused) or job.status in STUCK_STATUSES


def stuck_row(job: ScheduledResearchJob, now_ms: int) -> dict[str, Any]:
    """One reportable row for a stuck job, worst-first sortable on `overdue_seconds`."""
    return {
        "id": str(job.id),
        "status": str(getattr(job.status, "value", job.status)),
        "paused": bool(job.paused),
        "auto_paused_reason": job.auto_paused_reason or None,
        "overdue_seconds": round((now_ms - int(job.next_run_at)) / 1000.0, 1),
        "last_error": (job.last_error or "")[:200] or None,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The two fields `liveness()` publishes, so the shape is defined in one place."""
    return {
        "stuck_jobs": sorted(rows, key=lambda row: -row["overdue_seconds"]),
        "max_stuck_seconds": max((row["overdue_seconds"] for row in rows), default=0.0),
    }


#: What `liveness()` publishes when the store could not be read. `None`, not `[]`/`0` — an
#: unreadable store means "we do not know", and an empty list would read as "nothing is stuck",
#: which is the same lie these fields exist to stop.
UNKNOWN: dict[str, Any] = {"stuck_jobs": None, "max_stuck_seconds": None}

__all__ = ["STUCK_STATUSES", "UNKNOWN", "is_stuck", "stuck_row", "summarize"]
