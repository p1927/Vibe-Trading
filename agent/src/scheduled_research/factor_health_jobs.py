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

**Not** in ``job_tier_policy.COLLECTION_JOB_TYPES``: this collects nothing and costs no vendor
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

JOB_TYPE_FACTOR_HEALTH = "factor_health"
JOB_TYPE_FACTOR_HEALTH_LIVE = "factor_health_live"

FACTOR_HEALTH_JOB_TYPES = frozenset({JOB_TYPE_FACTOR_HEALTH, JOB_TYPE_FACTOR_HEALTH_LIVE})

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


def dispatch_factor_health_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_FACTOR_HEALTH:
        run_factor_health_job(job.config)
        return
    if job_type == JOB_TYPE_FACTOR_HEALTH_LIVE:
        run_factor_health_job({**job.config, "include_live_freshness": True})
        return
    raise ValueError(f"unsupported factor_health job_type: {job_type!r}")


async def dispatch_factor_health_job(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, dispatch_factor_health_job_sync)


def register_default_factor_health_jobs(store: ScheduledResearchJobStore) -> int:
    if not is_factor_health_scheduler_enabled():
        return 0

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
            )
        )
        logger.info("registered factor health job factor-health (%s)", daily_cron)
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
            )
        )
        logger.info("registered factor health job factor-health-live (%s)", live_cron)
        created += 1

    return created
