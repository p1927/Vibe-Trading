"""Executor for persisted scheduled research jobs.

The executor polls :class:`ScheduledResearchJobStore`, dispatches due jobs via
an injected async callable, and persists lifecycle/next-run updates after each
attempt. Schedule math is intentionally pure and clock-injected so tests can
exercise it without sleeping or reading wall-clock time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from src.config.accessor import get_env_config
from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_MS = 60 * 1000
DEFAULT_STARTUP_GRACE_MS = 30 * 1000
DEFAULT_STALE_RUNNING_MS = 45 * 60 * 1000
DEFAULT_INDEX_PLAN_REFRESH_STALE_MS = 10 * 60 * 1000
DEFAULT_DISPATCH_TIMEOUT_MS = DEFAULT_STALE_RUNNING_MS
DEFAULT_WATCHDOG_INTERVAL_MS = 60 * 1000
DEFAULT_FAILURE_THRESHOLD = 3
STARTUP_GRACE_ENV = "SCHEDULED_RESEARCH_STARTUP_GRACE_MS"
STALE_RUNNING_ENV = "SCHEDULED_RESEARCH_STALE_RUNNING_MS"
INDEX_PLAN_REFRESH_STALE_ENV = "INDEX_PLAN_REFRESH_STALE_MS"
DISPATCH_TIMEOUT_ENV = "SCHEDULED_RESEARCH_DISPATCH_TIMEOUT_MS"
WATCHDOG_INTERVAL_ENV = "SCHEDULED_RESEARCH_WATCHDOG_INTERVAL_MS"
FAILURE_THRESHOLD_ENV = "SCHEDULED_RESEARCH_FAILURE_THRESHOLD"
SCHEDULER_ENABLED_ENV = "VIBE_TRADING_ENABLE_SCHEDULER"
LAST_RESULT_CONFIG_KEY = "_last_result_summary"
_RECOVERY_ERROR_MARKERS = (
    "recovered on stack boot",
    "recovered on shutdown",
)

_JOB_DISPATCH_TIMEOUT_MS: dict[str, int] = {
    "index_plan_refresh": 10 * 60 * 1000,
    "hub_news_entity": 20 * 60 * 1000,
    "hub_news_ingest": 10 * 60 * 1000,
}
_INDEX_JOB_DISPATCH_TIMEOUT_MS = 30 * 60 * 1000


NowFn = Callable[[], int]
DispatchCallback = Callable[[ScheduledResearchJob], Awaitable[None]]

_TRUE_VALUES = {"1", "true", "yes", "on"}
# Search by day, not by minute, so an impossible date (e.g. Feb 31) fails fast
# instead of scanning years of minutes on the event loop. Four years covers any
# real recurrence, including a Feb-29 leap day.
_CRON_SEARCH_LIMIT_DAYS = 4 * 366 + 1
_CRON_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _now_ms() -> int:
    """Return current wall-clock time in epoch milliseconds."""
    return int(time.time() * 1000)


def _startup_grace_ms() -> int:
    """Delay before the first executor tick so the API can serve health checks."""
    raw = os.getenv(STARTUP_GRACE_ENV, str(DEFAULT_STARTUP_GRACE_MS)).strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_STARTUP_GRACE_MS


def _stale_running_ms() -> int:
    raw = os.getenv(STALE_RUNNING_ENV, str(DEFAULT_STALE_RUNNING_MS)).strip()
    try:
        return max(60_000, int(raw))
    except ValueError:
        return DEFAULT_STALE_RUNNING_MS


def _index_plan_refresh_stale_ms() -> int:
    raw = os.getenv(INDEX_PLAN_REFRESH_STALE_ENV, str(DEFAULT_INDEX_PLAN_REFRESH_STALE_MS)).strip()
    try:
        return max(60_000, int(raw))
    except ValueError:
        return DEFAULT_INDEX_PLAN_REFRESH_STALE_MS


def stale_running_ms_for(job: ScheduledResearchJob) -> int:
    """Return stale threshold for *job* (poll jobs use a shorter window)."""
    job_type = str(job.config.get("job_type") or "")
    if job_type == "index_plan_refresh":
        return _index_plan_refresh_stale_ms()
    return _stale_running_ms()


def is_job_stale_running(job: ScheduledResearchJob, now_ms: int) -> bool:
    """Return whether a RUNNING job has exceeded its stale threshold."""
    if job.status != JobStatus.RUNNING:
        return False
    started_at = job.last_run_at if job.last_run_at is not None else job.created_at
    return now_ms - started_at >= stale_running_ms_for(job)


def dispatch_timeout_ms_for(job: ScheduledResearchJob) -> int:
    """Return dispatch timeout for *job* (config override, then job_type, then env default)."""
    raw = job.config.get("dispatch_timeout_ms")
    if isinstance(raw, int) and raw > 0:
        return raw
    job_type = str(job.config.get("job_type") or "")
    if job_type in _JOB_DISPATCH_TIMEOUT_MS:
        return _JOB_DISPATCH_TIMEOUT_MS[job_type]
    if job_type.startswith("index_"):
        return _INDEX_JOB_DISPATCH_TIMEOUT_MS
    raw_env = os.getenv(DISPATCH_TIMEOUT_ENV, str(DEFAULT_DISPATCH_TIMEOUT_MS)).strip()
    try:
        return max(60_000, int(raw_env))
    except ValueError:
        return DEFAULT_DISPATCH_TIMEOUT_MS


def _request_pipeline_cancel_on_dispatch_timeout(job_id: str, job_type: str) -> None:
    """Cooperatively stop in-flight index pipeline work after executor timeout."""
    if not (job_type.startswith("index_") or job_type in _JOB_DISPATCH_TIMEOUT_MS):
        return
    try:
        from trade_integrations.dataflows.index_research.pipeline_cancel import request_pipeline_cancel
    except ImportError:
        return
    request_pipeline_cancel(f"dispatch_timeout:{job_id}")


def _watchdog_interval_ms() -> int:
    raw = os.getenv(WATCHDOG_INTERVAL_ENV, str(DEFAULT_WATCHDOG_INTERVAL_MS)).strip()
    try:
        return max(10_000, int(raw))
    except ValueError:
        return DEFAULT_WATCHDOG_INTERVAL_MS


def _default_failure_threshold() -> int:
    raw = os.getenv(FAILURE_THRESHOLD_ENV, str(DEFAULT_FAILURE_THRESHOLD)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_FAILURE_THRESHOLD


def _failure_threshold_for(job: ScheduledResearchJob) -> int:
    raw = job.config.get("failure_threshold")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _default_failure_threshold()


def _truncate_error(exc: BaseException) -> str:
    return str(exc)[:500]


def scheduler_enabled_from_env(value: str | None = None) -> bool:
    """Return whether the scheduled-research executor should run.

    The feature is disabled by default. Pass *value* in tests to avoid mutating
    process environment.
    """
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    return get_env_config().agent_tuning.vibe_trading_enable_scheduler


def is_due(job: ScheduledResearchJob, now_ms: int) -> bool:
    """Return whether *job* should fire at ``now_ms``.

    Cancelled and failed jobs are terminal and never re-dispatched; a failed
    job in particular keeps its old ``next_run_at`` (advancement may itself be
    what failed), so excluding it here prevents a re-dispatch loop every tick.
    Already-running jobs are left alone during live polling. Executor startup
    recovers stale persisted ``RUNNING`` jobs separately.
    """
    if job.status in {JobStatus.CANCELLED, JobStatus.RUNNING, JobStatus.FAILED}:
        return False
    return job.next_run_at <= now_ms


def next_due(schedule: str, after_ms: int) -> int:
    """Return the first due epoch-ms strictly after ``after_ms``.

    Supports the scheduled-research schedule format: a bare positive integer
    string for interval milliseconds, or a simplified 5-field cron expression
    interpreted in UTC.
    """
    validate_schedule(schedule)
    spec = schedule.strip()
    if spec.isdigit():
        return after_ms + int(spec)
    return _next_cron_due(spec, after_ms)


def _next_cron_due(schedule: str, after_ms: int) -> int:
    minutes, hours, doms, months, dows = (
        _parse_cron_field(part, low, high) for part, (low, high) in zip(schedule.split(), _CRON_BOUNDS)
    )
    start = datetime.fromtimestamp(after_ms / 1000.0, timezone.utc) + timedelta(milliseconds=1)
    # Round up to the next whole minute; cron has minute resolution.
    if start.second or start.microsecond:
        start = (start + timedelta(minutes=1)).replace(second=0, microsecond=0)

    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(_CRON_SEARCH_LIMIT_DAYS):
        candidate_day = day + timedelta(days=offset)
        if not _day_matches(candidate_day, doms, months, dows):
            continue
        for hour in sorted(hours) if hours is not None else range(24):
            for minute in sorted(minutes) if minutes is not None else range(60):
                fire = candidate_day.replace(hour=hour, minute=minute)
                if fire >= start:
                    return int(fire.timestamp() * 1000)
    raise ValueError(f"cron schedule has no matching time within search window: {schedule!r}")


def _parse_cron_field(part: str, low: int, high: int) -> set[int] | None:
    if part == "*":
        return None
    if part.startswith("*/"):
        step = int(part[2:])
        return set(range(low, high + 1, step))
    values: set[int] = set()
    for segment in part.split(","):
        if "-" in segment:
            start_s, end_s = segment.split("-", 1)
            start, end = int(start_s), int(end_s)
            values.update(range(start, end + 1))
        else:
            values.add(int(segment))
    return values


def _day_matches(dt: datetime, doms: set[int] | None, months: set[int] | None, dows: set[int] | None) -> bool:
    cron_day_of_week = (dt.weekday() + 1) % 7  # cron convention: Sunday == 0
    return (
        (doms is None or dt.day in doms)
        and (months is None or dt.month in months)
        and (dows is None or cron_day_of_week in dows)
    )


class ScheduledResearchExecutor:
    """Background poller that dispatches due scheduled research jobs."""

    def __init__(
        self,
        store: ScheduledResearchJobStore,
        dispatch: DispatchCallback,
        *,
        tick_interval_ms: int = DEFAULT_TICK_INTERVAL_MS,
        now_fn: NowFn = _now_ms,
        enabled: bool = True,
    ) -> None:
        """Initialize the executor.

        Args:
            store: Durable scheduled job store.
            dispatch: Async callable invoked once for each due job.
            tick_interval_ms: Poll interval for the background loop.
            now_fn: Injectable wall-clock source returning epoch milliseconds.
            enabled: When false, :meth:`start` and :meth:`stop` are no-ops.
        """
        self._store = store
        self._dispatch = dispatch
        self._tick_interval_ms = tick_interval_ms
        self._now_fn = now_fn
        self._enabled = enabled
        self._task: asyncio.Task | None = None
        self._wakeup: asyncio.Event | None = None
        self._stopping = False
        self._recovered_stale_running = False
        self._startup_backlog_deferred = False
        self._watchdog_task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        """Return whether the background loop task is active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the background loop.

        Idempotent. When disabled, this is a no-op.
        """
        if not self._enabled or self.is_running:
            return
        self._stopping = False
        self.recover_stale_running(self._now_fn(), startup=True)
        self._wakeup = asyncio.Event()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="scheduled-research-executor")
        self._watchdog_task = loop.create_task(
            self._stale_watchdog(),
            name="scheduled-research-stale-watchdog",
        )

    def wake(self) -> None:
        """Wake the executor loop for an immediate tick (e.g. after manual job recovery)."""
        if self._wakeup is not None:
            self._wakeup.set()

    async def stop(self) -> None:
        """Stop the background loop and wait for it to finish.

        Idempotent. When disabled or not started, this is a no-op.
        """
        if not self._enabled:
            return
        logger.info("scheduled research executor stopping…")
        self._stopping = True
        self.recover_all_running_on_shutdown(self._now_fn())
        watchdog = self._watchdog_task
        if watchdog is not None:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
        task = self._task
        if task is None:
            self._reset_runtime_state()
            return
        if self._wakeup is not None:
            self._wakeup.set()
        # The set() above wakes a sleeping loop in the common case. Cancel as a
        # fallback so shutdown never blocks for a full tick if the wakeup raced
        # the loop's sleep, then await the task to let it unwind cleanly.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._reset_runtime_state()
        logger.info("scheduled research executor stopped")

    def recover_all_running_on_shutdown(self, now_ms: int | None = None) -> int:
        """Reset every RUNNING job to pending for clean executor shutdown."""
        now = self._now_fn() if now_ms is None else now_ms
        jobs = self._store.load()
        recovered = 0
        for job in jobs.values():
            if job.status != JobStatus.RUNNING:
                continue
            _advance = job
            _advance.status = JobStatus.PENDING
            try:
                _advance.next_run_at = next_due(job.schedule, now)
            except Exception:
                logger.warning(
                    "could not advance schedule for shutdown-recovered job %s; deferring one tick",
                    job.id,
                    exc_info=True,
                )
                _advance.next_run_at = now + self._tick_interval_ms
            if not _advance.last_error:
                _advance.last_error = "recovered on executor shutdown"
            recovered += 1
            logger.warning(
                "recovering scheduled research job %s on executor shutdown (next_run_at=%s)",
                job.id,
                _advance.next_run_at,
            )
        if recovered:
            self._store.save(jobs)
        return recovered

    def _reset_runtime_state(self) -> None:
        """Clear in-memory executor flags so the next start is fresh."""
        self._stopping = False
        self._recovered_stale_running = False
        self._startup_backlog_deferred = False
        if self._wakeup is not None:
            self._wakeup.set()

    async def tick(self, now_ms: int | None = None) -> None:
        """Run one poll/dispatch pass.

        Args:
            now_ms: Optional explicit reference time. Defaults to ``now_fn``.
        """
        now = self._now_fn() if now_ms is None else now_ms
        self.recover_stale_running(now, startup=True)
        self.recover_stale_running(now, startup=False)
        jobs = sorted(
            (job for job in self._store.load().values() if is_due(job, now)),
            key=lambda job: job.next_run_at,
        )
        for job in jobs:
            await self._run_job(job, now)

    def recover_stale_running(self, now_ms: int | None = None, *, startup: bool = False) -> int:
        """Reset jobs left ``RUNNING`` after a crash or hung dispatch.

        On startup (``startup=True``), recover every ``RUNNING`` job once per
        executor instance. On each tick (``startup=False``), recover only jobs
        whose ``last_run_at`` exceeds :func:`stale_running_ms_for`.

        Recovered jobs are scheduled for their next cron/interval slot instead
        of firing immediately.
        """
        if startup and self._recovered_stale_running:
            return 0

        now = self._now_fn() if now_ms is None else now_ms
        jobs = self._store.load()
        recovered = 0
        for job in jobs.values():
            if job.status != JobStatus.RUNNING:
                continue
            if not startup and not is_job_stale_running(job, now):
                continue
            job.status = JobStatus.PENDING
            try:
                job.next_run_at = next_due(job.schedule, now)
            except Exception:
                logger.warning(
                    "could not advance schedule for recovered job %s; deferring one tick",
                    job.id,
                    exc_info=True,
                )
                job.next_run_at = now + self._tick_interval_ms
            recovered += 1
            logger.warning(
                "recovering stale scheduled research job %s from running to pending (next_run_at=%s)",
                job.id,
                job.next_run_at,
            )

        if recovered:
            self._store.save(jobs)
        if startup:
            self._recovered_stale_running = True
        return recovered

    def defer_startup_backlog(self, now_ms: int | None = None) -> int:
        """Push overdue pending jobs to their next schedule slot (once per process).

        After a crash or long downtime many cron jobs share ``next_run_at`` in
        the past. The first executor tick would otherwise run them back-to-back
        and spike memory in the Vibe API process.
        """
        if self._startup_backlog_deferred:
            return 0

        now = self._now_fn() if now_ms is None else now_ms
        jobs = self._store.load()
        deferred = 0
        for job in jobs.values():
            if job.status != JobStatus.PENDING:
                continue
            if job.next_run_at > now:
                continue
            try:
                job.next_run_at = next_due(job.schedule, now)
            except Exception:
                logger.warning(
                    "could not defer overdue job %s on startup; delaying one tick",
                    job.id,
                    exc_info=True,
                )
                job.next_run_at = now + self._tick_interval_ms
            deferred += 1
            logger.info(
                "deferring overdue scheduled job %s on startup (next_run_at=%s)",
                job.id,
                job.next_run_at,
            )

        if deferred:
            self._store.save(jobs)
        self._startup_backlog_deferred = True
        return deferred

    async def _run(self) -> None:
        grace_ms = _startup_grace_ms()
        if grace_ms > 0:
            logger.info(
                "scheduled research executor waiting %ss before first tick",
                grace_ms / 1000.0,
            )
            await self._sleep_or_wake(grace_ms)
        self.defer_startup_backlog(self._now_fn())
        while not self._stopping:
            try:
                await self.tick(self._now_fn())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("scheduled research executor tick failed", exc_info=True)
            if self._stopping:
                break
            await self._sleep_or_wake(self._tick_interval_ms)

    async def _stale_watchdog(self) -> None:
        """Recover hung RUNNING jobs on a timer independent of tick completion."""
        interval_ms = _watchdog_interval_ms()
        while not self._stopping:
            try:
                await asyncio.sleep(interval_ms / 1000.0)
            except asyncio.CancelledError:
                raise
            if self._stopping:
                break
            try:
                recovered = self.recover_stale_running(self._now_fn(), startup=False)
                if recovered:
                    self.wake()
            except Exception:
                logger.error("scheduled research stale watchdog failed", exc_info=True)

    async def _sleep_or_wake(self, sleep_ms: int) -> None:
        wakeup = self._wakeup
        if wakeup is None:
            await asyncio.sleep(sleep_ms / 1000.0)
            return
        # Re-check after re-entering: if stop() flipped _stopping and set the
        # event between the loop's check and here, return at once rather than
        # clearing the wakeup and blocking for a full tick on shutdown.
        if self._stopping:
            return
        wakeup.clear()
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=sleep_ms / 1000.0)
        except asyncio.TimeoutError:
            pass

    async def _run_job(self, job: ScheduledResearchJob, now_ms: int) -> None:
        # The tick snapshot may be stale by the time we reach this job (an
        # earlier dispatch was awaited). Re-read and confirm identity before
        # marking it RUNNING so a job the user deleted or replaced in the
        # meantime is not resurrected or dispatched.
        current = self._store.get(job.id)
        if current is None or not self._same_record(current, job) or not is_due(current, now_ms):
            return
        job = current

        if job.last_error and any(marker in job.last_error for marker in _RECOVERY_ERROR_MARKERS):
            job.last_error = None

        job.status = JobStatus.RUNNING
        self._store.upsert(job)

        timeout_ms = dispatch_timeout_ms_for(job)
        final_status = JobStatus.COMPLETED
        job_type = str(job.config.get("job_type") or "unknown")
        from src.api.runtime_activity import finish_named_task, register_named_task

        task_id = register_named_task(f"scheduled:{job.id}:{job_type}")
        logger.info(
            "scheduled research dispatch start job=%s type=%s timeout_ms=%s",
            job.id,
            job_type,
            timeout_ms,
        )
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._dispatch(job), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            logger.error(
                "scheduled research dispatch timed out for job %s after %sms",
                job.id,
                timeout_ms,
            )
            _request_pipeline_cancel_on_dispatch_timeout(job.id, job_type)
            job.config["_timed_out"] = True
            job.last_error = f"dispatch timed out after {timeout_ms}ms"
            job.consecutive_failures = int(job.consecutive_failures or 0) + 1
            final_status = JobStatus.COMPLETED
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                from trade_integrations.dataflows.index_research.pipeline_cancel import (
                    PipelineCancelledError,
                )
            except ImportError:
                PipelineCancelledError = None  # type: ignore[misc, assignment]
            if PipelineCancelledError is not None and isinstance(exc, PipelineCancelledError):
                logger.warning(
                    "scheduled research dispatch cancelled for job %s: %s",
                    job.id,
                    exc.reason,
                )
                job.last_error = f"cancelled: {exc.reason}"
                job.consecutive_failures = 0
                final_status = JobStatus.COMPLETED
            else:
                logger.error("scheduled research dispatch failed for job %s", job.id, exc_info=True)
                job.last_error = _truncate_error(exc)
                job.consecutive_failures = int(job.consecutive_failures or 0) + 1
                threshold = _failure_threshold_for(job)
                if job.consecutive_failures >= threshold:
                    final_status = JobStatus.FAILED
                else:
                    final_status = JobStatus.COMPLETED
                    logger.warning(
                        "scheduled job %s failed (%s/%s); keeping schedule alive",
                        job.id,
                        job.consecutive_failures,
                        threshold,
                    )
        else:
            job.consecutive_failures = 0
            job.last_error = None
            job.config.pop("_timed_out", None)
            raw_summary = job.config.pop(LAST_RESULT_CONFIG_KEY, None)
            if isinstance(raw_summary, dict):
                job.last_result_summary = raw_summary
            final_status = JobStatus.COMPLETED

        finally:
            finish_named_task(task_id)
            logger.info(
                "scheduled research dispatch done job=%s type=%s status=%s (%.1fs)",
                job.id,
                job_type,
                final_status.value,
                time.monotonic() - started,
            )

        job.last_run_at = now_ms
        try:
            job.next_run_at = next_due(job.schedule, now_ms)
        except Exception as exc:
            logger.error("scheduled research schedule advancement failed for job %s", job.id, exc_info=True)
            job.status = JobStatus.FAILED
            job.last_error = _truncate_error(exc)
            self._persist_completion(job)
            return

        job.status = final_status
        self._persist_completion(job)

    @staticmethod
    def _same_record(current: ScheduledResearchJob, job: ScheduledResearchJob) -> bool:
        """Return whether *current* is the same scheduled run we started.

        ``created_at`` is assigned once at creation, so a replacement POST for
        the same id (which the API stamps with a fresh ``created_at``) is
        distinguishable even when the schedule is unchanged.
        """
        return current.id == job.id and current.created_at == job.created_at

    def _persist_completion(self, job: ScheduledResearchJob) -> None:
        """Write a finished job back, unless it was changed during dispatch.

        Dispatch is awaited, so a concurrent DELETE or POST for the same id can
        land while a run is in flight. Reload first: if the record is gone the
        user cancelled it (do not resurrect), and if it is a different record
        (replaced via POST) let the new definition own its lifecycle. Only
        persist our completion when it still refers to the same scheduled run.
        """
        if self._stopping:
            logger.info(
                "scheduled research job %s finished during executor shutdown; skipping completion write",
                job.id,
            )
            return
        current = self._store.get(job.id)
        if current is None:
            logger.info("scheduled research job %s deleted during dispatch; skipping completion write", job.id)
            return
        if not self._same_record(current, job):
            logger.info("scheduled research job %s replaced during dispatch; skipping completion write", job.id)
            return
        if current.status != JobStatus.RUNNING:
            logger.info(
                "scheduled research job %s no longer running (status=%s); skipping completion write",
                job.id,
                current.status.value,
            )
            return
        self._store.upsert(job)
