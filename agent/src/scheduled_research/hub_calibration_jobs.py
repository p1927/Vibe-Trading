"""Scheduled unified hub calibration and maintenance jobs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.config.accessor import get_env_config
from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule, DEFAULT_JOB_TIMEZONE
from src.scheduled_research.run_outcome import LAST_RESULT_CONFIG_KEY, finish_run  # noqa: F401 (re-export)
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


def _dispatch_hub_calibration_job_body(job: ScheduledResearchJob) -> None:
    """Attach the run's summary (``run_outcome.finish_run``; the executor caps it) and fail the run
    when the orchestrator's rolled-up ``status`` is ``error``. Both summaries carry a top-level
    ``status`` that ``_rollup_pipeline_status`` derives from every step, including the evening job's
    ``steps.scenario_autogen.by_ticker.<ticker>.errors``, which is otherwise only WARNING log lines.
    This used to attach and never read the status, so a rolled-up ``error`` was recorded
    ``completed`` (D86: every handler fails loud)."""
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_HUB_MORNING_CALIBRATION:
        finish_run(job, run_hub_morning_calibration_job(job.config), "hub morning calibration")
        return
    if job_type == JOB_TYPE_HUB_EVENING_MAINTENANCE:
        finish_run(job, run_hub_evening_maintenance_job(job.config), "hub evening maintenance")
        return
    raise ValueError(f"unsupported hub_calibration job_type: {job_type!r}")


def dispatch_hub_calibration_job_sync(job: ScheduledResearchJob) -> None:
    """Dispatch, with this run's step_clock lines (calibration_orchestrator/calibration_runner)
    also reported live into the Scheduler tab's log buffer -- see index_jobs.dispatch_index_job_sync
    for the identical pattern and rationale."""
    try:
        from trade_integrations.dataflows.index_research.pipeline_cancel import set_stage_sink
    except ImportError:
        _dispatch_hub_calibration_job_body(job)
        return

    from src.scheduled_research.run_log_buffer import append_log

    set_stage_sink(lambda message: append_log(job.id, message))
    try:
        _dispatch_hub_calibration_job_body(job)
    finally:
        set_stage_sink(None)


async def dispatch_hub_calibration_job(job: ScheduledResearchJob) -> None:
    # Both types run in a supervised child process (Trade D244): each is a run of 5-45 minutes of
    # calibration and LLM maintenance work that has hit its dispatch timeout on release.
    from src.scheduled_research.child_dispatch import in_child
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, in_child(dispatch_hub_calibration_job_sync))


def register_default_hub_calibration_jobs(store: ScheduledResearchJobStore) -> int:
    # D80: always register — "off" is the scheduler's own per-job pause, not
    # a never-registered/invisible family. See docs/DECISIONS.md D80.
    enabled = is_hub_calibration_scheduler_enabled()

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
                timezone=DEFAULT_JOB_TIMEZONE,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_HUB_MORNING_CALIBRATION},
                paused=not enabled,
                auto_paused_reason=None if enabled else "HUB_CALIBRATION_ENABLE_SCHEDULER disabled (D80)",
            )
        )
        logger.info("registered hub calibration job %s (%s, paused=%s)", morning_id, morning_cron, not enabled)
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
                timezone=DEFAULT_JOB_TIMEZONE,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_HUB_EVENING_MAINTENANCE},
                paused=not enabled,
                auto_paused_reason=None if enabled else "HUB_CALIBRATION_ENABLE_SCHEDULER disabled (D80)",
            )
        )
        logger.info("registered hub calibration job %s (%s, paused=%s)", evening_id, evening_cron, not enabled)
        created += 1

    return created
