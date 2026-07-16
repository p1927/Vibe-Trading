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
INDEX_MONITOR_ENABLE_SCHEDULER_ENV = "INDEX_MONITOR_ENABLE_SCHEDULER"
INDEX_MONITOR_POLL_CRON_ENV = "INDEX_MONITOR_POLL_CRON"
DEFAULT_SNAPSHOT_CRON = "0 18 * * *"
DEFAULT_FULL_CRON = "0 8 * * 1"
DEFAULT_INDEX_POLL_CRON = "*/5 * * * *"

JOB_TYPE_INDEX_FACTOR_SNAPSHOT = "index_factor_snapshot"
JOB_TYPE_INDEX_RESEARCH = "index_research"
JOB_TYPE_INDEX_PLAN_REFRESH = "index_plan_refresh"
JOB_TYPE_INDEX_CALIBRATION = "index_calibration"
JOB_TYPE_COMPANY_RESEARCH_ARCHIVE = "company_research_archive"

INDEX_JOB_TYPES = frozenset({
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
    JOB_TYPE_INDEX_RESEARCH,
    JOB_TYPE_INDEX_PLAN_REFRESH,
    JOB_TYPE_INDEX_CALIBRATION,
    JOB_TYPE_COMPANY_RESEARCH_ARCHIVE,
})

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_index_scheduler_enabled(value: str | None = None) -> bool:
    """Return whether default index research jobs should register on startup."""
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    return os.getenv(INDEX_RESEARCH_ENABLE_SCHEDULER_ENV, "").strip().lower() in _TRUE_VALUES


def is_index_monitor_scheduler_enabled(value: str | None = None) -> bool:
    """Return whether live index plan refresh jobs should register on startup."""
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    return os.getenv(INDEX_MONITOR_ENABLE_SCHEDULER_ENV, "").strip().lower() in _TRUE_VALUES


def _ensure_trade_integrations_on_path() -> None:
    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def run_index_factor_snapshot_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect daily macro + constituent aggregate factors."""
    _ensure_trade_integrations_on_path()
    from datetime import datetime, timezone

    from trade_integrations.dataflows.index_research.snapshot import run_snapshot

    cfg = config or {}
    snapshot_date = cfg.get("snapshot_date")
    if not snapshot_date:
        snapshot_date = datetime.now(timezone.utc).date().isoformat()
    summary = run_snapshot(
        snapshot_date=snapshot_date,
        skip_constituents=bool(cfg.get("skip_constituents")),
    )

    enrich_days = int(cfg.get("enrich_days") or 30)
    try:
        from trade_integrations.dataflows.index_research.participant_oi_backfill import (
            backfill_participant_oi,
        )

        oi_summary = backfill_participant_oi(
            days=enrich_days,
            max_days=min(7, enrich_days),
            sleep_seconds=0.25,
        )
        summary["participant_oi"] = oi_summary
    except Exception as exc:
        logger.warning("participant OI refresh in factor snapshot failed: %s", exc)
        summary["participant_oi"] = {"status": "error", "reason": str(exc)}

    try:
        from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
            enrich_factor_history,
        )

        enrich_summary = enrich_factor_history(days=enrich_days)
        summary["factor_enrichment"] = enrich_summary
    except Exception as exc:
        logger.warning("factor enrichment in factor snapshot failed: %s", exc)
        summary["factor_enrichment"] = {"status": "error", "reason": str(exc)}

    return summary


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


def run_index_plan_refresh_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Light refresh for NIFTY when macro drifts or material news appears."""
    if not is_index_monitor_scheduler_enabled():
        logger.info("index plan refresh skipped: INDEX_MONITOR_ENABLE_SCHEDULER disabled")
        return {"skipped": True, "reason": "monitor_disabled"}

    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.light_refresh import run_index_light_refresh

    cfg = config or {}
    ticker = str(cfg.get("ticker") or "NIFTY").strip().upper()
    doc, reason = run_index_light_refresh(
        ticker,
        horizon_days=cfg.get("horizon_days"),
        force=bool(cfg.get("force")),
    )
    if reason == "unchanged":
        return {"skipped": False, "ticker": ticker, "reason": reason, "refreshed": False}
    return {
        "skipped": False,
        "ticker": ticker,
        "reason": reason,
        "refreshed": True,
        "as_of": doc.as_of.isoformat() if hasattr(doc.as_of, "isoformat") else str(doc.as_of),
    }


def run_company_research_archive_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Archive latest company research JSON snapshots for prediction history."""
    _ensure_trade_integrations_on_path()
    from datetime import datetime, timezone

    from trade_integrations.context.hub import archive_company_research_snapshots

    cfg = config or {}
    as_of_date = cfg.get("as_of_date")
    if not as_of_date:
        as_of_date = datetime.now(timezone.utc).date().isoformat()
    return archive_company_research_snapshots(as_of_date=as_of_date)


def run_index_calibration_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reconcile ledger, update accuracy, retrain macro model on drift."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.calibration_runner import run_calibration

    cfg = config or {}
    return run_calibration(
        horizon_days=cfg.get("horizon_days"),
        force_retrain=bool(cfg.get("force_retrain")),
    )


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
    if job_type == JOB_TYPE_INDEX_PLAN_REFRESH:
        summary = run_index_plan_refresh_job(job.config)
        logger.info("index plan refresh completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_INDEX_CALIBRATION:
        summary = run_index_calibration_job(job.config)
        logger.info("index calibration completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_COMPANY_RESEARCH_ARCHIVE:
        summary = run_company_research_archive_job(job.config)
        logger.info("company research archive completed for job %s: %s", job.id, summary)
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
        ScheduledResearchJob(
            id="nifty-index-calibration",
            prompt="Reconcile index prediction ledger and retrain macro model",
            schedule="0 6 * * *",
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_INDEX_CALIBRATION, "ticker": "NIFTY"},
        ),
        ScheduledResearchJob(
            id="nifty-company-research-archive",
            prompt="Archive company research snapshots for prediction history",
            schedule="30 18 * * *",
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_COMPANY_RESEARCH_ARCHIVE, "ticker": "NIFTY"},
        ),
    ]

    if is_index_monitor_scheduler_enabled():
        poll_cron = os.getenv(INDEX_MONITOR_POLL_CRON_ENV, DEFAULT_INDEX_POLL_CRON).strip()
        validate_schedule(poll_cron)
        defaults.append(
            ScheduledResearchJob(
                id="nifty-index-plan-refresh",
                prompt="Light refresh Nifty index prediction on news/macro drift",
                schedule=poll_cron,
                next_run_at=now_ms,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_INDEX_PLAN_REFRESH, "ticker": "NIFTY"},
            ),
        )

    created = 0
    for job in defaults:
        if store.get(job.id) is not None:
            continue
        store.upsert(job)
        created += 1
        logger.info("registered default index research job %s (%s)", job.id, job.schedule)
    return created
