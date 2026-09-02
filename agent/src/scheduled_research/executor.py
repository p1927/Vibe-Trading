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
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from src.config.accessor import get_env_config
from src.scheduled_research.models import (
    CRON_BOUNDS,
    DeliveryRecord,
    DeliveryStatus,
    JobStatus,
    ScheduledResearchJob,
    parse_cron_field,
    validate_schedule,
    validate_timezone,
)
from src.scheduled_research.job_tier_policy import collection_job_dispatch_enabled, is_collection_job
from src.scheduled_research.store import ScheduledResearchJobStore
# Stale-run detection and watchdog tuning live in staleness.py (a file we
# fully own) and are re-exported here so this module's existing internal
# call sites and external importers (lifecycle.py, index_prediction_jobs.py,
# scheduled_routes.py) are unaffected.
from src.scheduled_research.staleness import (
    DEFAULT_DISPATCH_TIMEOUT_MS,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_FRESH_REGISTRATION_DEFER_MS,
    DEFAULT_INDEX_PLAN_REFRESH_STALE_MS,
    DEFAULT_STALE_RUNNING_MS,
    DEFAULT_STARTUP_GRACE_MS,
    DEFAULT_WATCHDOG_INTERVAL_MS,
    DISPATCH_TIMEOUT_ENV,
    FAILURE_THRESHOLD_ENV,
    FRESH_REGISTRATION_DEFER_ENV,
    INDEX_PLAN_REFRESH_STALE_ENV,
    STALE_RUNNING_ENV,
    STARTUP_GRACE_ENV,
    WATCHDOG_INTERVAL_ENV,
    _autonomous_watch_target_running,
    _default_failure_threshold,
    _failure_threshold_for,
    _fresh_registration_defer_ms,
    _index_plan_refresh_stale_ms,
    _request_pipeline_cancel_on_dispatch_timeout,
    _stale_running_ms,
    _startup_grace_ms,
    _watchdog_buffer_ms,
    _watchdog_interval_ms,
    defer_fresh_registrations,
    dispatch_timeout_ms_for,
    is_job_stale_running,
    stale_running_ms_for,
)
from src.scheduled_research.verdict import (
    VerdictRecord,
    outcome_of,
    parse_verdict_section,
)

from dataclasses import replace as _dc_replace
from src.tools.redaction import redact_internal_paths, redact_text

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_MS = 60 * 1000
SCHEDULER_ENABLED_ENV = "VIBE_TRADING_ENABLE_SCHEDULER"
LAST_RESULT_CONFIG_KEY = "_last_result_summary"
_RECOVERY_ERROR_MARKERS = (
    "recovered on stack boot",
    "recovered on shutdown",
)

_MAX_PERSISTED_ERROR_CHARS = 1000

#: A send that has not returned within this long is assumed to belong to a
#: process that is no longer running.
DEFAULT_DELIVERY_LEASE_MS = 5 * 60 * 1000

NowFn = Callable[[], int]
# A dispatcher may return the session id it enqueued into. Returning None keeps
# the pre-delivery contract working unchanged.
DispatchCallback = Callable[[ScheduledResearchJob], Awaitable[Optional[str]]]
#: session_id -> (terminal status, briefing text), or None while in flight.
BriefingReader = Callable[[str], Optional[tuple[str, str]]]
#: (channel, target, text) -> delivered.
ChannelSender = Callable[[str, Optional[str], str], Awaitable[None]]

_TRUE_VALUES = {"1", "true", "yes", "on"}
# Search by day, not by minute, so an impossible date (e.g. Feb 31) fails fast
# instead of scanning years of minutes on the event loop. Four years covers any
# real recurrence, including a Feb-29 leap day; the extra headroom absorbs a
# yearly occurrence landing in a DST spring-forward gap (skipped by policy)
# several years in a row before a real instant exists again.
_CRON_SEARCH_LIMIT_DAYS = 6 * 366 + 1


def _now_ms() -> int:
    """Return current wall-clock time in epoch milliseconds."""
    return int(time.time() * 1000)


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
    recovers stale persisted ``RUNNING`` jobs separately. A paused job is
    skipped without mutating ``next_run_at``, so its original cadence resumes
    unchanged the moment it is unpaused.

    A firing whose outbox row is still PENDING or SENDING is also left alone:
    dispatch returns once the run is accepted, not once it is delivered, so a
    schedule shorter than that gap would otherwise re-dispatch onto the same
    row and overwrite it, orphaning the briefing a sweep still owes.
    """
    if job.paused:
        return False
    if job.status in {JobStatus.CANCELLED, JobStatus.RUNNING, JobStatus.FAILED}:
        return False
    if job.delivery.status in {DeliveryStatus.PENDING, DeliveryStatus.SENDING}:
        return False
    return job.next_run_at <= now_ms


def _collection_dispatch_blocked(job: ScheduledResearchJob) -> bool:
    """True if *job* is a data-collection job type and this process isn't
    ``STACK_PROFILE=release`` — see job_tier_policy.py for the full rationale (this mirrors
    stock_simulator's `_live_capture_enabled()` gate: release is the sole active collector,
    dev must not independently dispatch the same collection work). Read once per call, not
    cached, so a STACK_PROFILE change takes effect without needing a process restart."""
    job_type = str((job.config or {}).get("job_type") or "")
    if not is_collection_job(job_type):
        return False
    return not collection_job_dispatch_enabled(os.environ.get("STACK_PROFILE", "dev"))


def _persisted_error(exc: Exception) -> str:
    """Return a bounded, redaction-safe error for durable job state."""
    message = f"{type(exc).__name__}: {exc}"
    safe = redact_text(redact_internal_paths(message)).replace("\x00", "")
    if len(safe) <= _MAX_PERSISTED_ERROR_CHARS:
        return safe
    return f"{safe[: _MAX_PERSISTED_ERROR_CHARS - 3]}..."


def next_due(schedule: str, after_ms: int, tz: str | None = None) -> int:
    """Return the first due epoch-ms strictly after ``after_ms``.

    Supports the scheduled-research schedule format: a bare positive integer
    string for interval milliseconds, or a simplified 5-field cron expression.
    Cron is evaluated on the wall clock of *tz* (an IANA timezone key) when
    one is given, in UTC otherwise — the semantics every job had before the
    field existed. Interval schedules ignore *tz* entirely.
    """
    validate_schedule(schedule)
    spec = schedule.strip()
    if spec.isdigit():
        # Before the timezone check: interval schedules must keep advancing
        # even when the stored key cannot resolve on this host.
        return after_ms + int(spec)
    validate_timezone(tz)
    return _next_cron_due(spec, after_ms, tz)


def _next_cron_due(schedule: str, after_ms: int, tz: str | None = None) -> int:
    minutes, hours, doms, months, dows = (
        parse_cron_field(part, low, high) for part, (low, high) in zip(schedule.split(), CRON_BOUNDS)
    )
    zone = timezone.utc if tz is None else ZoneInfo(tz)
    # Walk candidates on the *local* calendar of ``zone`` so field matching —
    # the weekday in particular — follows the authoring wall clock. Ascending
    # wall order maps to ascending UTC order under a fixed fold policy, so
    # "strictly after" stays a plain epoch comparison.
    day = datetime.fromtimestamp(after_ms / 1000.0, zone).date()
    for offset in range(_CRON_SEARCH_LIMIT_DAYS):
        candidate_day = day + timedelta(days=offset)
        if not _day_matches(candidate_day, doms, months, dows):
            continue
        for hour in sorted(hours) if hours is not None else range(24):
            for minute in sorted(minutes) if minutes is not None else range(60):
                fire_ms = _local_wall_time_to_epoch_ms(candidate_day, hour, minute, zone)
                if fire_ms is not None and fire_ms > after_ms:
                    return fire_ms
    raise ValueError(f"cron schedule has no matching time within search window: {schedule!r}")


def _local_wall_time_to_epoch_ms(day: date, hour: int, minute: int, zone: tzinfo) -> int | None:
    """Resolve one local wall time to a UTC epoch-ms instant.

    DST policy (#953): a nonexistent wall time — the spring-forward gap —
    returns ``None`` so the occurrence is skipped; ``ZoneInfo`` would
    otherwise silently map it past the transition (PEP 495) and run it. An
    ambiguous wall time — the fall-back fold — resolves with ``fold=0``, the
    first occurrence, so it runs exactly once.
    """
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    as_utc = local.astimezone(timezone.utc)
    if as_utc.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        return None  # wall time does not exist in this zone (DST gap)
    return int(as_utc.timestamp() * 1000)


def _day_matches(dt: date, doms: set[int] | None, months: set[int] | None, dows: set[int] | None) -> bool:
    if months is not None and dt.month not in months:
        return False

    cron_day_of_week = (dt.weekday() + 1) % 7  # cron convention: Sunday == 0
    day_of_month_matches = doms is None or dt.day in doms
    day_of_week_matches = dows is None or cron_day_of_week in dows

    # Standard five-field cron treats day-of-month and day-of-week as an OR
    # when both fields are restricted. A wildcard in either field keeps the
    # other field authoritative.
    if doms is not None and dows is not None:
        return day_of_month_matches or day_of_week_matches
    return day_of_month_matches and day_of_week_matches


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
        max_consecutive_failures: int | None = None,
        retry_base_delay_ms: int | None = None,
        retry_max_delay_ms: int | None = None,
        dispatch_concurrency: int | None = None,
        briefing_reader: "BriefingReader | None" = None,
        channel_sender: "ChannelSender | None" = None,
        delivery_lease_ms: int = DEFAULT_DELIVERY_LEASE_MS,
    ) -> None:
        """Initialize the executor.

        Args:
            store: Durable scheduled job store.
            dispatch: Async callable invoked once for each due job.
            tick_interval_ms: Poll interval for the background loop.
            now_fn: Injectable wall-clock source returning epoch milliseconds.
            enabled: When false, :meth:`start` and :meth:`stop` are no-ops.
            max_consecutive_failures: Dispatch failures allowed before a job
                becomes terminal. Defaults to environment configuration.
            retry_base_delay_ms: Base delay for exponential retry backoff.
            retry_max_delay_ms: Upper bound for exponential retry backoff.
            dispatch_concurrency: Max number of due jobs dispatched at once
                per tick. Defaults to environment configuration. Jobs are
                independent records keyed by id in the store, and every store
                mutation (``upsert``/``load``/``save``) is a plain synchronous
                call with no internal ``await`` — so it always runs to
                completion atomically on the single asyncio event loop before
                any other task's code can run, regardless of how many
                dispatches are in flight concurrently. Bounding this above 1
                only overlaps the slow part (the awaited dispatch itself, e.g.
                network calls), not the store writes.
            briefing_reader: Callable returning ``(terminal_status, text)`` for
                a session, or ``None`` while its run is still in flight. Both
                collaborators are injected rather than imported so the outbox
                can be driven in a test without a session runtime or a network.
            channel_sender: Async callable delivering one briefing.
            delivery_lease_ms: How long a claimed row stays claimed before a
                later sweep may take it over. Too short duplicates a slow
                send; too long strands a briefing behind a dead process.

        Raises:
            ValueError: If the retry policy is invalid.
        """
        tuning = None
        if None in (max_consecutive_failures, retry_base_delay_ms, retry_max_delay_ms, dispatch_concurrency):
            tuning = get_env_config().agent_tuning
        self._store = store
        self._dispatch = dispatch
        self._briefing_reader = briefing_reader
        self._channel_sender = channel_sender
        self._delivery_lease_ms = delivery_lease_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sweep_task: asyncio.Task | None = None
        self._tick_interval_ms = tick_interval_ms
        self._now_fn = now_fn
        self._enabled = enabled
        self._max_consecutive_failures = (
            max_consecutive_failures
            if max_consecutive_failures is not None
            else tuning.vibe_trading_scheduler_max_consecutive_failures
        )
        self._retry_base_delay_ms = (
            retry_base_delay_ms
            if retry_base_delay_ms is not None
            else tuning.vibe_trading_scheduler_retry_base_delay_ms
        )
        self._retry_max_delay_ms = (
            retry_max_delay_ms
            if retry_max_delay_ms is not None
            else tuning.vibe_trading_scheduler_retry_max_delay_ms
        )
        self._dispatch_concurrency = (
            dispatch_concurrency
            if dispatch_concurrency is not None
            else tuning.vibe_trading_scheduler_dispatch_concurrency
        )
        if self._max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be at least 1")
        if self._retry_base_delay_ms < 0:
            raise ValueError("retry_base_delay_ms must be non-negative")
        if self._retry_max_delay_ms < self._retry_base_delay_ms:
            raise ValueError("retry_max_delay_ms must be at least retry_base_delay_ms")
        if self._dispatch_concurrency < 1:
            raise ValueError("dispatch_concurrency must be at least 1")
        self._task: asyncio.Task | None = None
        self._wakeup: asyncio.Event | None = None
        self._stopping = False
        self._recovered_stale_running = False
        self._startup_backlog_deferred = False
        self._watchdog_task: asyncio.Task | None = None
        self._executor_tick_count = 0

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
        self._executor_tick_count += 1
        if self._executor_tick_count % 60 == 0:
            try:
                from trade_integrations.observability.hooks import safe_emit

                safe_emit(
                    "schedule",
                    "scheduler_tick_heartbeat",
                    detail={"tick_count": self._executor_tick_count},
                )
            except ImportError:
                pass
        self.recover_stale_running(now, startup=True)
        self.recover_stale_running(now, startup=False)
        jobs = sorted(
            (
                job
                for job in self._store.load().values()
                if is_due(job, now) and not _collection_dispatch_blocked(job)
            ),
            key=lambda job: job.next_run_at,
        )
        # Dispatch up to _dispatch_concurrency jobs at once instead of
        # strictly one at a time: a burst of simultaneously-due jobs
        # (routine on overlapping cron cadences, not just a mass manual
        # trigger) used to queue behind whichever job the executor happened
        # to be awaiting, even when that job was slow only because of a
        # network call and shared nothing else with the others. Safe to
        # overlap because every store mutation a job performs (marking
        # RUNNING, persisting completion) is a synchronous call with no
        # internal ``await`` — it always runs to completion atomically on
        # this single event loop, so concurrent dispatches never interleave
        # their store writes even though their dispatch awaits overlap.
        semaphore = asyncio.Semaphore(self._dispatch_concurrency)

        async def _run_job_bounded(job: ScheduledResearchJob) -> None:
            async with semaphore:
                # One job's unexpected persistence/lifecycle error must not
                # starve every other job in this tick.
                try:
                    await self._run_job(job, now)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error("scheduled research job %s failed its run cycle", job.id, exc_info=True)

        if jobs:
            await asyncio.gather(*(_run_job_bounded(job) for job in jobs))

        # The sweep is what makes delivery correct; the event hook only makes
        # it prompt. A briefing whose hook was lost to a restart, a crash
        # between "run finished" and "message sent", or a transient send error
        # is picked up here on the next tick.
        await self.sweep_deliveries()

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
            if str((job.config or {}).get("job_type") or "") == "autonomous_agent_watch":
                # Autonomous watches are dispatched on a per-agent cadence; their
                # bootstrap path enqueues the first tick immediately after commit.
                # If the agent is not running, there is nothing to watch — skip
                # rather than fire a redundant tick that returns
                # ``agent_not_running`` (F2).
                if not _autonomous_watch_target_running(job):
                    job.next_run_at = now + self._tick_interval_ms
                    deferred += 1
                    logger.info(
                        "deferring autonomous_agent_watch for %s: no running agent",
                        job.id,
                    )
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
        # Captured here rather than at construction: the executor is built
        # before the server's loop exists, and request_sweep needs a loop that
        # is actually running to schedule onto.
        self._loop = asyncio.get_running_loop()
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

        # A persisted schedule the current grammar rejects (an older release
        # accepted a form since narrowed) must surface as a failed job, not as
        # an exception on the way to dispatch: that would leave the record
        # PENDING and due, retrying on every tick forever with nothing visible
        # to the user.
        try:
            validate_schedule(job.schedule)
        except ValueError as exc:
            logger.error("scheduled research job %s has an invalid schedule", job.id, exc_info=True)
            job.status = JobStatus.FAILED
            job.failure_kind = "schedule"
            job.last_error = _persisted_error(exc)
            job.last_run_at = now_ms
            # Not routed through _persist_completion: that guards against a
            # dispatch racing a concurrent mutation, but nothing has been
            # dispatched (or awaited) yet here, so no race window exists and
            # the RUNNING-only guard would just discard this write.
            self._store.upsert(job, validate=False)
            return

        if job.last_error and any(marker in job.last_error for marker in _RECOVERY_ERROR_MARKERS):
            job.last_error = None

        job.status = JobStatus.RUNNING
        # Stamp ``last_run_at`` before the dispatch await so the stale
        # watchdog (a separate task) sees a fresh ``last_run_at`` while the
        # dispatch is in flight. Without this, ``last_run_at`` stays at its
        # previous value (often days old) and ``is_job_stale_running`` falls
        # back to ``created_at``, marking every mid-dispatch job stale within
        # 60 seconds. The watchdog then advances ``next_run_at`` and resets
        # ``status`` to PENDING; when the dispatch completes,
        # ``_persist_completion`` reads back a PENDING job and silently
        # discards the completion write (because ``current.status != RUNNING``),
        # leaving ``last_run_at`` frozen at the old timestamp even though the
        # job ran successfully.
        job.last_run_at = now_ms
        # Lifecycle writes bypass validation: this record is already persisted,
        # and a write that cannot land would strand the job mid-flight.
        self._store.upsert(job, validate=False)

        timeout_ms = dispatch_timeout_ms_for(job)
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
        dispatch_error: Exception | None = None
        session_id: str | None = None
        was_cancelled = False
        try:
            session_id = await asyncio.wait_for(self._dispatch(job), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            logger.error(
                "scheduled research dispatch timed out for job %s after %sms",
                job.id,
                timeout_ms,
            )
            _request_pipeline_cancel_on_dispatch_timeout(job.id, job_type)
            job.config["_timed_out"] = True
            dispatch_error = TimeoutError(f"dispatch timed out after {timeout_ms}ms")
            job.consecutive_failures = int(job.consecutive_failures or 0) + 1
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
                was_cancelled = True
            else:
                logger.error("scheduled research dispatch failed for job %s", job.id, exc_info=True)
                dispatch_error = exc
                job.consecutive_failures = int(job.consecutive_failures or 0) + 1
        else:
            job.consecutive_failures = 0
            job.config.pop("_timed_out", None)
            raw_summary = job.config.pop(LAST_RESULT_CONFIG_KEY, None)
            if isinstance(raw_summary, dict):
                job.last_result_summary = raw_summary
            if isinstance(session_id, str) and session_id:
                if job.delivery_channel:
                    # Arm the outbox in the same write that records the firing.
                    # The row exists before anything can be sent, so a crash can
                    # only ever lose the *speed* of delivery, never the fact that
                    # one is owed.
                    job.delivery = DeliveryRecord(
                        status=DeliveryStatus.PENDING,
                        session_id=session_id,
                        key=f"{job.id}:{session_id}:{job.delivery_channel}",
                        updated_at=now_ms,
                    )
                else:
                    # No delivery owed, but the verdict sweep still needs the
                    # link from this job to its run: the session id rides the
                    # record with an explicit NONE so the outbox never picks it.
                    job.delivery = DeliveryRecord(
                        status=DeliveryStatus.NONE,
                        session_id=session_id,
                        updated_at=now_ms,
                    )

        finally:
            finish_named_task(task_id)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            _final_status = JobStatus.COMPLETED if dispatch_error is None else JobStatus.FAILED
            logger.info(
                "scheduled research dispatch done job=%s type=%s status=%s (%.1fs)",
                job.id,
                job_type,
                _final_status.value,
                elapsed_ms / 1000.0,
            )
            try:
                from trade_integrations.observability.emitter import emit_job_rollup
                from trade_integrations.observability.rollup import JobRollup

                emit_job_rollup(
                    JobRollup(
                        status=_final_status.value,
                        had_errors=dispatch_error is not None,
                        had_work=dispatch_error is None or bool(job.last_error),
                        job_type=job_type,
                        job_id=job.id,
                        detail={"last_error": str(dispatch_error) if dispatch_error else ""},
                    ),
                    module="schedule",
                )
                from trade_integrations.observability.emitter import emit

                emit(
                    "schedule",
                    "job_dispatch_done",
                    level="error" if dispatch_error else "info",
                    job_id=job.id,
                    duration_ms=elapsed_ms,
                    detail={
                        "job_type": job_type,
                        "final_status": _final_status.value,
                        "last_error": str(dispatch_error) if dispatch_error else "",
                    },
                )
            except ImportError:
                pass

        job.last_run_at = now_ms
        try:
            scheduled_next_run = next_due(job.schedule, now_ms, job.timezone)
        except Exception as exc:
            logger.error("scheduled research schedule advancement failed for job %s", job.id, exc_info=True)
            job.status = JobStatus.FAILED
            job.failure_kind = "schedule"
            job.last_error = _persisted_error(exc)
            self._persist_completion(job)
            return

        job.next_run_at = scheduled_next_run
        if dispatch_error is None:
            job.status = JobStatus.COMPLETED
            job.failure_kind = None
            if not was_cancelled:
                job.last_error = None
        else:
            job.failure_kind = "dispatch"
            job.last_error = _persisted_error(dispatch_error)
            if job.consecutive_failures >= self._max_consecutive_failures:
                job.status = JobStatus.FAILED
            else:
                job.status = JobStatus.PENDING
                retry_delay = self._retry_delay_ms(job.consecutive_failures)
                job.next_run_at = max(scheduled_next_run, now_ms + retry_delay)
                logger.warning(
                    "scheduled research job %s will retry after failure %d/%d at %d",
                    job.id,
                    job.consecutive_failures,
                    self._max_consecutive_failures,
                    job.next_run_at,
                )
        self._persist_completion(job)

    def _delivery_is_eligible(self, job: ScheduledResearchJob, now_ms: int) -> bool:
        """Whether this row is a sweep's to take.

        PENDING is owed. SENDING is claimed, and the claim is a lease rather
        than a lock: a process that died inside the send call would otherwise
        hold the row forever, so once the lease has expired the row is owed
        again. The idempotency key remains the backstop for the window that
        leaves.

        Args:
            job: The job whose outbox row is being considered.
            now_ms: Reference time in epoch milliseconds.

        Returns:
            True when a sweep should attempt this row now.
        """
        status = job.delivery.status
        if status is DeliveryStatus.PENDING:
            return True
        if status is not DeliveryStatus.SENDING:
            return False
        claimed_at = job.delivery.updated_at
        return claimed_at is None or now_ms - claimed_at >= self._delivery_lease_ms

    def request_sweep(self) -> None:
        """Ask for a delivery sweep as soon as the loop is free.

        Called from the session event listener, so it runs on whichever thread
        published the event and must not block. Sweeps coalesce: one already in
        flight covers anything that arrived while it was running, and the claim
        makes an overlap harmless in any case. Without a running loop this is a
        no-op — the periodic tick is what makes delivery correct, and this only
        makes it prompt.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        if self._sweep_task is not None and not self._sweep_task.done():
            return

        def _schedule() -> None:
            if self._sweep_task is not None and not self._sweep_task.done():
                return
            self._sweep_task = loop.create_task(self._sweep_quietly())

        try:
            loop.call_soon_threadsafe(_schedule)
        except RuntimeError:
            # The loop closed between the check and the call; the next tick
            # picks the row up.
            pass

    async def _sweep_quietly(self) -> None:
        """Run a sweep whose failure must not escape into the event path."""
        try:
            await self.sweep_deliveries()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("requested delivery sweep failed", exc_info=True)

    async def sweep_deliveries(self) -> int:
        """Deliver every briefing whose run has reached a terminal state.

        Idempotent and restart-safe: the outbox row is the only source of
        truth, a row already ``SENT`` is never re-sent, and a row whose run is
        still in flight is left for a later sweep.

        Returns:
            The number of rows whose state changed.
        """
        if self._briefing_reader is None:
            return 0

        changed = 0
        now = self._now_fn()
        for job in list(self._store.load().values()):
            if not job.delivery.session_id:
                continue
            if job.delivery.status is DeliveryStatus.NONE:
                # No briefing is owed to a channel, but this sweep is the only
                # terminal-observer a channel-less monitor has: read the run's
                # end state here and persist its verdict once it lands.
                changed += self._record_verdict_if_terminal(job, now)
                continue
            if not job.delivery_channel:
                continue
            if not self._delivery_is_eligible(job, now):
                continue
            if self._channel_sender is None:
                continue
            try:
                outcome = self._briefing_reader(job.delivery.session_id)
            except Exception as exc:
                logger.error("briefing read failed for job %s", job.id, exc_info=True)
                self._mark_delivery_failed(job, exc, retryable=True)
                changed += 1
                continue
            if outcome is None:
                continue  # still running; a later sweep will find it

            status, text = outcome
            if status != "completed":
                # A failed or cancelled run has no briefing to deliver. Record
                # why rather than leaving the row pending forever.
                self._mark_delivery_failed(
                    job, RuntimeError(f"run {status}"), retryable=False
                )
                changed += 1
                continue

            # Claim the row BEFORE the network call. A re-read alone would not
            # help: while the first send is in flight the row still reads
            # PENDING, so a concurrent sweep or the event hook would deliver
            # the same briefing again.
            current = self._store.get(job.id)
            if current is None or current.delivery.key != job.delivery.key:
                continue
            if not self._delivery_is_eligible(current, now):
                continue
            current.delivery.status = DeliveryStatus.SENDING
            current.delivery.updated_at = now
            self._store.upsert(current, validate=False)

            try:
                await self._channel_sender(job.delivery_channel, job.delivery_target, text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("briefing delivery failed for job %s", job.id, exc_info=True)
                self._mark_delivery_failed(current, exc, retryable=True)
                changed += 1
                continue

            current.delivery.status = DeliveryStatus.SENT
            current.delivery.error = None
            current.delivery.updated_at = self._now_fn()
            # The verdict rides the same write as the terminal delivery state,
            # never a second pass over the job (#1140's clobbering lesson).
            if (
                current.last_verdict is None
                or current.last_verdict.session_id != current.delivery.session_id
            ):
                self._record_verdict_on(current, current.delivery.session_id, text, now)
            self._store.upsert(current, validate=False)
            changed += 1
        return changed

    def _record_verdict_on(
        self, job: ScheduledResearchJob, session_id: str, text: str, now_ms: int
    ) -> None:
        """Write the run's verdict record onto ``job``, shifting the prior one.

        The previous record embeds one level only: a verdict chain any deeper
        is noise for the list view.
        """
        parse, items = parse_verdict_section(text)
        previous = job.last_verdict
        if previous is not None:
            previous = _dc_replace(previous, previous=None)
        job.last_verdict = VerdictRecord(
            session_id=session_id,
            recorded_at=now_ms,
            parse=parse,
            outcome=outcome_of(items),
            items=items,
            previous=previous,
        )

    def _record_verdict_if_terminal(self, job: ScheduledResearchJob, now_ms: int) -> int:
        """Persist the run's verdict once its session reaches a terminal state.

        Channel-less jobs have no outbox, so this sweep is the only place their
        terminal briefing is ever read. In-flight runs return ``None`` from the
        briefing reader and are left for a later pass; failed or cancelled runs
        produce no briefing, so the prior verdict simply stays visible as stale.

        Returns:
            1 when the job record changed, else 0.
        """
        session_id = job.delivery.session_id
        if not session_id:
            return 0
        if job.last_verdict is not None and job.last_verdict.session_id == session_id:
            return 0  # this firing is already recorded
        try:
            outcome = self._briefing_reader(session_id) if self._briefing_reader else None
        except Exception:
            logger.error("verdict read failed for job %s", job.id, exc_info=True)
            return 0
        if outcome is None:
            return 0
        status, text = outcome
        if status != "completed":
            return 0
        self._record_verdict_on(job, session_id, text, now_ms)
        self._store.upsert(job, validate=False)
        return 1

    def _mark_delivery_failed(
        self, job: ScheduledResearchJob, exc: Exception, *, retryable: bool
    ) -> None:
        """Record a delivery failure without losing which firing it belonged to.

        Args:
            job: The job whose outbox row failed.
            exc: The failure, persisted through the same redaction the dispatch
                path uses.
            retryable: Whether a later sweep could still succeed. A channel
                outage is; a run that finished FAILED or CANCELLED is not,
                because no briefing will ever exist for it.
        """
        job.delivery.error = _persisted_error(exc)
        job.delivery.updated_at = self._now_fn()
        if retryable:
            job.delivery.attempts += 1
            if job.delivery.attempts < self._max_consecutive_failures:
                job.delivery.status = DeliveryStatus.PENDING
                self._store.upsert(job, validate=False)
                return
        job.delivery.status = DeliveryStatus.FAILED
        self._store.upsert(job, validate=False)

    def _retry_delay_ms(self, consecutive_failures: int) -> int:
        """Return bounded exponential backoff for a dispatch failure count."""
        exponent = max(0, consecutive_failures - 1)
        if self._retry_base_delay_ms == 0:
            return 0
        delay = self._retry_base_delay_ms * (2 ** min(exponent, 62))
        return min(delay, self._retry_max_delay_ms)

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
        self._store.upsert(job, validate=False)
