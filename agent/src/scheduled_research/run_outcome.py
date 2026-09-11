"""How a scheduled run that did not finish cleanly is recorded in the job store.

Fork-only sidecar for ``executor.py`` (an upstream file), ``lifecycle.py`` and the job-type
dispatchers, per docs/FORK_CONVENTIONS.md.

**One rule.** A run that did not finish cleanly ends with ``failure_kind`` set,
``consecutive_failures`` incremented, and ``last_error`` saying why. "Did not finish cleanly" means
it raised, its handler's own summary reported ``had_errors``, it timed out, or it was left RUNNING
and then recovered by the stale watchdog, executor shutdown, or stack boot/shutdown. D32's
failed-jobs check (``.claude/check_dev_ports.py``) reads exactly ``failure_kind`` /
``consecutive_failures``. A path that left them unset was a failed run D32 could not see. Before
this, four such paths existed. See ``.claude/backlog/items/2026-09-11-job-errors-recorded-as-success.md``.

**Why recovery records ``"dispatch"`` rather than a new kind.** ``ScheduledResearchJob.from_dict``
rejects any ``failure_kind`` outside its known set, and the store quarantines the whole file when a
single record fails to load. A new kind written here would make the store unloadable for any older
pin a ``trade release update`` rollback lands on. The distinction lives in ``last_error`` instead.
"""

from __future__ import annotations

from typing import Any

from src.scheduled_research.models import ScheduledResearchJob

#: The kind a recovered (never-completed) run is recorded under. See the module docstring.
RECOVERED_FAILURE_KIND = "dispatch"

_MAX_ERROR_CHARS = 1000


class JobRunHadErrorsError(RuntimeError):
    """A job handler returned normally, but its own summary reports ``had_errors``.

    Raised so the executor's ordinary exception path records the failure: ``failure_kind``,
    ``consecutive_failures``, backoff and auto-pause. That is the pattern
    ``index_jobs``' factor-snapshot branch already used. It stops only this job, never the
    scheduler.
    """


def record_interrupted_run(job: ScheduledResearchJob, reason: str) -> None:
    """Mark a run that was left RUNNING and never wrote its own outcome as a failed run.

    ``last_error`` is always overwritten. It used to be written only ``if not job.last_error``, so an
    older run's error survived into a record whose newest run failed differently, or left no trace.
    """
    job.failure_kind = RECOVERED_FAILURE_KIND
    job.consecutive_failures = int(job.consecutive_failures or 0) + 1
    job.last_error = reason[:_MAX_ERROR_CHARS]


def run_error_detail(summary: Any) -> str:
    """The error text a handler summary carries: its top-level ``error``, plus any per-part errors."""
    if not isinstance(summary, dict):
        return ""
    parts: list[str] = []
    if summary.get("error"):
        parts.append(str(summary["error"]))
    results = summary.get("results")
    if isinstance(results, dict):
        for name, result in results.items():
            if isinstance(result, dict) and result.get("error"):
                parts.append(f"{name}: {result['error']}")
    return "; ".join(parts)


def raise_if_run_had_errors(job: ScheduledResearchJob, summary: Any, label: str) -> None:
    """Raise `JobRunHadErrorsError` when the handler's summary reports ``had_errors``."""
    if isinstance(summary, dict) and summary.get("had_errors"):
        detail = run_error_detail(summary) or "no error detail in the summary"
        raise JobRunHadErrorsError(f"{label} for job {job.id} reported errors: {detail}")


__all__ = [
    "JobRunHadErrorsError",
    "RECOVERED_FAILURE_KIND",
    "raise_if_run_had_errors",
    "record_interrupted_run",
    "run_error_detail",
]
