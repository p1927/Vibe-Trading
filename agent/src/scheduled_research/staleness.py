"""Stale-run detection and watchdog tuning for the scheduled-research executor.

Fork-only sidecar for ``agent/src/scheduled_research/executor.py`` (an
upstream file): everything here is pure, env/config-driven, and takes its
inputs as explicit parameters — no dependency on
``ScheduledResearchExecutor``'s own instance state — so it lives in its own
module rather than being spliced into upstream's executor class and
functions. Re-exported from ``executor.py`` so existing internal call sites
and external importers (``lifecycle.py``, ``index_prediction_jobs.py``,
``scheduled_routes.py``) are unaffected.
"""

from __future__ import annotations

import logging
import time

from src.config.accessor import get_env_config
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)

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
# Defer the first run of every freshly-registered job by this many ms so that
# a uvicorn --reload worker restart does not refire the entire scheduler on
# every code save. The default of 30 min is shorter than every default cron in
# this codebase and longer than any realistic dev iteration loop.
DEFAULT_FRESH_REGISTRATION_DEFER_MS = 30 * 60 * 1000
FRESH_REGISTRATION_DEFER_ENV = "SCHEDULED_RESEARCH_FRESH_DEFER_MS"

_JOB_DISPATCH_TIMEOUT_MS: dict[str, int] = {
    "index_plan_refresh": 10 * 60 * 1000,
    "index_factor_snapshot": 60 * 60 * 1000,
    "hub_news_entity": 20 * 60 * 1000,
    "hub_news_ingest": 10 * 60 * 1000,
}
_INDEX_JOB_DISPATCH_TIMEOUT_MS = 30 * 60 * 1000


def _autonomous_watch_target_running(job: ScheduledResearchJob) -> bool:
    """Return whether the agent bound to an autonomous_agent_watch job is running.

    Used by :meth:`ScheduledResearchExecutor.defer_startup_backlog` to skip
    watches whose agent is not running — otherwise every reload would fire a
    ``run_watch_tick`` that immediately returns ``agent_not_running`` and
    re-deploys the same dead path on every subsequent tick until the schedule
    catches up.
    """
    agent_id = str((job.config or {}).get("autonomous_agent_id") or "").strip()
    if not agent_id:
        # Legacy watch job without an explicit agent id — fall through; the
        # dispatcher handles the missing-id case.
        return True
    try:
        from trade_integrations.autonomous_agents.store import get_agent
    except ImportError:
        return True
    try:
        agent = get_agent(agent_id)
    except Exception:
        logger.debug("autonomous agent lookup failed for %s", agent_id, exc_info=True)
        return True
    return bool(agent) and str(agent.get("status") or "") == "running"


def _startup_grace_ms() -> int:
    """Delay before the first executor tick so the API can serve health checks."""
    raw = get_env_config().trade.scheduled_research_startup_grace_ms.strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_STARTUP_GRACE_MS


def _fresh_registration_defer_ms() -> int:
    """Return the defer window (ms) for first-run jobs after a hot reload.

    Pushing the first run by this much stops uvicorn --reload from re-firing
    every default scheduled job back-to-back on every code save. Set to 0 to
    restore the pre-fix behaviour (every reload fires everything).
    """
    raw = get_env_config().trade.scheduled_research_fresh_defer_ms.strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_FRESH_REGISTRATION_DEFER_MS


def defer_fresh_registrations(
    store: ScheduledResearchJobStore,
    *,
    now_ms: int | None = None,
    defer_ms: int | None = None,
) -> int:
    """Push ``next_run_at`` forward for never-executed PENDING jobs.

    Every ``register_default_*`` helper stamps ``next_run_at=now_ms`` so a
    fresh job fires immediately on the next tick. On uvicorn --reload this
    means every code save re-stamps every default job and the first executor
    tick dispatches them all, cascading LLM calls and IO before the user types
    anything. Calling this once after registration defers their first run by
    a configurable grace window (default 30 min) so the scheduler only fires
    on the persisted cron schedule, not on every reload.

    Returns the number of jobs deferred.
    """
    if defer_ms is None:
        defer_ms = _fresh_registration_defer_ms()
    if defer_ms <= 0:
        return 0
    now = int(time.time() * 1000) if now_ms is None else now_ms
    jobs = store.load()
    deferred = 0
    for job in jobs.values():
        if job.status != JobStatus.PENDING:
            continue
        # A job that has fired at least once already is on its real schedule;
        # do not push it back or we lose cadence.
        if job.last_run_at is not None:
            continue
        # recording_wake jobs are a single cheap status-flip + subprocess
        # spawn, not an LLM-cost cascade risk — the thing this defer exists
        # to prevent. Deferring them here means a backend restart while the
        # market is already open (or close to opening) pushes the wake
        # another `defer_ms` into the future for no reason, leaving a
        # wait_for_open recording stuck in "waiting_for_open" long after
        # the market opened. Exempt them so they fire at their real,
        # already-computed `next_open_at` deadline.
        if str(job.config.get("job_type") or "") == "recording_wake":
            continue
        if job.next_run_at >= now + defer_ms:
            continue
        job.next_run_at = now + defer_ms
        deferred += 1
        logger.info(
            "deferring fresh registration of scheduled job %s to %s",
            job.id,
            job.next_run_at,
        )
    if deferred:
        store.save(jobs)
    return deferred


def _stale_running_ms() -> int:
    raw = get_env_config().trade.scheduled_research_stale_running_ms.strip()
    try:
        return max(60_000, int(raw))
    except ValueError:
        return DEFAULT_STALE_RUNNING_MS


def _index_plan_refresh_stale_ms() -> int:
    raw = get_env_config().trade.index_plan_refresh_stale_ms.strip()
    try:
        return max(60_000, int(raw))
    except ValueError:
        return DEFAULT_INDEX_PLAN_REFRESH_STALE_MS


def _watchdog_buffer_ms() -> int:
    """Buffer above the dispatch timeout for stale-running recovery.

    The watchdog fires on a separate interval (default 60s) and recovers any
    RUNNING job whose ``last_run_at`` is older than
    :func:`stale_running_ms_for`. If the stale threshold is shorter than the
    dispatch timeout (or the dispatch + cleanup), the watchdog can recover a
    still-cleaning-up job, and ``_persist_completion`` silently discards the
    completion because ``current.status != RUNNING``. Guarantee a buffer of
    one watchdog interval above the dispatch timeout so a dispatch can always
    finish and write its completion before the watchdog decides it's stale.
    """
    return max(_watchdog_interval_ms(), 60_000)


def stale_running_ms_for(job: ScheduledResearchJob) -> int:
    """Return stale threshold for *job* (poll jobs use a shorter window)."""
    job_type = str(job.config.get("job_type") or "")
    if job_type == "index_plan_refresh":
        return _index_plan_refresh_stale_ms()
    if job_type == "autonomous_agent_watch":
        try:
            interval = int(str(job.schedule).strip())
            return max(120_000, 2 * interval)
        except ValueError:
            return 120_000
    # Guarantee the stale threshold is at least ``dispatch_timeout + buffer``
    # so the watchdog cannot fire mid-cleanup. Without this, a long-running
    # hub-news drain (e.g. 20-min dispatch + LLM adjudication cleanup) would
    # be recovered after ``stale_running_ms`` (default 45 min) but before the
    # completion code path runs, silently dropping the ``last_run_at`` write
    # via the ``current.status != RUNNING`` guard in ``_persist_completion``.
    base = _stale_running_ms()
    return max(base, dispatch_timeout_ms_for(job) + _watchdog_buffer_ms())


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
    raw_env = get_env_config().trade.scheduled_research_dispatch_timeout_ms.strip()
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
    raw = get_env_config().trade.scheduled_research_watchdog_interval_ms.strip()
    try:
        return max(10_000, int(raw))
    except ValueError:
        return DEFAULT_WATCHDOG_INTERVAL_MS


def _default_failure_threshold() -> int:
    raw = get_env_config().trade.scheduled_research_failure_threshold.strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_FAILURE_THRESHOLD


def _failure_threshold_for(job: ScheduledResearchJob) -> int:
    raw = job.config.get("failure_threshold")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _default_failure_threshold()
