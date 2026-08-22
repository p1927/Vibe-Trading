"""Scheduled hub capture jobs (intraday chain snapshots)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.config.accessor import get_env_config
from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.hub_bridge import ensure_trade_stack_path

logger = logging.getLogger(__name__)

HUB_CAPTURE_ENABLE_SCHEDULER_ENV = "HUB_CAPTURE_ENABLE_SCHEDULER"
HUB_CAPTURE_INTRADAY_CRON_ENV = "HUB_CAPTURE_INTRADAY_CRON"
DEFAULT_INTRADAY_CRON = "0 10,13,15 * * 1-5"

# Flow/VIX snapshot cadence — more frequent than the option-chain job
# above since fii_net_5d/dii_net_5d/nifty_pcr/dii_absorption_ratio are
# pinned Ridge factors (see docs/factors-document.md); default mirrors the
# recorder's own flow_poller.py cadence (every 30 min during the session).
HUB_CAPTURE_FACTOR_SNAPSHOT_CRON_ENV = "HUB_CAPTURE_FACTOR_SNAPSHOT_CRON"
DEFAULT_FACTOR_SNAPSHOT_CRON = "*/30 9-16 * * 1-5"

JOB_TYPE_HUB_CAPTURE_INTRADAY = "hub_capture_intraday"
JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT = "hub_capture_factor_snapshot"

HUB_CAPTURE_JOB_TYPES = frozenset(
    {JOB_TYPE_HUB_CAPTURE_INTRADAY, JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT}
)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_hub_capture_scheduler_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    explicit = get_env_config().trade.hub_capture_enable_scheduler.strip().lower()
    if explicit in _TRUE_VALUES:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    from src.scheduled_research.index_jobs import is_index_scheduler_enabled

    return is_index_scheduler_enabled()


def _ensure_trade_integrations_on_path() -> None:
    ensure_trade_stack_path()


def run_hub_capture_intraday_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.hub_capture.intraday import run_intraday_capture

    entity_id = str((config or {}).get("entity_id") or "NIFTY").upper()
    summary = run_intraday_capture(entity_id=entity_id)
    logger.info("hub capture intraday: %s", summary.get("status"))
    return summary


def run_hub_capture_factor_snapshot_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.hub_capture.factor_snapshot import run_factor_snapshot_capture

    entity_id = (config or {}).get("entity_id")
    summary = run_factor_snapshot_capture(entity_id=str(entity_id).upper() if entity_id else None)
    logger.info("hub capture factor snapshot: %s", summary.get("status"))
    return summary


def dispatch_hub_capture_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_HUB_CAPTURE_INTRADAY:
        run_hub_capture_intraday_job(job.config)
        return
    if job_type == JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT:
        run_hub_capture_factor_snapshot_job(job.config)
        return
    raise ValueError(f"unsupported hub_capture job_type: {job_type!r}")


async def dispatch_hub_capture_job(job: ScheduledResearchJob) -> None:
    await asyncio.to_thread(dispatch_hub_capture_job_sync, job)


def register_default_hub_capture_jobs(store: ScheduledResearchJobStore) -> int:
    if not is_hub_capture_scheduler_enabled():
        return 0
    now_ms = int(time.time() * 1000)
    registered = 0

    cron = get_env_config().trade.hub_capture_intraday_cron.strip()
    validate_schedule(cron)
    job_id = "hub-capture-intraday"
    if store.get(job_id) is None:
        store.upsert(
            ScheduledResearchJob(
                id=job_id,
                prompt="Hub capture: intraday NIFTY option chain snapshots for proprietary factor history",
                schedule=cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_HUB_CAPTURE_INTRADAY, "entity_id": "NIFTY"},
            )
        )
        logger.info("registered hub capture job %s (%s)", job_id, cron)
        registered += 1

    # Independent of the intraday job above — registered separately so
    # deployments that already have "hub-capture-intraday" (from before this
    # job existed) still pick up the new one instead of short-circuiting.
    factor_cron = get_env_config().trade.hub_capture_factor_snapshot_cron.strip()
    validate_schedule(factor_cron)
    factor_job_id = "hub-capture-factor-snapshot"
    if store.get(factor_job_id) is None:
        store.upsert(
            ScheduledResearchJob(
                id=factor_job_id,
                prompt="Hub capture: FII/DII flow + India VIX snapshots for proprietary factor history",
                schedule=factor_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT, "entity_id": "NIFTY"},
            )
        )
        logger.info("registered hub capture job %s (%s)", factor_job_id, factor_cron)
        registered += 1

    return registered
