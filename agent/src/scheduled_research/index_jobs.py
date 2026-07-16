"""Scheduled index research jobs (factor snapshot + full research)."""

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

INDEX_RESEARCH_ENABLE_SCHEDULER_ENV = "INDEX_RESEARCH_ENABLE_SCHEDULER"
INDEX_RESEARCH_SNAPSHOT_CRON_ENV = "INDEX_RESEARCH_SNAPSHOT_CRON"
INDEX_RESEARCH_FULL_CRON_ENV = "INDEX_RESEARCH_FULL_CRON"
DEFAULT_SNAPSHOT_CRON = "0 18 * * *"
DEFAULT_FULL_CRON = "0 8 * * 1"

JOB_TYPE_INDEX_FACTOR_SNAPSHOT = "index_factor_snapshot"
JOB_TYPE_INDEX_RESEARCH = "index_research"

INDEX_JOB_TYPES = frozenset({JOB_TYPE_INDEX_FACTOR_SNAPSHOT, JOB_TYPE_INDEX_RESEARCH})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_index_scheduler_enabled(value: str | None = None) -> bool:
    """Return whether default index research jobs should register on startup."""
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    return os.getenv(INDEX_RESEARCH_ENABLE_SCHEDULER_ENV, "").strip().lower() in _TRUE_VALUES


def _ensure_trade_integrations_on_path() -> None:
    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def run_index_factor_snapshot_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect daily macro + constituent aggregate factors."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.snapshot import run_snapshot

    cfg = config or {}
    return run_snapshot(
        snapshot_date=cfg.get("snapshot_date"),
        skip_constituents=bool(cfg.get("skip_constituents")),
    )


def run_index_research_job(config: dict[str, Any] | None = None) -> None:
    """Run full index research pipeline and persist to hub."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.context.hub import save_index_research
    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    cfg = config or {}
    ticker = str(cfg.get("ticker") or "NIFTY").strip().upper()
    if cfg.get("run_snapshot_first"):
        run_index_factor_snapshot_job(cfg)
    doc = run_index_research(
        ticker,
        horizon_days=cfg.get("horizon_days"),
        refresh_constituents=bool(cfg.get("refresh_constituents")),
    )
    save_index_research(doc)


def dispatch_index_job_sync(job: ScheduledResearchJob) -> None:
    """Execute one index scheduled job synchronously."""
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_INDEX_FACTOR_SNAPSHOT:
        summary = run_index_factor_snapshot_job(job.config)
        logger.info("index factor snapshot completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_INDEX_RESEARCH:
        run_index_research_job(job.config)
        logger.info("index research completed for job %s", job.id)
        return
    raise ValueError(f"unsupported index job_type: {job_type!r}")


async def dispatch_index_job(job: ScheduledResearchJob) -> None:
    """Run an index job without blocking the asyncio event loop."""
    await asyncio.to_thread(dispatch_index_job_sync, job)


def register_default_index_jobs(store: ScheduledResearchJobStore) -> int:
    """Register default NIFTY index jobs when missing. Returns count created."""
    snapshot_cron = os.getenv(INDEX_RESEARCH_SNAPSHOT_CRON_ENV, DEFAULT_SNAPSHOT_CRON).strip()
    full_cron = os.getenv(INDEX_RESEARCH_FULL_CRON_ENV, DEFAULT_FULL_CRON).strip()
    validate_schedule(snapshot_cron)
    validate_schedule(full_cron)

    now_ms = int(time.time() * 1000)
    defaults = [
        ScheduledResearchJob(
            id="nifty-index-factor-snapshot",
            prompt="Collect daily Nifty index factor snapshot",
            schedule=snapshot_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_INDEX_FACTOR_SNAPSHOT, "ticker": "NIFTY"},
        ),
        ScheduledResearchJob(
            id="nifty-index-research",
            prompt="Run full Nifty index research pipeline",
            schedule=full_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_INDEX_RESEARCH,
                "ticker": "NIFTY",
                "run_snapshot_first": True,
                "refresh_constituents": True,
            },
        ),
    ]

    created = 0
    for job in defaults:
        if store.get(job.id) is not None:
            continue
        store.upsert(job)
        created += 1
        logger.info("registered default index research job %s (%s)", job.id, job.schedule)
    return created
