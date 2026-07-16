"""Scheduled unified hub calibration and maintenance jobs."""

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

HUB_CALIBRATION_ENABLE_SCHEDULER_ENV = "HUB_CALIBRATION_ENABLE_SCHEDULER"
HUB_CALIBRATION_UNIFIED_ENV = "HUB_CALIBRATION_UNIFIED"
HUB_MORNING_CALIBRATION_CRON_ENV = "HUB_MORNING_CALIBRATION_CRON"
HUB_EVENING_MAINTENANCE_CRON_ENV = "HUB_EVENING_MAINTENANCE_CRON"
DEFAULT_MORNING_CRON = "0 6 * * *"
DEFAULT_EVENING_CRON = "35 18 * * *"

JOB_TYPE_HUB_MORNING_CALIBRATION = "hub_morning_calibration"
JOB_TYPE_HUB_EVENING_MAINTENANCE = "hub_evening_maintenance"

HUB_CALIBRATION_JOB_TYPES = frozenset({
    JOB_TYPE_HUB_MORNING_CALIBRATION,
    JOB_TYPE_HUB_EVENING_MAINTENANCE,
})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_hub_unified_calibration_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    raw = os.getenv(HUB_CALIBRATION_UNIFIED_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_hub_calibration_scheduler_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    explicit = os.getenv(HUB_CALIBRATION_ENABLE_SCHEDULER_ENV, "").strip().lower()
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


def run_hub_morning_calibration_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.hub_analytics.calibration_orchestrator import run_morning_hub_calibration

    summary = run_morning_hub_calibration(config or {})
    logger.info("hub morning calibration: %s", summary.get("status"))
    return summary


def run_hub_evening_maintenance_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.hub_analytics.calibration_orchestrator import run_evening_hub_maintenance

    summary = run_evening_hub_maintenance(config or {})
    logger.info("hub evening maintenance: %s", summary.get("status"))
    return summary


def dispatch_hub_calibration_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_HUB_MORNING_CALIBRATION:
        run_hub_morning_calibration_job(job.config)
        return
    if job_type == JOB_TYPE_HUB_EVENING_MAINTENANCE:
        run_hub_evening_maintenance_job(job.config)
        return
    raise ValueError(f"unsupported hub_calibration job_type: {job_type!r}")


async def dispatch_hub_calibration_job(job: ScheduledResearchJob) -> None:
    await asyncio.to_thread(dispatch_hub_calibration_job_sync, job)


def register_default_hub_calibration_jobs(store: ScheduledResearchJobStore) -> int:
    if not is_hub_calibration_scheduler_enabled():
        return 0

    created = 0
    now_ms = int(time.time() * 1000)

    morning_cron = os.getenv(HUB_MORNING_CALIBRATION_CRON_ENV, DEFAULT_MORNING_CRON).strip()
    validate_schedule(morning_cron)
    morning_id = "hub-morning-calibration"
    if store.get(morning_id) is None:
        store.upsert(
            ScheduledResearchJob(
                id=morning_id,
                prompt="Unified hub calibration: reconcile ledgers, export fills, retrain, manifest",
                schedule=morning_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_HUB_MORNING_CALIBRATION},
            )
        )
        logger.info("registered hub calibration job %s (%s)", morning_id, morning_cron)
        created += 1

    evening_cron = os.getenv(HUB_EVENING_MAINTENANCE_CRON_ENV, DEFAULT_EVENING_CRON).strip()
    validate_schedule(evening_cron)
    evening_id = "hub-evening-maintenance"
    if store.get(evening_id) is None:
        store.upsert(
            ScheduledResearchJob(
                id=evening_id,
                prompt="Unified hub maintenance: archive research history and refresh manifest",
                schedule=evening_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_HUB_EVENING_MAINTENANCE},
            )
        )
        logger.info("registered hub calibration job %s (%s)", evening_id, evening_cron)
        created += 1

    return created
