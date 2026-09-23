"""Scheduled factor-registry health check — the caller `status_honesty()` never had.

Fork-only sidecar (see ``docs/FORK_CONVENTIONS.md``), modelled on ``dst_eval_jobs.py``: a new
module plus one loader entry in ``job_dispatch_registry.py``, nothing interleaved into upstream
code.

Why this exists: ``factors/acquisition/registry_health.py:status_honesty()`` and
``factors/freshness.py`` both measure real drift correctly and neither was ever called by
anything on a schedule, so a catalog claiming ``FactorStatus.RECORDED`` for 163 specs with no
data anywhere went unnoticed. See
``.claude/backlog/items/2026-09-06-recorded-status-honesty.md``.

Two jobs, split by cost:

- ``factor_health`` (daily) reads only what is already on disk — the whole hub scan is a few
  seconds — and raises on a factor that claims ``RECORDED`` while nothing is stored for it in any
  physical home. That is a real regression, so it fails the run loudly rather than logging.
- ``factor_health_live`` (weekly) additionally calls every ``RECORDED`` factor's live source,
  which takes minutes and hits external vendors. Off by default; set
  ``FACTOR_HEALTH_LIVE_CRON`` to schedule it.

- ``factor_reference_check`` (daily, Trade DECISIONS D222) compares each venue's stored reference
  calendar factor against a fresh vendor fetch (``factors/plan.py:check_references()``, through
  ``acquire()`` like any factor fetch) and queues the reference's missing days as ordinary gap
  jobs. The gap planner reads the store only, so this is the only thing that audits the reference
  itself. It **is** in ``COLLECTION_JOB_TYPES`` (release-only, D60): it calls vendors to write work
  into the hub's gap queue, and dev's hub is a mirror of release's. A failed fetch fails the run
  (D103). Override the window with ``config.lookback_days`` (default 90).

- ``factor_gap_fill`` (every 3 h, Trade DECISIONS D287) is the scheduled refresh that asks the gap
  planner (Trade D207(4)): ``factors/plan.py:scheduled_gap_fill()`` plans the last
  ``config.lookback_days`` (default 3650) into the queue and drains at most ``config.drain_limit``
  factors (default 10) through the one dispatch path. Release-only collection (vendor fetches into
  release's hub), and it runs in the supervised child (D244/D262): a drain is minutes of fetch and
  parquet work. A drained factor that failed makes the run failed after the others ran (D36/D282).
  ``FACTOR_GAP_FILL_CRON`` overrides the schedule.

**Not** in ``job_tier_policy.COLLECTION_JOB_TYPES`` (the two ``factor_health*`` types): this collects nothing and costs no vendor
quota in its daily form — it is an observability check over data already held, the same category
as the ``*_eval`` job types that are deliberately allowed to run in dev. It is meaningful on both
tiers because each reads its own ``TRADE_STACK_HUB_DIR`` (release's real hub; dev's mirror), and
the report records which profile and hub it checked.

Enabled by DEFAULT, unlike ``dst_eval_jobs``. A check that has to be switched on is a check that
decays back to having no caller, which is the exact failure being fixed. Set
``FACTOR_HEALTH_ENABLE_SCHEDULER=0`` to turn it off.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.hub_bridge import ensure_trade_stack_path

logger = logging.getLogger(__name__)

FACTOR_HEALTH_ENABLE_SCHEDULER_ENV = "FACTOR_HEALTH_ENABLE_SCHEDULER"
FACTOR_HEALTH_CRON_ENV = "FACTOR_HEALTH_CRON"
FACTOR_HEALTH_LIVE_CRON_ENV = "FACTOR_HEALTH_LIVE_CRON"

#: 07:10 local, after the overnight recorders have written but early enough that a stopped writer
#: is visible the same morning.
DEFAULT_FACTOR_HEALTH_CRON = "10 7 * * *"
FACTOR_REFERENCE_CHECK_CRON_ENV = "FACTOR_REFERENCE_CHECK_CRON"
#: 06:40 local: after the overnight EOD refreshes, and before the 07:10 health pass.
DEFAULT_FACTOR_REFERENCE_CHECK_CRON = "40 6 * * *"

JOB_TYPE_FACTOR_HEALTH = "factor_health"
JOB_TYPE_FACTOR_HEALTH_LIVE = "factor_health_live"
JOB_TYPE_FACTOR_REFERENCE_CHECK = "factor_reference_check"
JOB_TYPE_FACTOR_GAP_FILL = "factor_gap_fill"

FACTOR_GAP_FILL_CRON_ENV = "FACTOR_GAP_FILL_CRON"
#: Every 3 h at :20, after the 06:40 reference check has queued its days; ten factors a run works the
#: queue down in a few days without a run outgrowing its budget.
DEFAULT_FACTOR_GAP_FILL_CRON = "20 */3 * * *"
#: The run's dispatch budget (and the child's timeout). Its leases outlive it, so a killed run's jobs
#: return to the queue only once they lapse.
FACTOR_GAP_FILL_TIMEOUT_MS = 45 * 60 * 1000

FACTOR_HEALTH_JOB_TYPES = frozenset(
    {JOB_TYPE_FACTOR_HEALTH, JOB_TYPE_FACTOR_HEALTH_LIVE, JOB_TYPE_FACTOR_REFERENCE_CHECK,
     JOB_TYPE_FACTOR_GAP_FILL}
)

_FALSE_VALUES = {"0", "false", "no", "off"}


def is_factor_health_scheduler_enabled(value: str | None = None) -> bool:
    """Default ON — only an explicit falsey value disables it. See the module docstring."""
    raw = value if value is not None else os.environ.get(FACTOR_HEALTH_ENABLE_SCHEDULER_ENV, "")
    return raw.strip().lower() not in _FALSE_VALUES


def run_factor_health_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the check and RAISE on a new ``RECORDED``-but-absent factor.

    Deliberately not the non-blocking "catch and return a summary" shape the eval jobs use: those
    report on quality, this one detects a catalog that has started lying about itself, which the
    scheduler should surface as a failed run rather than bury in a summary field.
    """
    ensure_trade_stack_path()
    from trade_integrations.factors.health_check import (
        health_summary_line,
        run_and_raise_on_regression,
    )

    live = bool((config or {}).get("include_live_freshness"))
    report = run_and_raise_on_regression(include_live_freshness=live)
    logger.info("factor health: %s", health_summary_line(report))
    stale = report["stale_on_disk"]
    if stale:
        logger.warning(
            "factor health: %d RECORDED factors have a stopped writer (oldest %s, %s) -- each "
            "cluster is tracked in .claude/backlog/",
            len(stale),
            stale[0]["factor"],
            stale[0]["last_day"],
        )
    return report


def run_factor_reference_check_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """D222: each stored reference calendar vs a fresh vendor fetch; missing days are queued."""
    ensure_trade_stack_path()
    from datetime import date, timedelta

    from trade_integrations.factors.plan import check_references

    end = date.today()
    start = end - timedelta(days=int((config or {}).get("lookback_days") or 90))
    report = check_references(start=start.isoformat(), end=end.isoformat())
    logger.info(
        "factor reference check %s..%s: %d reference(s) incomplete %s, %d job(s) queued %s, errors %s",
        start, end, len(report["references_incomplete"]), report["references_incomplete"],
        report["jobs"], report["enqueued"], report["errors"] or "none",
    )
    return report


def run_factor_gap_fill_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """D287: one bounded plan + drain pass over release's gap queue."""
    ensure_trade_stack_path()
    from trade_integrations.factors.plan import scheduled_gap_fill

    config = config or {}
    report = scheduled_gap_fill(
        lookback_days=int(config.get("lookback_days") or 3650),
        drain_limit=int(config.get("drain_limit") or 10),
        lease_seconds=int(config.get("lease_seconds") or FACTOR_GAP_FILL_TIMEOUT_MS // 1000 + 600),
    )
    logger.info(
        "factor gap fill %s..%s: %d factor(s) with open gaps (%d day(s)), enqueued %s; drained %d "
        "factor(s) %s, %d day(s) filled; failures %s",
        report["window"]["start"], report["window"]["end"], report["plan"]["factors_with_open_gaps"],
        report["plan"]["open_gap_days"], report["enqueued"], report["factors_drained"],
        report["outcomes"], report["days_filled"], report["failures"] or "none",
    )
    return report


def dispatch_factor_health_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_FACTOR_GAP_FILL:
        from src.scheduled_research.index_jobs import LAST_RESULT_CONFIG_KEY
        from src.scheduled_research.run_outcome import raise_if_run_had_errors

        report = run_factor_gap_fill_job(job.config)
        job.config[LAST_RESULT_CONFIG_KEY] = {
            k: report[k] for k in ("status", "window", "enqueued", "factors_drained", "outcomes",
                                   "days_filled", "failures")
        }
        raise_if_run_had_errors(job, report, "factor gap fill")
        return
    if job_type == JOB_TYPE_FACTOR_REFERENCE_CHECK:
        from src.scheduled_research.run_outcome import raise_if_run_had_errors

        raise_if_run_had_errors(job, run_factor_reference_check_job(job.config), "factor reference check")
        return
    if job_type == JOB_TYPE_FACTOR_HEALTH:
        run_factor_health_job(job.config)
        return
    if job_type == JOB_TYPE_FACTOR_HEALTH_LIVE:
        run_factor_health_job({**job.config, "include_live_freshness": True})
        return
    raise ValueError(f"unsupported factor_health job_type: {job_type!r}")


async def dispatch_factor_health_job(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.child_dispatch import in_child
    from src.scheduled_research.run_log_buffer import run_logged

    heavy = str(job.config.get("job_type") or "") == JOB_TYPE_FACTOR_GAP_FILL  # D244/D262
    await run_logged(job, in_child(dispatch_factor_health_job_sync) if heavy else dispatch_factor_health_job_sync)


def register_default_factor_health_jobs(store: ScheduledResearchJobStore) -> int:
    # D80: always register — "off" is the scheduler's own per-job pause, not a
    # never-registered/invisible family. See docs/DECISIONS.md D80.
    enabled = is_factor_health_scheduler_enabled()
    reason = None if enabled else "FACTOR_HEALTH_ENABLE_SCHEDULER disabled (D80)"

    created = 0
    now_ms = int(time.time() * 1000)

    daily_cron = (os.environ.get(FACTOR_HEALTH_CRON_ENV) or DEFAULT_FACTOR_HEALTH_CRON).strip()
    validate_schedule(daily_cron)
    if store.get("factor-health") is None:
        store.upsert(
            ScheduledResearchJob(
                id="factor-health",
                prompt=(
                    "Factor registry health: status honesty across every physical home, plus "
                    "on-disk staleness for every RECORDED factor"
                ),
                schedule=daily_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_FACTOR_HEALTH},
                paused=not enabled,
                auto_paused_reason=reason,
            )
        )
        logger.info("registered factor health job factor-health (%s, paused=%s)", daily_cron, not enabled)
        created += 1

    reference_cron = (
        os.environ.get(FACTOR_REFERENCE_CHECK_CRON_ENV) or DEFAULT_FACTOR_REFERENCE_CHECK_CRON
    ).strip()
    validate_schedule(reference_cron)
    if store.get("factor-reference-check") is None:
        store.upsert(
            ScheduledResearchJob(
                id="factor-reference-check",
                prompt=(
                    "Reference calendars (D222): each venue's stored reference factor vs a fresh "
                    "vendor fetch; queue the reference's missing days as gap jobs"
                ),
                schedule=reference_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_FACTOR_REFERENCE_CHECK},
                paused=not enabled,
                auto_paused_reason=reason,
            )
        )
        logger.info(
            "registered factor reference check job factor-reference-check (%s, paused=%s)",
            reference_cron, not enabled,
        )
        created += 1

    gap_fill_cron = (os.environ.get(FACTOR_GAP_FILL_CRON_ENV) or DEFAULT_FACTOR_GAP_FILL_CRON).strip()
    validate_schedule(gap_fill_cron)
    if store.get("factor-gap-fill") is None:
        store.upsert(
            ScheduledResearchJob(
                id="factor-gap-fill",
                prompt=(
                    "Gap fill (D287): plan the missing days/periods of every factor into the queue, "
                    "then drain a bounded number of factors through the one dispatch path"
                ),
                schedule=gap_fill_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_FACTOR_GAP_FILL, "lookback_days": 3650, "drain_limit": 10,
                        "dispatch_timeout_ms": FACTOR_GAP_FILL_TIMEOUT_MS},
                paused=not enabled,
                auto_paused_reason=reason,
            )
        )
        logger.info("registered factor gap fill job factor-gap-fill (%s, paused=%s)", gap_fill_cron, not enabled)
        created += 1

    # Opt-in only: this one calls every RECORDED factor's live source.
    live_cron = (os.environ.get(FACTOR_HEALTH_LIVE_CRON_ENV) or "").strip()
    if live_cron and store.get("factor-health-live") is None:
        validate_schedule(live_cron)
        store.upsert(
            ScheduledResearchJob(
                id="factor-health-live",
                prompt="Factor registry health + live-vendor freshness pass over every RECORDED factor",
                schedule=live_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_FACTOR_HEALTH_LIVE},
                paused=not enabled,
                auto_paused_reason=reason,
            )
        )
        logger.info("registered factor health job factor-health-live (%s, paused=%s)", live_cron, not enabled)
        created += 1

    return created
