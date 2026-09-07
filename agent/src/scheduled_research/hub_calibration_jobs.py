"""Scheduled unified hub calibration and maintenance jobs."""

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

HUB_CALIBRATION_ENABLE_SCHEDULER_ENV = "HUB_CALIBRATION_ENABLE_SCHEDULER"
HUB_CALIBRATION_UNIFIED_ENV = "HUB_CALIBRATION_UNIFIED"
HUB_MORNING_CALIBRATION_CRON_ENV = "HUB_MORNING_CALIBRATION_CRON"
HUB_EVENING_MAINTENANCE_CRON_ENV = "HUB_EVENING_MAINTENANCE_CRON"
DEFAULT_MORNING_CRON = "0 6 * * *"
DEFAULT_EVENING_CRON = "35 18 * * *"

JOB_TYPE_HUB_MORNING_CALIBRATION = "hub_morning_calibration"
JOB_TYPE_HUB_EVENING_MAINTENANCE = "hub_evening_maintenance"

#: Same scratch key `index_jobs.py`'s `_attach_job_result_summary` writes and
#: `executor.py`'s `_dispatch` pops into `job.last_result_summary` after a successful
#: dispatch. Reusing the established name/mechanism rather than inventing a second one for
#: this module -- see .claude/backlog/items/2026-09-07-scenario-autogen-stale-pipeline-bind.md
#: (its errors-must-surface requirement is what this wiring satisfies).
LAST_RESULT_CONFIG_KEY = "_last_result_summary"

HUB_CALIBRATION_JOB_TYPES = frozenset({
    JOB_TYPE_HUB_MORNING_CALIBRATION,
    JOB_TYPE_HUB_EVENING_MAINTENANCE,
})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_hub_unified_calibration_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    raw = get_env_config().trade.hub_calibration_unified.strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_hub_calibration_scheduler_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    explicit = get_env_config().trade.hub_calibration_enable_scheduler.strip().lower()
    if explicit in _TRUE_VALUES:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    from src.scheduled_research.index_jobs import is_index_scheduler_enabled

    return is_index_scheduler_enabled()


def _ensure_trade_integrations_on_path() -> None:
    ensure_trade_stack_path()


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


def _attach_job_result_summary(job: ScheduledResearchJob, summary: dict[str, Any]) -> None:
    """Write `summary` into `job.config[LAST_RESULT_CONFIG_KEY]` so the executor surfaces it
    as `job.last_result_summary` once dispatch completes. Both the morning and evening
    summaries already carry a top-level `status` plus a `steps` dict with one entry per
    sub-step (`_rollup_pipeline_status` rolls sub-step status into it) -- including, for the
    evening job, `steps.scenario_autogen.by_ticker.<ticker>.errors`, which is otherwise only
    visible as WARNING log lines. Attached whole (not compacted) since these summaries are
    small, bounded by the fixed set of daily steps -- unlike the high-volume ingest jobs in
    `index_jobs.py` that need `_compact_result_summary` to stay a reasonable size."""
    if isinstance(summary, dict):
        job.config[LAST_RESULT_CONFIG_KEY] = summary


def dispatch_hub_calibration_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_HUB_MORNING_CALIBRATION:
        summary = run_hub_morning_calibration_job(job.config)
        _attach_job_result_summary(job, summary)
        return
    if job_type == JOB_TYPE_HUB_EVENING_MAINTENANCE:
        summary = run_hub_evening_maintenance_job(job.config)
        _attach_job_result_summary(job, summary)
        return
    raise ValueError(f"unsupported hub_calibration job_type: {job_type!r}")


async def dispatch_hub_calibration_job(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, dispatch_hub_calibration_job_sync)


def register_default_hub_calibration_jobs(store: ScheduledResearchJobStore) -> int:
    if not is_hub_calibration_scheduler_enabled():
        return 0

    created = 0
    now_ms = int(time.time() * 1000)

    morning_cron = get_env_config().trade.hub_morning_calibration_cron.strip()
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

    evening_cron = get_env_config().trade.hub_evening_maintenance_cron.strip()
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
