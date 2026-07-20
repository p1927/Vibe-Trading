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

JOB_TYPE_INDEX_PREDICTION_POST_CLOSE = "index_prediction_post_close"
JOB_TYPE_HUB_NEWS_ENTITY = "hub_news_entity"
JOB_TYPE_HUB_NEWS_INGEST = "hub_news_ingest"
JOB_TYPE_EXTERNAL_PREDICTIONS_REFRESH = "external_predictions_refresh"

INDEX_JOB_TYPES = frozenset({
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
    JOB_TYPE_INDEX_RESEARCH,
    JOB_TYPE_INDEX_PLAN_REFRESH,
    JOB_TYPE_INDEX_CALIBRATION,
    JOB_TYPE_COMPANY_RESEARCH_ARCHIVE,
    JOB_TYPE_INDEX_PREDICTION_POST_CLOSE,
    JOB_TYPE_HUB_NEWS_ENTITY,
    JOB_TYPE_HUB_NEWS_INGEST,
    JOB_TYPE_EXTERNAL_PREDICTIONS_REFRESH,
})

LAST_RESULT_CONFIG_KEY = "_last_result_summary"

_TRUE_VALUES = {"1", "true", "yes", "on"}

_POST_CLOSE_LIGHT_ENRICH_DAYS = 30


def _reraise_pipeline_cancel(exc: BaseException) -> None:
    """Re-raise cooperative pipeline cancellation so the executor can record it."""
    try:
        from trade_integrations.dataflows.index_research.pipeline_cancel import PipelineCancelledError
    except ImportError:
        return
    if isinstance(exc, PipelineCancelledError):
        raise exc


def is_index_scheduler_enabled(value: str | None = None) -> bool:
    """Return whether default index research jobs should register on startup."""
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    from src.config.accessor import get_env_config

    return bool(get_env_config().agent_tuning.index_research_enable_scheduler)


def is_index_monitor_scheduler_enabled(value: str | None = None) -> bool:
    """Return whether live index plan refresh jobs should register on startup."""
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    from src.config.accessor import get_env_config

    return bool(get_env_config().agent_tuning.index_monitor_enable_scheduler)


def _ensure_trade_integrations_on_path() -> None:
    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def _compact_result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    """Shrink a job result dict for persistence on ScheduledResearchJob."""
    if not isinstance(result, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("mode", "skipped", "pipeline_paused", "pause_reason", "had_errors", "status", "error"):
        if key in result:
            summary[key] = result[key]
    staging = result.get("staging")
    if isinstance(staging, dict):
        summary["staging"] = {
            k: staging.get(k)
            for k in ("processed", "created", "updated", "skipped", "errors", "paused")
            if k in staging
        }
    for stage in ("repair", "backfill", "compact_events", "cleanup", "rollup"):
        part = result.get(stage)
        if isinstance(part, dict):
            summary[stage] = {
                k: part.get(k)
                for k in ("status", "error", "skipped", "repaired", "groups_merged", "rows_removed")
                if k in part
            }
    totals = result.get("totals")
    if isinstance(totals, dict):
        summary["totals"] = dict(totals)
    return summary


def _attach_job_result_summary(job: ScheduledResearchJob, result: dict[str, Any] | None) -> None:
    summary = _compact_result_summary(result)
    if summary:
        job.config[LAST_RESULT_CONFIG_KEY] = summary
    if isinstance(result, dict) and result.get("had_errors"):
        job.config[LAST_RESULT_CONFIG_KEY] = {
            **summary,
            "warning": "one or more pipeline stages reported errors",
        }


def run_index_factor_snapshot_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect daily macro + constituent aggregate factors."""
    _ensure_trade_integrations_on_path()
    from datetime import datetime, timezone

    from trade_integrations.dataflows.index_research.pipeline_cancel import check_pipeline_cancel
    from trade_integrations.dataflows.index_research.snapshot import run_snapshot

    check_pipeline_cancel()
    cfg = config or {}
    snapshot_date = cfg.get("snapshot_date")
    if not snapshot_date:
        snapshot_date = datetime.now(timezone.utc).date().isoformat()
    summary = run_snapshot(
        snapshot_date=snapshot_date,
        skip_constituents=bool(cfg.get("skip_constituents")),
    )

    enrich_days = int(cfg.get("enrich_days") or 7)
    participant_oi_days = int(cfg.get("participant_oi_days") or min(7, enrich_days))
    live_fetch_days = int(cfg.get("live_fetch_days") or 1)
    enrich_rolling_only = bool(cfg.get("enrich_rolling_only", False))
    try:
        from trade_integrations.dataflows.index_research.participant_oi_backfill import (
            backfill_participant_oi,
        )

        oi_summary = backfill_participant_oi(
            days=enrich_days,
            max_days=participant_oi_days,
            sleep_seconds=0.25,
            skip_if_complete=True,
        )
        summary["participant_oi"] = oi_summary
    except Exception as exc:
        _reraise_pipeline_cancel(exc)
        logger.warning("participant OI refresh in factor snapshot failed: %s", exc)
        summary["participant_oi"] = {"status": "error", "reason": str(exc)}

    check_pipeline_cancel()
    try:
        from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
            enrich_factor_history,
        )

        enrich_summary = enrich_factor_history(
            days=enrich_days,
            batch_historic=False,
            enrichment_mode="light",
            enrich_rolling_only=enrich_rolling_only,
            live_fetch_days=live_fetch_days,
        )
        summary["factor_enrichment"] = enrich_summary
    except Exception as exc:
        _reraise_pipeline_cancel(exc)
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
    try:
        from src.trade.index_prediction_run_jobs import get_active_job

        active = get_active_job(ticker)
        if active and str(active.get("status") or "") in {"queued", "running"}:
            logger.info("index plan refresh skipped: manual run active for %s", ticker)
            return {"skipped": True, "reason": "manual_run_active", "ticker": ticker}
    except Exception as exc:
        logger.debug("manual run active check skipped: %s", exc)
    try:
        doc, reason = run_index_light_refresh(
            ticker,
            horizon_days=cfg.get("horizon_days"),
            force=bool(cfg.get("force")),
            poll_mode=True,
        )
    except Exception as exc:
        _reraise_pipeline_cancel(exc)
        # Poll jobs must not enter terminal FAILED on transient pipeline errors.
        logger.exception("index plan refresh failed for %s", ticker)
        return {
            "skipped": False,
            "ticker": ticker,
            "reason": "error",
            "refreshed": False,
            "error": str(exc),
        }
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


def run_index_prediction_post_close_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Post-close: enrich flows, backtest, counterfactual, data audit."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.factor_backfill_enrichment import enrich_factor_history
    from trade_integrations.dataflows.index_research.hub_data_audit import run_and_save_data_audit
    from trade_integrations.dataflows.index_research.backtest_runner import run_and_save_backtest
    from trade_integrations.dataflows.index_research.nse_browser_refresh import refresh_nse_browser_for_prediction
    from trade_integrations.dataflows.index_research.prediction_counterfactual import run_and_save_counterfactual

    cfg = config or {}
    enrich_days = int(cfg.get("enrich_days") or min(int(cfg.get("days") or 365), _POST_CLOSE_LIGHT_ENRICH_DAYS))
    backtest_days = int(cfg.get("days") or 365)
    horizon_days = int(cfg.get("horizon_days") or 14)
    nse_browser = refresh_nse_browser_for_prediction(
        days=enrich_days,
        refresh=bool(cfg.get("refresh_nse_browser", True)),
        refresh_cookies=bool(cfg.get("refresh_cookies", False)),
    )
    return {
        "nse_browser": nse_browser,
        "factor_enrichment": enrich_factor_history(
            days=enrich_days,
            batch_historic=False,
            enrichment_mode="light",
            skip_niftyinvest_fetch=True,
        ),
        "backtest": run_and_save_backtest(
            days=backtest_days,
            horizon_days=horizon_days,
            include_bottom_up=bool(cfg.get("include_bottom_up")),
        ),
        "counterfactual": run_and_save_counterfactual(days=backtest_days, horizon_days=horizon_days),
        "data_audit": run_and_save_data_audit(days=backtest_days, horizon_days=horizon_days),
    }


def run_index_calibration_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reconcile ledger, update accuracy, retrain macro model on drift."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.calibration_runner import run_calibration

    cfg = config or {}
    return run_calibration(
        horizon_days=cfg.get("horizon_days"),
        force_retrain=bool(cfg.get("force_retrain")),
    )


def run_hub_news_entity_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Drain staging queue and optionally run heavy entity maintenance."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.news_entity_worker import run_hub_news_entity_job as _fn

    try:
        return _fn(config)
    except Exception as exc:
        logger.exception("hub news entity job failed")
        return {"status": "error", "error": str(exc), "had_errors": True}


def run_hub_news_ingest_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch live news from configured sources into hub staging."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.hub_news_ingest import run_hub_news_ingest

    cfg = config or {}
    mode = str(cfg.get("mode") or "full").strip().lower()
    sources = cfg.get("sources")
    if sources is None:
        sources = "default"
    try:
        return run_hub_news_ingest(
            ticker=str(cfg.get("ticker") or "NIFTY"),
            sources=sources,
            mode=mode,
            lookback_days=cfg.get("lookback_days"),
            rss_limit_per_feed=int(cfg.get("rss_limit_per_feed") or 10),
            watcher_since_hours=int(cfg.get("watcher_since_hours") or 6),
            watcher_tickers=cfg.get("watcher_tickers"),
        )
    except Exception as exc:
        logger.exception("hub news ingest job failed (mode=%s)", mode)
        return {"status": "error", "error": str(exc), "mode": mode, "had_errors": True}


def run_external_predictions_refresh_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh third-party NIFTY forecasts for default horizons."""
    _ensure_trade_integrations_on_path()
    cfg = dict(config or {})
    ticker = str(cfg.get("ticker") or "NIFTY").upper()
    horizons = cfg.get("horizon_days")
    if isinstance(horizons, int):
        horizon_list = [horizons]
    elif isinstance(horizons, list):
        horizon_list = [int(h) for h in horizons if int(h) > 0]
    else:
        horizon_list = [14, 30]
    try:
        from trade_integrations.dataflows.index_research.external_predictions.refresh import (
            refresh_all_external_predictions,
        )

        summaries = []
        for horizon in horizon_list:
            snap = refresh_all_external_predictions(symbol=ticker, horizon_days=horizon)
            ok = sum(1 for p in snap.predictions if p.fetch_status == "ok")
            summaries.append({"horizon_days": horizon, "ok_predictions": ok, "fetched_at": snap.fetched_at})
        return {"status": "ok", "ticker": ticker, "horizons": summaries}
    except Exception as exc:
        logger.exception("external predictions refresh job failed")
        return {"status": "error", "error": str(exc), "ticker": ticker}


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
    if job_type == JOB_TYPE_INDEX_PREDICTION_POST_CLOSE:
        summary = run_index_prediction_post_close_job(job.config)
        logger.info("index prediction post-close completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_HUB_NEWS_ENTITY:
        summary = run_hub_news_entity_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("hub news entity pipeline completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_HUB_NEWS_INGEST:
        summary = run_hub_news_ingest_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("hub news ingest completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_EXTERNAL_PREDICTIONS_REFRESH:
        summary = run_external_predictions_refresh_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("external predictions refresh completed for job %s: %s", job.id, summary)
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

    skip_unified_duplicates = False
    try:
        from src.scheduled_research.hub_calibration_jobs import (
            is_hub_calibration_scheduler_enabled,
            is_hub_unified_calibration_enabled,
        )

        skip_unified_duplicates = (
            is_hub_calibration_scheduler_enabled() and is_hub_unified_calibration_enabled()
        )
    except Exception:
        pass

    now_ms = int(time.time() * 1000)
    defaults = [
        ScheduledResearchJob(
            id="nifty-index-factor-snapshot",
            prompt="Collect daily Nifty index factor snapshot",
            schedule=snapshot_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
                "ticker": "NIFTY",
                "enrich_days": 7,
                "enrich_rolling_only": True,
                "skip_constituents": True,
                "participant_oi_days": 1,
                "live_fetch_days": 1,
            },
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
        ScheduledResearchJob(
            id="nifty-hub-news-ingest-full",
            prompt="Full hub news ingest (all sources, daily)",
            schedule=os.getenv("HUB_NEWS_FULL_INGEST_CRON", "0 7 * * *").strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "NIFTY",
                "sources": os.getenv("HUB_NEWS_FULL_SOURCES", "all"),
                "lookback_days": 3,
            },
        ),
        ScheduledResearchJob(
            id="nifty-external-predictions-refresh",
            prompt="Refresh third-party NIFTY street forecasts (SearXNG + LLM)",
            schedule=os.getenv("EXTERNAL_PREDICTIONS_REFRESH_CRON", "0 8 * * *").strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_EXTERNAL_PREDICTIONS_REFRESH,
                "ticker": "NIFTY",
                "horizon_days": [14, 30],
            },
        ),
        ScheduledResearchJob(
            id="nifty-hub-news-ingest-light",
            prompt="Light hub news ingest (all env RSS feeds)",
            schedule=os.getenv(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                os.getenv("HUB_NEWS_INGEST_CRON", "0 */4 * * *"),
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "NIFTY",
                "sources": os.getenv("HUB_NEWS_LIGHT_SOURCES", "rss"),
                "lookback_days": 1,
            },
        ),
        ScheduledResearchJob(
            id="nifty-hub-news-entity",
            prompt="Drain staging news refs into distilled hub events",
            schedule=os.getenv("HUB_NEWS_ENTITY_CRON", "35 18 * * *").strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_ENTITY,
                "mode": "drain",
                "ticker": "NIFTY",
                "batch_size": 200,
            },
        ),
        ScheduledResearchJob(
            id="nifty-hub-news-entity-maintenance",
            prompt="Heavy hub news maintenance (repair, backfill, compact)",
            schedule=os.getenv("HUB_NEWS_ENTITY_MAINTENANCE_CRON", "0 3 * * 0").strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_ENTITY,
                "mode": "maintenance",
                "ticker": "NIFTY",
                "batch_size": 200,
                "lookback_days": 365,
            },
        ),
        ScheduledResearchJob(
            id="nifty-index-prediction-post-close",
            prompt="Weekly post-close prediction pipeline refresh (flows, backtest, counterfactual)",
            schedule="0 4 * * 6",
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_INDEX_PREDICTION_POST_CLOSE,
                "ticker": "NIFTY",
                "days": 365,
                "enrich_days": _POST_CLOSE_LIGHT_ENRICH_DAYS,
                "horizon_days": 14,
                "include_bottom_up": True,
            },
        ),
    ]

    if skip_unified_duplicates:
        defaults = [
            job
            for job in defaults
            if job.id not in {"nifty-index-calibration", "nifty-company-research-archive"}
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
    try:
        _ensure_trade_integrations_on_path()
        from trade_integrations.hub_storage.news_pipeline_config import sync_scheduled_jobs_from_config

        sync_scheduled_jobs_from_config()
    except Exception as exc:
        logger.warning("hub news pipeline job sync failed: %s", exc)

    for job in defaults:
        if store.get(job.id) is not None:
            continue
        store.upsert(job)
        created += 1
        logger.info("registered default index research job %s (%s)", job.id, job.schedule)
    return created
