"""Scheduled hub capture jobs (intraday chain snapshots)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)

HUB_CAPTURE_ENABLE_SCHEDULER_ENV = "HUB_CAPTURE_ENABLE_SCHEDULER"
HUB_CAPTURE_INTRADAY_CRON_ENV = "HUB_CAPTURE_INTRADAY_CRON"
DEFAULT_INTRADAY_CRON = "0 10,13,15 * * 1-5"

JOB_TYPE_HUB_CAPTURE_INTRADAY = "hub_capture_intraday"

HUB_CAPTURE_JOB_TYPES = frozenset({JOB_TYPE_HUB_CAPTURE_INTRADAY})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_hub_capture_scheduler_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    explicit = os.getenv(HUB_CAPTURE_ENABLE_SCHEDULER_ENV, "").strip().lower()
    if explicit in _TRUE_VALUES:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    from src.scheduled_research.index_jobs import is_index_scheduler_enabled

    return is_index_scheduler_enabled()


def _ensure_trade_integrations_on_path() -> None:
    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def run_hub_capture_intraday_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.hub_capture.intraday import run_intraday_capture

    entity_id = str((config or {}).get("entity_id") or "NIFTY").upper()
    summary = run_intraday_capture(entity_id=entity_id)
    logger.info("hub capture intraday: %s", summary.get("status"))
    return summary


def dispatch_hub_capture_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_HUB_CAPTURE_INTRADAY:
        run_hub_capture_intraday_job(job.config)
        return
    raise ValueError(f"unsupported hub_capture job_type: {job_type!r}")


async def dispatch_hub_capture_job(job: ScheduledResearchJob) -> None:
    await asyncio.to_thread(dispatch_hub_capture_job_sync, job)


def register_default_hub_capture_jobs(store: ScheduledResearchJobStore) -> int:
    if not is_hub_capture_scheduler_enabled():
        return 0
    cron = os.getenv(HUB_CAPTURE_INTRADAY_CRON_ENV, DEFAULT_INTRADAY_CRON).strip()
    validate_schedule(cron)
    job_id = "hub-capture-intraday"
    if store.get(job_id) is not None:
        return 0
    now_ms = int(time.time() * 1000)
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
    return 1
