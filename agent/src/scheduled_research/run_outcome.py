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


def record_interrupted_run(job: ScheduledResearchJob, reason: str, *, restart_artifact: bool = False) -> None:
    """Mark a run that was left RUNNING and never wrote its own outcome as a failed run.

    ``last_error`` is always overwritten. It used to be written only ``if not job.last_error``, so an
    older run's error survived into a record whose newest run failed differently, or left no trace.
    """
    job.failure_kind = RECOVERED_FAILURE_KIND
    # A run cut short by OUR restart (executor/stack shutdown, boot/startup recovery) is not the
    # job's failure: it stays visible (failure_kind + last_error) but must not stack toward
    # auto-pause. Only the mid-life stale watchdog (a real hang) counts.
    if not restart_artifact:
        job.consecutive_failures = int(job.consecutive_failures or 0) + 1
    job.last_error = reason[:_MAX_ERROR_CHARS]


#: Most per-part errors named in ``last_error``; the rest are counted, so one bad vendor day with
#: thirty failed factors still fits the 1000-char field and still says how many failed.
_MAX_ERROR_PARTS = 8


def run_error_detail(summary: Any) -> str:
    """The error text a handler summary carries, wherever it sits: every nested part holding an
    ``error``, or ``status == "error"`` with a ``reason`` (the global-macro EOD refresh's
    ``series.<name>`` and ``factors.factors.<market/key>`` parts), named by its path. One reader
    for every handler's shape, not a key per handler: the EOD refresh's two failed runs of
    2026-09-21/22 recorded only "no error detail in the summary" because this read ``results.*``
    alone (.claude/backlog/items/2026-09-23-global-macro-eod-refresh-error-detail-lost.md)."""
    parts: list[str] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if not isinstance(node, dict) or depth > 4:
            return
        text = node.get("error") or (node.get("reason") if node.get("status") == "error" else None)
        if text:
            parts.append(f"{path}: {text}" if path else str(text))
        for key, child in node.items():
            if isinstance(child, dict):
                walk(child, f"{path}.{key}" if path else str(key), depth + 1)

    walk(summary, "", 0)
    shown = "; ".join(parts[:_MAX_ERROR_PARTS])
    return shown + (f"; (+{len(parts) - _MAX_ERROR_PARTS} more)" if len(parts) > _MAX_ERROR_PARTS else "")


def raise_if_run_had_errors(job: ScheduledResearchJob, summary: Any, label: str) -> None:
    """Raise `JobRunHadErrorsError` when the handler's summary reports an error.

    Checks both ``had_errors`` (the convention most handlers use) and a bare
    ``status == "error"`` with no ``had_errors`` key at all (a second, inconsistent
    convention some handlers use instead — e.g. constituent-volume-snapshot's "no
    active broker session" / "no constituent symbols" paths). Either one means the
    run did not finish cleanly and must not be recorded ``completed``. See
    docs/DECISIONS.md D86: every handler now fails loud and visibly (this check
    always fires); whether a given job type also gets exempted from auto-pause for
    genuinely routine partial-vendor noise (the way ``hub_news_ingest``'s
    ``barren_collection`` is) stays a separate, per-branch decision.
    """
    if not isinstance(summary, dict):
        return
    if not (summary.get("had_errors") or summary.get("status") == "error"):
        return
    detail = run_error_detail(summary) or "no error detail in the summary"
    raise JobRunHadErrorsError(f"{label} for job {job.id} reported errors: {detail}")


__all__ = [
    "JobRunHadErrorsError",
    "RECOVERED_FAILURE_KIND",
    "raise_if_run_had_errors",
    "record_interrupted_run",
    "run_error_detail",
]
