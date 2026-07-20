"""Scheduled hub trade-data jobs (fills export, ledger materialization)."""

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

TRADE_DATA_ENABLE_SCHEDULER_ENV = "TRADE_DATA_ENABLE_SCHEDULER"
TRADE_FILLS_EXPORT_CRON_ENV = "TRADE_FILLS_EXPORT_CRON"
RESEARCH_HISTORY_ARCHIVE_CRON_ENV = "RESEARCH_HISTORY_ARCHIVE_CRON"
DEFAULT_FILLS_EXPORT_CRON = "0 19 * * *"
DEFAULT_RESEARCH_HISTORY_ARCHIVE_CRON = "35 18 * * *"
DEFAULT_NSE_MACRO_REFRESH_CRON = "15 6 * * *"
NSE_MACRO_REFRESH_CRON_ENV = "NSE_MACRO_REFRESH_CRON"

JOB_TYPE_TRADE_FILLS_EXPORT = "trade_fills_export"
JOB_TYPE_RESEARCH_HISTORY_ARCHIVE = "research_history_archive"
JOB_TYPE_NSE_MACRO_REFRESH = "nse_macro_refresh"

TRADE_DATA_JOB_TYPES = frozenset({
    JOB_TYPE_TRADE_FILLS_EXPORT,
    JOB_TYPE_RESEARCH_HISTORY_ARCHIVE,
    JOB_TYPE_NSE_MACRO_REFRESH,
})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_trade_data_scheduler_enabled(value: str | None = None) -> bool:
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    explicit = os.getenv(TRADE_DATA_ENABLE_SCHEDULER_ENV, "").strip().lower()
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


def run_research_history_archive_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from datetime import datetime, timezone

    from trade_integrations.context.hub import archive_options_stock_snapshots

    cfg = config or {}
    as_of_date = cfg.get("as_of_date")
    if not as_of_date:
        as_of_date = datetime.now(timezone.utc).date().isoformat()
    summary = archive_options_stock_snapshots(as_of_date=as_of_date)
    logger.info("research history archive: %s", summary)
    return summary


def run_trade_fills_export_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.env import load_trade_env
    from trade_integrations.hub_storage.openalgo_fills_export import export_openalgo_fills

    load_trade_env()
    cfg = config or {}
    summary = export_openalgo_fills(dry_run=bool(cfg.get("dry_run")))
    logger.info("trade fills export: %s", summary)
    return summary


def run_nse_macro_refresh_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.nse_browser.repository import ingest_repository_to_hub, sync_all_repo_seed_layers

    cfg = config or {}
    counts = sync_all_repo_seed_layers(explicit=True, allow_live_fetch=bool(cfg.get("allow_live_fetch", True)))
    repo_counts = ingest_repository_to_hub()
    summary = {"seed_layers": counts, "repository": repo_counts}
    logger.info("nse macro refresh: %s", summary)
    return summary


def dispatch_trade_data_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_TRADE_FILLS_EXPORT:
        run_trade_fills_export_job(job.config)
        return
    if job_type == JOB_TYPE_RESEARCH_HISTORY_ARCHIVE:
        run_research_history_archive_job(job.config)
        return
    if job_type == JOB_TYPE_NSE_MACRO_REFRESH:
        run_nse_macro_refresh_job(job.config)
        return
    raise ValueError(f"unsupported trade_data job_type: {job_type!r}")


async def dispatch_trade_data_job(job: ScheduledResearchJob) -> None:
    await asyncio.to_thread(dispatch_trade_data_job_sync, job)


def register_default_trade_data_jobs(store: ScheduledResearchJobStore) -> int:
    """Register nightly fills export and research history archive jobs."""
    try:
        from src.scheduled_research.hub_calibration_jobs import (
            is_hub_calibration_scheduler_enabled,
            is_hub_unified_calibration_enabled,
        )

        if is_hub_calibration_scheduler_enabled() and is_hub_unified_calibration_enabled():
            return 0
    except Exception:
        pass

    if not is_trade_data_scheduler_enabled():
        return 0

    created = 0
    now_ms = int(time.time() * 1000)

    fills_cron = os.getenv(TRADE_FILLS_EXPORT_CRON_ENV, DEFAULT_FILLS_EXPORT_CRON).strip()
    validate_schedule(fills_cron)
    fills_job_id = "hub-trade-fills-export"
    if store.get(fills_job_id) is None:
        store.upsert(
            ScheduledResearchJob(
                id=fills_job_id,
                prompt="Export OpenAlgo sandbox fills and sync execution parquet",
                schedule=fills_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_TRADE_FILLS_EXPORT},
            )
        )
        logger.info("registered default trade data job %s (%s)", fills_job_id, fills_cron)
        created += 1

    archive_cron = os.getenv(
        RESEARCH_HISTORY_ARCHIVE_CRON_ENV,
        DEFAULT_RESEARCH_HISTORY_ARCHIVE_CRON,
    ).strip()
    validate_schedule(archive_cron)
    archive_job_id = "hub-research-history-archive"
    if store.get(archive_job_id) is None:
        store.upsert(
            ScheduledResearchJob(
                id=archive_job_id,
                prompt="Archive options and stock research snapshots to history/",
                schedule=archive_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_RESEARCH_HISTORY_ARCHIVE},
            )
        )
        logger.info("registered default trade data job %s (%s)", archive_job_id, archive_cron)
        created += 1

    nse_cron = os.getenv(NSE_MACRO_REFRESH_CRON_ENV, DEFAULT_NSE_MACRO_REFRESH_CRON).strip()
    validate_schedule(nse_cron)
    nse_job_id = "nse-macro-refresh"
    if store.get(nse_job_id) is None:
        store.upsert(
            ScheduledResearchJob(
                id=nse_job_id,
                prompt="Refresh NSE browser repo seed layers and hub macro datasets",
                schedule=nse_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_NSE_MACRO_REFRESH},
            )
        )
        logger.info("registered default trade data job %s (%s)", nse_job_id, nse_cron)
        created += 1

    return created
