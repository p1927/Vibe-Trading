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
from typing import Any, Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

from src.config.accessor import get_env_config
from src.channels.bus.events import DeliveryReceipt
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
from src.scheduled_research.index_jobs import HubNewsIngestCollectedNothingError
from src.scheduled_research.job_tier_policy import (
    collection_job_dispatch_enabled,
    is_collection_job,
    is_operational_tier_job,
)
from src.scheduled_research.store import ScheduledResearchJobStore
# Fork-only sidecar (docs/FORK_CONVENTIONS.md): the jobs `is_due()` cannot express.
from src.scheduled_research import stuck_jobs
# Fork-only sidecar: D11 shortest-expected-runtime-first slot admission (docs/DECISIONS.md).
from src.scheduled_research.dispatch_admission import DispatchPool, record_dispatch_duration
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
    dispatch_concurrency_for_job_type,
    dispatch_timeout_ms_for,
    is_job_stale_running,
    stale_running_ms_for,
)


try:  # sidecar (docs/FORK_CONVENTIONS.md): Trade owns the budget mechanism, this fork calls it
    from trade_integrations.job_deadline import job_deadline as _job_dispatch_budget
except ImportError:  # pragma: no cover - standalone vibetrading install, outside the monorepo
    from contextlib import contextmanager as _contextmanager

    @_contextmanager
    def _job_dispatch_budget(seconds):  # type: ignore[misc]
        """No-op when this agent runs outside the Trade monorepo (its packaged CLI does)."""
        yield


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

#: How long :meth:`ScheduledResearchExecutor.stop` waits for already-dispatched
#: jobs to finish before it cancels them and recovers whatever is still RUNNING.
#: Small on purpose — it exists to catch a dispatch that is *milliseconds* from
#: its completion write, not to let a 90-minute ingest finish. Live-measured
#: 2026-09-07: two real completions landed 13-24ms after shutdown began and were
#: mislabelled for want of exactly this wait. A long grace here would instead
#: stall the process shutdown that uvicorn is already timing out.
DEFAULT_SHUTDOWN_DRAIN_GRACE_MS = 5_000

NowFn = Callable[[], int]
# A dispatcher may return the session id it enqueued into. Returning None keeps
# the pre-delivery contract working unchanged.
DispatchCallback = Callable[[ScheduledResearchJob], Awaitable[Optional[str]]]
#: session_id -> (terminal status, briefing text), or None while in flight.
BriefingReader = Callable[[str], Optional[tuple[str, str]]]
#: (channel, target, text) -> delivered.
ChannelSender = Callable[
    [str, Optional[str], str], Awaitable[DeliveryReceipt | None]
]

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
    if job.status in {
        JobStatus.CANCELLED,
        JobStatus.RUNNING,
        JobStatus.FAILED,
        JobStatus.EXPIRED,
    }:
        return False
    if job.end_at is not None and now_ms > job.end_at:
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


#: Wall-clock ms when this process imported the executor, i.e. when it booted. Reported by
#: ``liveness()`` so a caller can tell "never resumed since this process started" from "resumed,
#: then reset by a restart": under ``trade dev --reload`` any code save restarts the process and
#: the executor always boots stopped. See
#: .claude/backlog/items/2026-09-06-boot-pause-under-reload.md.
_PROCESS_STARTED_MS = int(time.time() * 1000)


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
        shutdown_drain_grace_ms: int = DEFAULT_SHUTDOWN_DRAIN_GRACE_MS,
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
                mutation (``upsert``/``update_run_state``/``load``/``save``) is
                a plain synchronous
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
        self._shutdown_drain_grace_ms = max(0, int(shutdown_drain_grace_ms))
        #: True only between shutdown recovery finalising the store and the next
        #: start(). See the guard in :meth:`_persist_completion`.
        self._shutdown_recovery_done = False
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
        # How many times start() actually started the loop in this process, and when last
        # (wall clock). With _PROCESS_STARTED_MS these say which "not running" a caller sees.
        self._start_count = 0
        self._last_started_ms: int | None = None
        # The operational tier's own loop/task, over the same store — see
        # `_run_operational`/`_operational_tick`. Split from the main loop so a long
        # collection-job dispatch can never hold an autonomous agent's watch/news/
        # strategy-review cadence hostage (the head-of-line stall the main tick barrier
        # causes). Starts and stops together with the main loop; never runs alone.
        self._operational_task: asyncio.Task | None = None
        self._operational_wakeup: asyncio.Event | None = None
        self._operational_tick_count = 0
        self._last_operational_tick_started_ms: int | None = None
        self._last_operational_tick_completed_ms: int | None = None
        self._operational_in_flight_job_ids: set[str] = set()
        # Liveness evidence. ``is_running`` only says a Task object exists and hasn't
        # finished — it stays True for a loop parked inside ``gather`` on one slow job
        # while every other job starves. These three are the evidence that the loop is
        # actually making progress; see ``liveness()`` and docs/add/autonomous_agents.md.
        self._last_tick_started_ms: int | None = None
        self._last_tick_completed_ms: int | None = None
        self._in_flight_job_ids: set[str] = set()
        # D11 (sidecar dispatch_admission.py): one persistent slot pool per loop, so a
        # tick offers its due jobs and returns instead of awaiting their dispatches.
        self._dispatch_pool = DispatchPool(
            global_cap=lambda: self._dispatch_concurrency, stopping=lambda: self._stopping, next_due_fn=next_due
        )
        self._operational_dispatch_pool = DispatchPool(
            global_cap=lambda: self._dispatch_concurrency, stopping=lambda: self._stopping, next_due_fn=next_due
        )

    @property
    def is_running(self) -> bool:
        """Return whether the background loop task is active."""
        return self._task is not None and not self._task.done()

    @property
    def is_operational_running(self) -> bool:
        """Return whether the operational tier's own loop task is active."""
        return self._operational_task is not None and not self._operational_task.done()

    def _restart_fields(self) -> dict[str, Any]:
        """Process start time and resume history, all wall-clock epoch ms.

        ``start_count == 0`` with ``running: false`` means nobody has resumed the executor since
        this process started; a resume issued before ``process_started_at`` was wiped by that
        restart. ``start_count > 0`` means it was resumed here and has since stopped.
        """
        return {
            "process_started_at": _PROCESS_STARTED_MS,
            "start_count": self._start_count,
            "last_started_at": self._last_started_ms,
        }

    def liveness(self, now_ms: int | None = None) -> dict[str, Any]:
        """Evidence that the dispatch loop is progressing, not merely alive.

        ``is_running`` is not a liveness signal: a tick parked inside ``asyncio.gather``
        on one long job holds the barrier for every other job (``tick`` gathers only the
        jobs due at its start), so the loop reports healthy while dispatching nothing.
        That is exactly what a 2026-09-07 live pass observed — ``running: true`` across a
        32-minute window with zero dispatches and an agent's watch tick 25 minutes overdue.

        ``max_overdue_seconds`` is the field that would have caught it: it rises whether the
        cause is a stalled tick or a stopped executor.

        It is also computed only over jobs *this tier is allowed to dispatch*: a
        collection job on a non-release profile is excluded, because it is permanently past
        ``next_run_at`` by design and its age measures the tier gate, not the scheduler.

        It does **not** rise for a job nothing can dispatch, and an earlier version of this
        docstring claimed otherwise. It is computed over jobs ``is_due()`` accepts, and
        ``is_due()`` returns False for FAILED, EXPIRED, CANCELLED, RUNNING and paused jobs — so
        the two states that mean "this job is permanently dead" were precisely the two it could
        not see. Measured 2026-09-07: ``nifty-hub-news-ingest-light``/``-tight`` sat **105 hours**
        past ``next_run_at`` with ``status=failed`` while this endpoint reported
        ``max_overdue_seconds: 6183`` against an unrelated healthy-but-slow job, and
        ``check_dev_ports.py`` passed that reading for four days.

        ``stuck_jobs``/``max_stuck_seconds`` close that hole without touching dispatch: they are
        computed over ``is_stuck()``, the complement, so a terminal or paused job is reported as
        a fault while still never being re-dispatched. See
        ``.claude/backlog/items/2026-09-07-nothing-notices-an-overdue-scheduled-job.md``.
        """
        now = self._now_fn() if now_ms is None else now_ms
        max_overdue_ms = 0
        overdue_job_id = ""
        operational_max_overdue_ms = 0
        operational_overdue_job_id = ""
        stuck: list[dict[str, Any]] = []
        try:
            for job in self._store.load().values():
                # A job this tier may not dispatch is excluded from *both* fault metrics
                # below. On dev that is every collection job, and there is nothing an
                # operator here can do about any of them: they are parked because collection
                # belongs to release. Reporting them made the standing dev health check fail
                # permanently — first through `max_overdue_seconds`, then, once that was
                # fixed, through `stuck_jobs` naming the same ten jobs as "fallen out of the
                # schedule and will never fire again". Both readings are true of dev and
                # neither is a fault. On release, where these jobs really do run, the gate is
                # open and every one of them is reported exactly as before.
                if _collection_dispatch_blocked(job):
                    continue
                if stuck_jobs.is_stuck(job, now):
                    # Reported, never dispatched. Collected BEFORE the `is_due` filter below,
                    # precisely because `is_due` is what excludes these -- see stuck_jobs.py.
                    stuck.append(stuck_jobs.stuck_row(job, now))
                if not is_due(job, now):
                    continue
                overdue = now - int(job.next_run_at or now)
                job_type = str((job.config or {}).get("job_type") or "")
                if is_operational_tier_job(job_type):
                    if overdue > operational_max_overdue_ms:
                        operational_max_overdue_ms = overdue
                        operational_overdue_job_id = str(job.id)
                    continue
                if overdue > max_overdue_ms:
                    max_overdue_ms = overdue
                    overdue_job_id = str(job.id)
        except Exception:
            # Store read failure must not take down the health endpoint, but it must not
            # be reported as "nothing overdue" either — None is distinguishable from 0.
            logger.warning("liveness store read failed", exc_info=True)
            return {
                **self._restart_fields(),
                "running": self.is_running,
                "tick_count": self._executor_tick_count,
                "last_tick_started_at": self._last_tick_started_ms,
                "last_tick_completed_at": self._last_tick_completed_ms,
                "in_flight": sorted(self._in_flight_job_ids),
                "max_overdue_seconds": None,
                "max_overdue_job_id": "",
                **stuck_jobs.UNKNOWN,
                "operational": {
                    "running": self.is_operational_running,
                    "tick_count": self._operational_tick_count,
                    "last_tick_started_at": self._last_operational_tick_started_ms,
                    "last_tick_completed_at": self._last_operational_tick_completed_ms,
                    "in_flight": sorted(self._operational_in_flight_job_ids),
                    "max_overdue_seconds": None,
                    "max_overdue_job_id": "",
                },
            }
        return {
            **self._restart_fields(),
            "running": self.is_running,
            "tick_count": self._executor_tick_count,
            "last_tick_started_at": self._last_tick_started_ms,
            "last_tick_completed_at": self._last_tick_completed_ms,
            "in_flight": sorted(self._in_flight_job_ids),
            "max_overdue_seconds": round(max_overdue_ms / 1000.0, 1),
            "max_overdue_job_id": overdue_job_id,
            **stuck_jobs.summarize(stuck),
            # The operational tier (autonomous_agent_* + recording_wake +
            # options_position_monitor) runs its own loop/tick, split from the main one
            # above so it can never be starved by a long collection-job dispatch — see
            # `_run_operational`/`_operational_tick` and
            # .claude/backlog/items/2026-09-07-scheduler-tick-head-of-line-stall.md.
            "operational": {
                "running": self.is_operational_running,
                "tick_count": self._operational_tick_count,
                "last_tick_started_at": self._last_operational_tick_started_ms,
                "last_tick_completed_at": self._last_operational_tick_completed_ms,
                "in_flight": sorted(self._operational_in_flight_job_ids),
                "max_overdue_seconds": round(operational_max_overdue_ms / 1000.0, 1),
                "max_overdue_job_id": operational_overdue_job_id,
            },
        }

    def start(self) -> None:
        """Start the background loop.

        Idempotent. When disabled, this is a no-op.
        """
        if not self._enabled or self.is_running:
            return
        self._stopping = False
        self._shutdown_recovery_done = False
        self._start_count += 1
        self._last_started_ms = int(time.time() * 1000)
        self.recover_stale_running(self._now_fn(), startup=True)
        self._wakeup = asyncio.Event()
        self._operational_wakeup = asyncio.Event()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="scheduled-research-executor")
        # Started together with the main loop, per the settled decision that nothing
        # dispatches until the operator explicitly resumes — there is no independent
        # start/stop for the operational tier.
        self._operational_task = loop.create_task(
            self._run_operational(), name="scheduled-research-executor-operational"
        )
        self._watchdog_task = loop.create_task(
            self._stale_watchdog(),
            name="scheduled-research-stale-watchdog",
        )

    def wake(self) -> None:
        """Wake the executor loop for an immediate tick (e.g. after manual job recovery)."""
        if self._operational_wakeup is not None:
            self._operational_wakeup.set()
        if self._wakeup is not None:
            self._wakeup.set()

    async def stop(self, *, auto_pause_reason: str | None = None) -> None:
        """Stop the background loop and wait for it to finish.

        Idempotent. When disabled or not started, this is a no-op.

        Args:
            auto_pause_reason: When set, any job caught mid-``RUNNING`` by this
                shutdown is also marked ``paused`` with this reason (see
                :meth:`recover_all_running_on_shutdown`) so it reads as a
                process-restart interruption rather than a real hang. Leave
                ``None`` for a deliberate user-initiated pause (the dispatch
                loop stopping is the user's intent here, not a crash — each
                job should stay eligible to fire again once resumed rather
                than needing an explicit unpause).
        """
        if not self._enabled:
            return
        logger.info("scheduled research executor stopping…")
        self._stopping = True
        # Drain first, recover last. This used to recover *before* touching the
        # dispatch tasks, so a job whose work finished milliseconds later found
        # itself already marked PENDING and its own completion write stood down
        # — a genuinely successful run permanently filed as "recovered on
        # executor shutdown". Live-measured 2026-09-07: two real completions
        # landed 13-24ms after this point. See .claude/backlog/items/
        # 2026-09-07-shutdown-recovery-relabels-a-completed-run.md.
        #
        # Ordering it this way is only safe because recover_all_running_on_shutdown
        # re-reads each job fresh and skips anything no longer RUNNING (see its
        # docstring): a dispatch that lands during the drain is left alone, and
        # one that is genuinely still running after the cancel below never wrote
        # a completion at all (``_dispatch_one`` re-raises CancelledError), so it
        # is still RUNNING and still gets recovered.
        await self._drain_in_flight_dispatches()
        # Dispatches are pool tasks, not children of the loop task (D11), so cancelling the
        # loop below would no longer cancel them — the pools do it explicitly.
        await self._dispatch_pool.shutdown()
        await self._operational_dispatch_pool.shutdown()
        watchdog = self._watchdog_task
        if watchdog is not None:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None
        operational_task = self._operational_task
        if operational_task is not None:
            if self._operational_wakeup is not None:
                self._operational_wakeup.set()
            operational_task.cancel()
            try:
                await operational_task
            except asyncio.CancelledError:
                pass
            self._operational_task = None
        task = self._task
        if task is None:
            self._shutdown_recovery_done = True
            self.recover_all_running_on_shutdown(
                self._now_fn(), auto_pause_reason=auto_pause_reason
            )
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
        # Every dispatch task has now either written its own completion (during
        # the drain above) or been cancelled without writing one. Whatever is
        # still RUNNING in the store is therefore genuinely interrupted.
        self._shutdown_recovery_done = True
        self.recover_all_running_on_shutdown(self._now_fn(), auto_pause_reason=auto_pause_reason)
        self._reset_runtime_state()
        logger.info("scheduled research executor stopped")

    async def _drain_in_flight_dispatches(self) -> None:
        """Give already-dispatched jobs a bounded moment to finish before shutdown.

        ``_dispatch_one`` discards a job's id from its in-flight set only in the
        ``finally`` *after* ``_run_job`` returns, and ``_run_job`` writes its
        completion synchronously before returning — so an empty in-flight set is
        proof that every dispatch which was going to complete has already
        persisted its result. That is the whole point: it lets :meth:`stop`
        distinguish "finished just as we were stopping" from "genuinely
        interrupted", instead of relabelling the former as the latter.

        Bounded by ``shutdown_drain_grace_ms``. A long-running dispatch simply
        outlasts the grace and is cancelled and recovered exactly as before —
        this never holds shutdown open for a job's full dispatch budget.
        """
        grace_s = self._shutdown_drain_grace_ms / 1000.0
        if grace_s <= 0:
            return
        deadline = time.monotonic() + grace_s
        while self._in_flight_job_ids or self._operational_in_flight_job_ids:
            if time.monotonic() >= deadline:
                logger.info(
                    "scheduled research shutdown: %d dispatch(es) still in flight after "
                    "%dms grace, cancelling them (%s)",
                    len(self._in_flight_job_ids) + len(self._operational_in_flight_job_ids),
                    self._shutdown_drain_grace_ms,
                    ", ".join(sorted(self._in_flight_job_ids | self._operational_in_flight_job_ids)),
                )
                return
            await asyncio.sleep(0.01)

    def recover_all_running_on_shutdown(
        self, now_ms: int | None = None, *, auto_pause_reason: str | None = None
    ) -> int:
        """Reset every RUNNING job to pending for clean executor shutdown.

        Args:
            now_ms: Optional explicit reference time.
            auto_pause_reason: When set, also stamp each recovered job
                ``paused=True``/``auto_paused_reason=<this>`` — the same
                distinguishing marker ``lifecycle.recover_persisted_scheduler_jobs``
                uses for boot/shutdown recovery — so a process-restart
                interruption reads differently from a job that just happens to
                be ``pending``, without cross-referencing the API log. Left
                unset for a plain user-initiated pause (see :meth:`stop`).

        A job's own in-flight ``_run_job`` completion can land concurrently
        with this call (``stop()`` cancels the dispatch task but does not
        wait for its completion write — see 2026-09-07-
        executor-shutdown-recovery-can-revert-a-concurrent-completion). The
        original implementation took one ``load()`` snapshot, mutated it in
        memory, and wrote the *entire* store back from that snapshot — if a
        completion's targeted ``update_run_state()`` write landed in between,
        this method's later whole-store ``save()`` silently clobbered it back
        to the pre-completion state, discarding a genuinely successful run's
        bookkeeping (the collected data itself was unaffected; only the job
        record was reverted). Fixed by re-``load()``-ing fresh and
        re-checking ``status == RUNNING`` immediately before each job's own
        write, with no ``await`` in between — since this is asyncio
        (cooperative, single-threaded), that leaves no point where another
        coroutine's write can interleave, closing the race the same way
        ``update_run_state()`` already does for the ordinary completion path.
        """
        now = self._now_fn() if now_ms is None else now_ms
        candidate_ids = [
            job_id for job_id, job in self._store.load().items() if job.status == JobStatus.RUNNING
        ]
        recovered = 0
        for job_id in candidate_ids:
            jobs = self._store.load()
            job = jobs.get(job_id)
            if job is None or job.status != JobStatus.RUNNING:
                # Deleted, replaced, or already completed/failed by its own
                # dispatch task between the listing above and here — that
                # write owns the record now; do not touch it.
                continue
            job.status = JobStatus.PENDING
            try:
                job.next_run_at = next_due(job.schedule, now)
            except Exception:
                logger.warning(
                    "could not advance schedule for shutdown-recovered job %s; deferring one tick",
                    job.id,
                    exc_info=True,
                )
                job.next_run_at = now + self._tick_interval_ms
            if not job.last_error:
                job.last_error = "recovered on executor shutdown"
            if auto_pause_reason and not job.paused:
                job.paused = True
                job.auto_paused_reason = auto_pause_reason
            recovered += 1
            logger.warning(
                "recovering scheduled research job %s on executor shutdown (next_run_at=%s)",
                job.id,
                job.next_run_at,
            )
            self._store.save(jobs)
        return recovered

    def _reset_runtime_state(self) -> None:
        """Clear in-memory executor flags so the next start is fresh."""
        self._stopping = False
        self._recovered_stale_running = False
        self._startup_backlog_deferred = False
        if self._wakeup is not None:
            self._wakeup.set()
        if self._operational_wakeup is not None:
            self._operational_wakeup.set()

    async def tick(self, now_ms: int | None = None, *, wait: bool = True) -> None:
        """Run one poll/dispatch pass.

        Args:
            now_ms: Optional explicit reference time. Defaults to ``now_fn``.
            wait: When true (direct callers), return only once the jobs this tick offered
                have finished. The background loop passes ``False`` so a long dispatch never
                blocks the next tick (D11(a); see ``dispatch_admission.py``).
        """
        now = self._now_fn() if now_ms is None else now_ms
        self._executor_tick_count += 1
        self._last_tick_started_ms = now
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
        self._expire_elapsed_jobs(now)
        jobs = sorted(
            (
                job
                for job in self._store.load().values()
                if is_due(job, now)
                and not _collection_dispatch_blocked(job)
                # The operational tier (autonomous_agent_* + recording_wake +
                # options_position_monitor) dispatches on its own loop/tick
                # (`_operational_tick`) so a long collection-job dispatch on this main
                # loop can never hold its cadence hostage — see
                # `_run_operational` and
                # .claude/backlog/items/2026-09-07-scheduler-tick-head-of-line-stall.md.
                and not is_operational_tier_job(str(job.config.get("job_type") or ""))
            ),
            key=lambda job: job.next_run_at,
        )
        await self._dispatch_due_jobs(
            jobs, now, in_flight=self._in_flight_job_ids, pool=self._dispatch_pool, wait=wait
        )

        # The sweep is what makes delivery correct; the event hook only makes
        # it prompt. A briefing whose hook was lost to a restart, a crash
        # between "run finished" and "message sent", or a transient send error
        # is picked up here on the next tick.
        await self.sweep_deliveries()
        self._last_tick_completed_ms = self._now_fn()

    async def _operational_tick(self, now_ms: int | None = None, *, wait: bool = True) -> None:
        """Run one poll/dispatch pass over the operational tier only.

        Deliberately does not repeat the main tick's store-wide maintenance
        (`recover_stale_running`, `_expire_elapsed_jobs`, `sweep_deliveries`) — those
        already cover the whole store regardless of which loop calls them, and running
        them from both loops would only duplicate work, not add coverage. This loop
        owns exactly one thing: dispatching operational-tier jobs on their own cadence,
        immune to whatever the main loop's tick is currently blocked on.
        """
        now = self._now_fn() if now_ms is None else now_ms
        self._operational_tick_count += 1
        self._last_operational_tick_started_ms = now
        jobs = sorted(
            (
                job
                for job in self._store.load().values()
                if is_due(job, now) and is_operational_tier_job(str(job.config.get("job_type") or ""))
            ),
            key=lambda job: job.next_run_at,
        )
        await self._dispatch_due_jobs(
            jobs,
            now,
            in_flight=self._operational_in_flight_job_ids,
            pool=self._operational_dispatch_pool,
            wait=wait,
        )
        self._last_operational_tick_completed_ms = self._now_fn()

    async def _dispatch_due_jobs(
        self,
        jobs: list[ScheduledResearchJob],
        now: int,
        *,
        in_flight: set[str],
        pool: DispatchPool,
        wait: bool = True,
    ) -> None:
        """Offer `jobs` (already filtered to due + tier-eligible) to this loop's slot pool,
        tracking in-flight ids in `in_flight` for `liveness()`. Shared by the main tick and
        `_operational_tick` so both loops' dispatch/concurrency semantics stay identical.

        Slot accounting, the per-type caps, shortest-expected-runtime-first ordering, the
        reserved last slot and ageing all live in the sidecar ``dispatch_admission.py``
        (docs/DECISIONS.md D11). Concurrent dispatches are safe for the same reason as
        before: every store mutation a job performs is a synchronous call with no internal
        ``await``, so it runs to completion atomically on this single event loop. A job
        waiting for a slot has not been marked RUNNING and its ``dispatch_timeout_ms`` clock
        has not started, so queueing never counts against its budget.
        """

        async def _dispatch_one(job: ScheduledResearchJob, admit_now: int) -> None:
            # One job's unexpected persistence/lifecycle error must not starve every other
            # job. Tracked in `in_flight` for the whole await so ``liveness()`` can name
            # what is running.
            in_flight.add(str(job.id))
            try:
                await self._run_job(job, admit_now)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("scheduled research job %s failed its run cycle", job.id, exc_info=True)
            finally:
                in_flight.discard(str(job.id))

        await pool.submit(
            jobs, now, run=_dispatch_one, population=self._store.load().values(), wait=wait
        )

    def _expire_elapsed_jobs(self, now_ms: int) -> int:
        """Persist jobs whose configured end boundary has elapsed."""
        jobs = self._store.load()
        changed = 0
        for job in jobs.values():
            if job.end_at is None or now_ms <= job.end_at:
                continue
            if job.status in {
                JobStatus.CANCELLED,
                JobStatus.FAILED,
                JobStatus.EXPIRED,
                JobStatus.RUNNING,
            }:
                continue
            job.status = JobStatus.EXPIRED
            changed += 1
        if changed:
            self._store.save(jobs)
        return changed

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

        A job that is currently tier-eligible to dispatch (i.e. not blocked by
        ``_collection_dispatch_blocked`` — a non-collection job, or a collection
        job on the release tier) is left alone instead: its ``next_run_at``
        stays in the past so the executor's normal tick loop picks it up and
        dispatches it on the very next tick, subject to that loop's own
        existing concurrency/pacing limits. Deferring is reserved for jobs that
        cannot dispatch right now anyway (dispatch-blocked collection jobs, or
        an ``autonomous_agent_watch`` with no running agent) — pushing those
        forward would just fail or be pointless.
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
            if not _collection_dispatch_blocked(job):
                # Tier-eligible right now: leave next_run_at in the past so the
                # normal tick loop dispatches it starting the very next tick.
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
                await self.tick(self._now_fn(), wait=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("scheduled research executor tick failed", exc_info=True)
            if self._stopping:
                break
            await self._sleep_or_wake(self._tick_interval_ms)

    async def _run_operational(self) -> None:
        """The operational tier's own poll loop, over the same store as `_run`.

        Mirrors `_run` exactly except for which tick it calls and which wakeup event it
        sleeps on — same tick interval, same grace period, same shutdown handling.
        Deliberately does not call `defer_startup_backlog` a second time: that method is
        idempotent (`self._startup_backlog_deferred` guards it) and the main loop's
        `_run` already covers the whole store, operational jobs included.
        """
        grace_ms = _startup_grace_ms()
        if grace_ms > 0:
            await self._sleep_or_wake(grace_ms, event=self._operational_wakeup)
        while not self._stopping:
            try:
                await self._operational_tick(self._now_fn(), wait=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("scheduled research operational-tier tick failed", exc_info=True)
            if self._stopping:
                break
            await self._sleep_or_wake(self._tick_interval_ms, event=self._operational_wakeup)

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

    async def _sleep_or_wake(self, sleep_ms: int, *, event: asyncio.Event | None = None) -> None:
        wakeup = self._wakeup if event is None else event
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
            # update_run_state (not upsert): names only the lifecycle fields
            # this branch changed, so a pause/resume click landing in the same
            # instant is never reverted by this write.
            self._store.update_run_state(
                job.id,
                status=job.status,
                failure_kind=job.failure_kind,
                last_error=job.last_error,
                last_run_at=job.last_run_at,
            )
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
        # update_run_state (not upsert): a pause/resume click landing between
        # this write and the completion write at the end of this method must
        # survive both, not just be re-clobbered by whichever fires last.
        self._store.update_run_state(
            job.id,
            status=job.status,
            last_run_at=job.last_run_at,
            last_error=job.last_error,
        )

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
        # Set when the dispatch task itself is cancelled (executor shutdown past the drain grace,
        # or any other task.cancel()). The CancelledError still propagates — no completion is
        # persisted, recover_all_running_on_shutdown() owns the record — but the dispatch-done
        # log line and observability events below must not report it as completed. See
        # .claude/backlog/items/2026-09-08-dispatch-done-log-says-completed-for-a-cancelled-run.md
        interrupted = False
        try:
            # Bind this job's dispatch budget to the context BEFORE dispatching, so every LLM
            # retry ladder underneath it bounds itself by what is left (sidecar:
            # `trade_integrations.job_deadline`, per docs/FORK_CONVENTIONS.md — the logic lives in
            # Trade, this file only calls it). `asyncio.wait_for` below abandons the AWAIT on
            # timeout; it cannot stop work running in an `asyncio.to_thread` worker. Without a
            # budget the abandoned work keeps retrying — measured: one provider ladder can run
            # ~2.3 h inside a job whose ceiling was 20 minutes, which is how three India hub-news
            # jobs reached three consecutive dispatch timeouts and auto-paused.
            # See .claude/backlog/items/2026-09-07-collection-job-timeout-and-llm-deadline-hardening.md
            with _job_dispatch_budget(timeout_ms / 1000.0):
                session_id = await asyncio.wait_for(
                    self._dispatch(job), timeout=timeout_ms / 1000.0
                )
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
            interrupted = True
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
            record_dispatch_duration(job, elapsed_ms)  # D11 expected-runtime history
            # A cancelled dispatch — task cancellation (`interrupted`) or a PipelineCancelledError
            # (`was_cancelled`) — reports `cancelled`, not `completed`: `dispatch_error is None`
            # alone cannot tell "finished cleanly" from "was stopped before it could fail".
            if interrupted or was_cancelled:
                _final_status = JobStatus.CANCELLED
            else:
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
        auto_paused = False
        if dispatch_error is None:
            job.status = (
                JobStatus.EXPIRED
                if job.end_at is not None and scheduled_next_run > job.end_at
                else JobStatus.COMPLETED
            )
            job.failure_kind = None
            if not was_cancelled:
                job.last_error = None
        else:
            # A "barren collection" — a hub-news ingest run that a shut gate
            # stopped before it collected anything — is a real failure (it
            # increments consecutive_failures, takes backoff, and alerts like
            # any other) but is deliberately never terminal. The obvious way to
            # hit the threshold with it is a sustained LLM-Wiki outage, and
            # auto-pausing the daily ingest on the sole-collector tier for that
            # is the same terminal-silencing shape
            # 2026-09-07-nifty-ingest-jobs-terminally-failed was filed about,
            # arriving from the opposite direction. Decided 2026-09-08; see
            # .claude/backlog/items/2026-09-07-zero-total-ingest-failure-path-unobserved-in-production.md
            # § Decision. Only the auto-pause is exempted — nothing else.
            barren_collection = isinstance(dispatch_error, HubNewsIngestCollectedNothingError)
            job.failure_kind = "barren_collection" if barren_collection else "dispatch"
            job.last_error = _persisted_error(dispatch_error)
            if not barren_collection and job.consecutive_failures >= self._max_consecutive_failures:
                # A recurring job (every job in this store carries a schedule)
                # must not go terminal here: JobStatus.FAILED is excluded from
                # is_due() forever, which turns a transient dispatch outage
                # into permanent silence with `next_run_at` frozen in the
                # past and no health signal anywhere (see
                # 2026-09-07-nifty-ingest-jobs-terminally-failed). Auto-pause
                # instead, using the same paused/auto_paused_reason marker
                # recover_all_running_on_shutdown() uses for shutdown
                # recovery — already surfaced by check_dev_ports.py's stuck-job
                # check and the operator UI, and already resumable via the
                # ordinary enable/resume path in pause_control.set_job_enabled.
                job.status = JobStatus.PENDING
                job.paused = True
                job.auto_paused_reason = (
                    f"auto-paused: {job.consecutive_failures} consecutive "
                    f"dispatch failures (last: {job.last_error})"
                )
                auto_paused = True
                logger.error(
                    "scheduled research job %s auto-paused after %d consecutive "
                    "dispatch failures; resume manually after investigating "
                    "(last_error=%s)",
                    job.id,
                    job.consecutive_failures,
                    job.last_error,
                )
            else:
                job.status = JobStatus.PENDING
                retry_delay = self._retry_delay_ms(job.consecutive_failures)
                job.next_run_at = max(scheduled_next_run, now_ms + retry_delay)
                if job.end_at is not None and job.next_run_at > job.end_at:
                    job.status = JobStatus.EXPIRED
                if barren_collection and job.consecutive_failures >= self._max_consecutive_failures:
                    # Past the threshold and still retrying: say so explicitly
                    # rather than printing a misleading "failure 4/2", so an
                    # operator reading the log sees the exemption at work
                    # instead of assuming the auto-pause silently broke.
                    logger.error(
                        "scheduled research job %s has collected nothing %d consecutive times "
                        "(threshold %d); retrying at %d rather than auto-pausing — a barren "
                        "collection is exempt from the terminal disable, so investigate the "
                        "gate (last_error=%s)",
                        job.id,
                        job.consecutive_failures,
                        self._max_consecutive_failures,
                        job.next_run_at,
                        job.last_error,
                    )
                else:
                    logger.warning(
                        "scheduled research job %s will retry after failure %d/%d at %d",
                        job.id,
                        job.consecutive_failures,
                        self._max_consecutive_failures,
                        job.next_run_at,
                    )
        self._persist_completion(job, extra_fields={"paused": True, "auto_paused_reason": job.auto_paused_reason} if auto_paused else None)

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
            self._store.update_run_state(current.id, delivery=current.delivery)

            try:
                receipt = await self._channel_sender(
                    job.delivery_channel, job.delivery_target, text
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("briefing delivery failed for job %s", job.id, exc_info=True)
                self._mark_delivery_failed(current, exc, retryable=True)
                changed += 1
                continue

            if receipt is None:
                # Compatibility for pre-receipt injected senders used by
                # embedders and older tests. Production adapters always return
                # an explicit receipt through BaseChannel.send_with_receipt.
                current.delivery.status = DeliveryStatus.SENT
                current.delivery.provider_message_id = None
            elif receipt.status == "sent":
                current.delivery.status = DeliveryStatus.SENT
                current.delivery.provider_message_id = receipt.provider_message_id
            else:
                current.delivery.status = DeliveryStatus.ACCEPTED
                current.delivery.provider_message_id = receipt.provider_message_id
            current.delivery.error = None
            current.delivery.updated_at = self._now_fn()
            # The verdict rides the same write as the terminal delivery state,
            # never a second pass over the job (#1140's clobbering lesson).
            if (
                current.last_verdict is None
                or current.last_verdict.session_id != current.delivery.session_id
            ):
                self._record_verdict_on(current, current.delivery.session_id, text, now)
            self._store.update_run_state(
                current.id, delivery=current.delivery, last_verdict=current.last_verdict
            )
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
        self._store.update_run_state(job.id, last_verdict=job.last_verdict)
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
                self._store.update_run_state(job.id, delivery=job.delivery)
                return
        job.delivery.status = DeliveryStatus.FAILED
        self._store.update_run_state(job.id, delivery=job.delivery)

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

    def _persist_completion(
        self, job: ScheduledResearchJob, extra_fields: dict | None = None
    ) -> None:
        """Write a finished job back, unless it was changed during dispatch.

        Dispatch is awaited, so a concurrent DELETE or POST for the same id can
        land while a run is in flight. Reload first: if the record is gone the
        user cancelled it (do not resurrect), and if it is a different record
        (replaced via POST) let the new definition own its lifecycle. Only
        persist our completion when it still refers to the same scheduled run.

        Args:
            extra_fields: Additional run-state fields this run cycle owns
                beyond the usual set below — currently only used to stamp
                ``paused``/``auto_paused_reason`` when a repeated dispatch
                failure auto-pauses the job (see the terminal-failure branch
                in :meth:`_run_job`). ``None`` (the default) leaves those two
                fields untouched, exactly as before, so an ordinary
                completion cannot revert a concurrent pause/resume click.
        """
        # Deliberately keyed on "shutdown recovery has already finalised the
        # store", not on "shutdown has begun". The guard used to be
        # ``if self._stopping``, which threw away the verdict of *any* run
        # finishing after ``stop()`` was entered — including one that finished
        # milliseconds later and had genuinely succeeded. That is what filed two
        # real, successful ingests (``had_work: True``) as
        # ``recovered on executor shutdown`` on 2026-09-07. Between the start of
        # ``stop()`` and its recovery call there is now a bounded drain
        # (:meth:`_drain_in_flight_dispatches`) precisely so those completions
        # can land, so discarding them there is exactly backwards. Once recovery
        # *has* run, the ``current.status != JobStatus.RUNNING`` check below
        # already rejects a late write on its own; this flag just makes that
        # intent explicit and gives it a legible log line. See
        # .claude/backlog/items/2026-09-07-shutdown-recovery-relabels-a-completed-run.md.
        if self._shutdown_recovery_done:
            logger.info(
                "scheduled research job %s finished after shutdown recovery; skipping completion write",
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
        # update_run_state (not upsert): `job` is the snapshot dispatch started
        # from, so writing it back whole would silently revert `current`'s
        # `paused`/`auto_paused_reason` if a pause/resume click landed while
        # this dispatch was in flight. Naming only the fields this run cycle
        # actually owns keeps that click intact.
        self._store.update_run_state(
            job.id,
            status=job.status,
            next_run_at=job.next_run_at,
            last_run_at=job.last_run_at,
            consecutive_failures=job.consecutive_failures,
            last_error=job.last_error,
            failure_kind=job.failure_kind,
            delivery=job.delivery,
            last_verdict=job.last_verdict,
            last_result_summary=job.last_result_summary,
            config=job.config,
            **(extra_fields or {}),
        )
