"""Scheduled index research jobs (factor snapshot + full research)."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config.accessor import get_env_config, get_env_or
from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.hub_bridge import ensure_trade_stack_path

logger = logging.getLogger(__name__)

INDEX_RESEARCH_ENABLE_SCHEDULER_ENV = "INDEX_RESEARCH_ENABLE_SCHEDULER"
INDEX_RESEARCH_SNAPSHOT_CRON_ENV = "INDEX_RESEARCH_SNAPSHOT_CRON"
INDEX_RESEARCH_FULL_CRON_ENV = "INDEX_RESEARCH_FULL_CRON"
INDEX_MONITOR_ENABLE_SCHEDULER_ENV = "INDEX_MONITOR_ENABLE_SCHEDULER"
INDEX_MONITOR_POLL_CRON_ENV = "INDEX_MONITOR_POLL_CRON"
DEFAULT_SNAPSHOT_CRON = "0 18 * * *"
DEFAULT_FULL_CRON = "0 8 * * 1"
DEFAULT_INDEX_POLL_CRON = "*/5 * * * *"
STOCK_HISTORY_COVERAGE_SWEEP_CRON_ENV = "STOCK_HISTORY_COVERAGE_SWEEP_CRON"
DEFAULT_STOCK_HISTORY_COVERAGE_SWEEP_CRON = "0 19 * * *"
GLOBAL_MACRO_EOD_REFRESH_CRON_ENV = "GLOBAL_MACRO_EOD_REFRESH_CRON"
DEFAULT_GLOBAL_MACRO_EOD_REFRESH_CRON = "15 19 * * *"

JOB_TYPE_INDEX_FACTOR_SNAPSHOT = "index_factor_snapshot"
JOB_TYPE_INDEX_RESEARCH = "index_research"
JOB_TYPE_INDEX_PLAN_REFRESH = "index_plan_refresh"
JOB_TYPE_INDEX_CALIBRATION = "index_calibration"
JOB_TYPE_COMPANY_RESEARCH_ARCHIVE = "company_research_archive"

JOB_TYPE_INDEX_PREDICTION_POST_CLOSE = "index_prediction_post_close"
JOB_TYPE_HUB_NEWS_ENTITY = "hub_news_entity"
JOB_TYPE_HUB_NEWS_INGEST = "hub_news_ingest"
# Per-job dispatch_timeout_ms overrides for the heavier "full" ingest and
# "drain" entity variants, which share a job_type (and thus a default
# staleness.py timeout bucket) with a much cheaper sibling ("light" ingest,
# "maintenance" entity). Sized with headroom over the known ~52min real
# full-ingest runtime and the observed 20min entity-drain timeout failure.
# See 2026-08-27-scheduler-dispatch-timeouts.
_HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS = 90 * 60 * 1000
_HUB_NEWS_ENTITY_DRAIN_DISPATCH_TIMEOUT_MS = 40 * 60 * 1000
# "light"/"tight" ingest jobs never got their own override and fell back to
# staleness.py's generic 10-min hub_news_ingest default. Live-verified
# 2026-08-30: nifty-hub-news-ingest-tight failed 3 consecutive runs at exactly
# that 10-min cap, and per-market light-mode runs (us/jp/cn/ru) were already
# observed completing anywhere from ~1min to ~9min. "-tight" clones "-light"'s
# config verbatim but fires every 5min instead of light's slower cadence, so
# it has the least margin. Sized with 2x headroom over the observed ~9-10min
# ceiling. See 2026-08-30-hub-news-ingest-tight-light-dispatch-timeout-undersized.
_HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS = 20 * 60 * 1000
JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP = "stock_history_coverage_sweep"
JOB_TYPE_NEWS_QUALITY_EVAL = "news_quality_eval"
JOB_TYPE_NEWS_DEDUP_QUALITY_EVAL = "news_dedup_quality_eval"
JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH = "global_macro_eod_refresh"
JOB_TYPE_OI_SNAPSHOT = "oi_snapshot"
JOB_TYPE_REINFERENCE_TICK = "reinference_tick"
JOB_TYPE_PUMP_DUMP_PROXY = "pump_dump_proxy"
JOB_TYPE_MAX_PAIN_BHAVCOPY = "max_pain_bhavcopy"
JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT = "constituent_volume_snapshot"
JOB_TYPE_FORECAST_PLATFORM_RETRAIN = "forecast_platform_retrain"
JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH = "quantile_forecast_ledger_push"

INDEX_JOB_TYPES = frozenset({
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
    JOB_TYPE_INDEX_RESEARCH,
    JOB_TYPE_INDEX_PLAN_REFRESH,
    JOB_TYPE_INDEX_CALIBRATION,
    JOB_TYPE_COMPANY_RESEARCH_ARCHIVE,
    JOB_TYPE_INDEX_PREDICTION_POST_CLOSE,
    JOB_TYPE_HUB_NEWS_ENTITY,
    JOB_TYPE_HUB_NEWS_INGEST,
    JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP,
    JOB_TYPE_NEWS_QUALITY_EVAL,
    JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH,
    JOB_TYPE_OI_SNAPSHOT,
    JOB_TYPE_REINFERENCE_TICK,
    JOB_TYPE_PUMP_DUMP_PROXY,
    JOB_TYPE_MAX_PAIN_BHAVCOPY,
    JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT,
    JOB_TYPE_FORECAST_PLATFORM_RETRAIN,
    JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH,
})

NEWS_QUALITY_EVAL_CRON_ENV = "NEWS_QUALITY_EVAL_CRON"
DEFAULT_NEWS_QUALITY_EVAL_CRON = "0 2 * * *"

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
    ensure_trade_stack_path()


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
    finalize = result.get("cold_tier_finalize")
    if isinstance(finalize, dict):
        summary["cold_tier_finalize"] = {
            "status": finalize.get("status"),
            "reason": finalize.get("reason"),
            "failed_steps": finalize.get("failed_steps") or [],
            "trading_day": finalize.get("trading_day"),
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


def _index_factor_snapshot_had_errors(summary: dict[str, Any]) -> bool:
    if summary.get("status") == "error":
        return True
    ohlcv = summary.get("ohlcv") or {}
    if isinstance(ohlcv, dict) and ohlcv.get("status") == "error":
        return True
    enrich = summary.get("factor_enrichment") or {}
    if enrich.get("status") == "error" or enrich.get("reason") == "no_nifty_history":
        return True
    for key in ("participant_oi", "macro_daily", "cache_flows", "repo_flows", "panel"):
        step = summary.get(key) or {}
        if isinstance(step, dict) and step.get("status") in {"error", "partial"}:
            return True
    finalize = summary.get("cold_tier_finalize") or {}
    if isinstance(finalize, dict):
        if finalize.get("status") in {"partial", "error", "skipped"}:
            return True
        for step_name, step in finalize.items():
            if step_name in {"status", "failed_steps", "trading_day", "reason"}:
                continue
            if isinstance(step, dict) and step.get("status") in {"error", "partial"}:
                return True
    return False


def _should_skip_cold_tier_finalize(summary: dict[str, Any]) -> tuple[bool, str]:
    if summary.get("status") == "error":
        return True, "persist_failed"
    enrich = summary.get("factor_enrichment") or {}
    if enrich.get("status") == "error":
        return True, "factor_enrichment_failed"
    if enrich.get("reason") == "no_nifty_history":
        return True, "no_nifty_history"
    return False, ""


def run_index_factor_snapshot_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect daily macro + constituent aggregate factors."""
    _ensure_trade_integrations_on_path()

    from trade_integrations.dataflows.index_research.pipeline_cancel import check_pipeline_cancel

    cfg = config or {}
    ticker = str(cfg.get("ticker") or "NIFTY").strip().upper()
    try:
        from src.trade.index_prediction_run_jobs import get_active_job

        active = get_active_job(ticker)
        if active and str(active.get("status") or "") in {"queued", "running"}:
            logger.info("index factor snapshot skipped: manual run active for %s", ticker)
            return {"skipped": True, "reason": "manual_run_active", "ticker": ticker}
    except Exception as exc:
        logger.debug("manual run active check skipped: %s", exc)

    check_pipeline_cancel()

    snapshot_date = cfg.get("snapshot_date")
    if not snapshot_date:
        from trade_integrations.dataflows.company_research.market import india_trading_date_iso

        snapshot_date = india_trading_date_iso()[:10]

    try:
        from trade_integrations.dataflows.index_research.history_ingest import (
            persist_daily_hub_market_data,
        )

        summary = persist_daily_hub_market_data()
    except Exception as exc:
        _reraise_pipeline_cancel(exc)
        logger.warning("daily hub market persist in factor snapshot failed: %s", exc)
        summary = {"status": "error", "reason": str(exc)}

    check_pipeline_cancel()
    from trade_integrations.dataflows.index_research.snapshot import run_snapshot

    snapshot_summary = run_snapshot(
        snapshot_date=snapshot_date,
        skip_constituents=bool(cfg.get("skip_constituents")),
    )
    summary = {**summary, "snapshot": snapshot_summary}

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

    check_pipeline_cancel()
    skip_finalize, skip_reason = _should_skip_cold_tier_finalize(summary)
    if skip_finalize:
        from trade_integrations.dataflows.company_research.market import india_trading_date_iso

        summary["cold_tier_finalize"] = {
            "status": "skipped",
            "reason": skip_reason,
            "trading_day": india_trading_date_iso()[:10],
        }
    else:
        try:
            from trade_integrations.dataflows.index_research.history_ingest import finalize_daily_cold_tier

            flow_lookback = int(cfg.get("flow_lookback_days") or 7)
            finalize_summary = finalize_daily_cold_tier(
                flow_lookback_days=flow_lookback,
                macro_lookback_days=int(cfg.get("macro_lookback_days") or 14),
                panel_tail_days=int(cfg.get("panel_tail_days") or 14),
            )
            summary["cold_tier_finalize"] = finalize_summary
        except Exception as exc:
            _reraise_pipeline_cancel(exc)
            logger.warning("cold tier finalize in factor snapshot failed: %s", exc)
            summary["cold_tier_finalize"] = {"status": "error", "reason": str(exc)}

    if _index_factor_snapshot_had_errors(summary):
        summary["had_errors"] = True
    return summary


def run_index_research_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run full index research pipeline and persist to hub.

    Registers in the same job store as manual "Run analysis" (via
    ``start_job``/``run_worker``) instead of running the pipeline directly, so
    a manual run and this weekly full-refresh cannot race each other's writes
    to the same hub document, and this run is visible (with live logs) in the
    Prediction page like any other job.
    """
    _ensure_trade_integrations_on_path()
    cfg = config or {}
    ticker = str(cfg.get("ticker") or "NIFTY").strip().upper()
    if cfg.get("run_snapshot_first"):
        run_index_factor_snapshot_job(cfg)

    from src.trade.index_prediction_run_jobs import (
        get_active_job,
        get_job,
        mark_worker_pid,
        run_worker,
        start_job,
    )

    try:
        active = get_active_job(ticker)
        if active and str(active.get("status") or "") in {"queued", "running"}:
            logger.info(
                "index research (scheduled) skipped: another run active for %s (job=%s)",
                ticker,
                active.get("job_id"),
            )
            return {
                "skipped": True,
                "reason": "run_active",
                "ticker": ticker,
                "active_job_id": active.get("job_id"),
            }
    except Exception as exc:
        logger.debug("active-run check skipped: %s", exc)

    job_id, reused = start_job(
        ticker=ticker,
        horizon_days=cfg.get("horizon_days"),
        refresh_constituents=bool(cfg.get("refresh_constituents")),
        run_forecast_lab=True,
    )
    if not reused:
        mark_worker_pid(job_id, os.getpid())
        run_worker(job_id)  # blocking; dispatch_index_job already runs off the event loop
    else:
        from src.trade.index_prediction_run_jobs import _get_job_record, worker_alive

        existing = _get_job_record(job_id)
        if existing is not None and not worker_alive(existing):
            mark_worker_pid(job_id, os.getpid())
            run_worker(job_id)
    final = get_job(job_id) or {}
    return {
        "skipped": False,
        "ticker": ticker,
        "job_id": job_id,
        "reused": reused,
        "status": final.get("status"),
    }


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
            skip_niftyinvest_fetch=False,
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


def run_forecast_platform_retrain_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Weekly retrain for the multi-factor causal forecast platform
    (.claude/backlog/items/2026-08-25-multi-factor-causal-forecast-platform.md): causal
    graph discovery, regime-model fitting, and quantile-forecast training, each saved as
    a new candidate version. Never auto-promotes anything — see
    `trade_integrations.forecast_platform_retrain`'s own module docstring for why that's
    a deliberate governance decision, not a gap. This job existing and being scheduled
    (see `default_index_jobs()` below) is itself the fix for a real, confirmed gap: as of
    2026-08-26 the three retrain entrypoints this calls had zero call sites anywhere in
    the repo, not even in tests — exactly the "built but never scheduled" pattern
    `.claude/backlog/items/2026-08-25-investigation-built-but-never-scheduled-pattern.md`
    already documented 4 prior instances of.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.forecast_platform_retrain import retrain_forecast_platform

    cfg = config or {}
    return retrain_forecast_platform(end=cfg.get("end"), factor_start=cfg.get("factor_start", "2020-01-01"))


def run_quantile_forecast_ledger_push_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily live push of `quantile_forecast.forecast_nifty_range()` into the index
    `prediction_ledger` (.claude/backlog/items/2026-08-25-multi-factor-causal-forecast-
    platform.md, Phase 7). Trains fresh quantile models off whatever causal graph/regime
    summary is currently *promoted* — never a saved-but-unpromoted candidate — same
    governance boundary `forecast_platform_retrain`'s job respects for its own legs.

    Deliberately held back until a real, validated walk-forward coverage number existed
    to justify pushing this signal somewhere live-facing readers see — see
    `.claude/backlog/items/2026-08-26-quantile-forecast-live-wiring-pending-real-coverage.md`
    for that number and the reasoning for finally wiring this in.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.quantile_forecast.forecast import forecast_nifty_range
    from trade_integrations.quantile_forecast.ledger_bridge import push_forecast_to_ledger

    cfg = config or {}
    try:
        result = forecast_nifty_range(as_of=cfg.get("as_of"), factor_start=cfg.get("factor_start", "2020-01-01"))
    except Exception as exc:
        logger.exception("quantile forecast ledger push: forecast_nifty_range failed")
        return {"status": "error", "error": str(exc)}

    rows_appended = push_forecast_to_ledger(result)
    return {"status": "ok", "rows_appended": rows_appended, "as_of_date": result.get("as_of_date")}


def run_stock_history_coverage_sweep_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily full-coverage backfill sweep.

    Before this job existed, the only automatic trigger for the
    stock_history coverage buckets was `StockHistory.supplement_today()`
    (called once per recording session for 3 macro/flow buckets only —
    and until recently that call was itself a no-op due to a bucket-
    name mismatch, see `stock_history/coverage.py`). Every other bucket
    (constituents, constituent_ohlcv, sector_index_daily, equity_ohlcv,
    index_tape_*, ...) had a working backfill handler but nothing ever
    called it, so several went stale for weeks to over a year with no
    error surfaced anywhere. This job runs `backfill_into_week` for the
    current ISO week across every registered bucket (`include_optional`
    covers the optional/soft buckets too), so gaps get caught the same
    week they appear instead of accumulating silently.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.stock_history.api import StockHistory
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso

    cfg = config or {}
    try:
        sh = StockHistory()
        summary = sh.backfill_into_week(
            week_start=india_trading_date_iso()[:10],
            include_optional=bool(cfg.get("include_optional", True)),
            verify_after=True,
        )
        return {
            "status": "error" if summary.had_errors else "ok",
            "ok_count": summary.ok_count,
            "failed_count": summary.failed_count,
            "skipped_count": summary.skipped_count,
            "had_errors": summary.had_errors,
        }
    except Exception as exc:
        logger.exception("stock_history coverage sweep failed")
        return {"status": "error", "error": str(exc), "had_errors": True}


def run_global_macro_eod_refresh_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily refresh of the stale `github_datasets`-sourced global-macro EOD
    CSVs (oil_brent_daily, oil_wti_daily, gold, us_10y, sp500) from yfinance.

    Calls `StockHistory.refresh_global_macro_eod()` once per series in
    `global_macro_store.EOD_REFRESH_SYMBOLS` — the first scheduled caller of
    that entry point, added because nothing else in the repo refreshes
    these CSVs (see the 2026-08-22 prediction-tab-data-source-consolidation
    backlog item's "Remaining follow-ups"). Each series is fetched inside
    its own try/except so one bad yfinance call (e.g. a `sp500` rate limit)
    can't block the others from refreshing.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.stock_history.api import StockHistory

    sh = StockHistory()
    cfg = config or {}
    lookback_days = int(cfg.get("lookback_days") or 90)
    series_list = cfg.get("series") or sh.list_eod_refreshable_series()
    results: dict[str, Any] = {}
    had_errors = False
    for series in series_list:
        try:
            result = sh.refresh_global_macro_eod(series=series, lookback_days=lookback_days)
        except Exception as exc:
            logger.warning("global macro EOD refresh failed for series %s: %s", series, exc)
            result = {"status": "error", "series": series, "reason": str(exc)}
        results[series] = result
        if isinstance(result, dict) and result.get("status") == "error":
            had_errors = True
    return {"status": "error" if had_errors else "ok", "had_errors": had_errors, "series": results}


def run_oi_snapshot_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily forward-only OI/max-pain snapshot capture (module 2 step 4 of the
    options-profitability-prediction-platform backlog item — see
    .claude/backlog/items/2026-08-22-nifty-market-reversal-signal.md).

    `openalgo/services/oi_tracker_service.py::calculate_max_pain` is
    live-only and cannot be backfilled — this job's only purpose is to run
    on a daily cadence so history accumulates for later backtesting; a
    missed day is lost, not a job failure worth alerting loudly on (the
    underlying capture function already degrades to a status dict rather
    than raising for exactly this reason).
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.oi_snapshot_store import (
        capture_and_append_oi_snapshot,
    )

    cfg = config or {}
    return capture_and_append_oi_snapshot(
        underlying=str(cfg.get("underlying") or "NIFTY"),
        exchange=str(cfg.get("exchange") or "NSE_INDEX"),
        expiry_date=cfg.get("expiry_date"),
    )


def run_pump_dump_proxy_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily post-close pump-and-dump proxy capture (module 2 step 3 of the
    options-profitability-prediction-platform backlog item — see
    .claude/backlog/items/2026-08-22-nifty-market-reversal-signal.md).

    Cadence decided 2026-08-24: run once daily after close, same shape as
    ``run_oi_snapshot_job`` — a missed/no-data day is expected (only India's
    tick recorder is always-on) and written as-is rather than skipped, so
    this accumulator's day-coverage stays honestly visible for whenever
    there's enough history to join into `reversal_hazard`'s training panel.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.volume_concentration import (
        capture_and_append_pump_dump_snapshot,
    )

    cfg = config or {}
    return capture_and_append_pump_dump_snapshot(
        symbol=str(cfg.get("symbol") or "NIFTY"),
        exchange=str(cfg.get("exchange") or "NSE_INDEX"),
    )


def run_max_pain_bhavcopy_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Daily post-close max-pain reconstruction from NSE's F&O bhavcopy
    (module 2 step 4's retroactive complement — see
    .claude/backlog/archive/items/2026-08-22-nifty-market-reversal-signal.md's
    2026-08-24 entries).

    Made the primary forward-going max-pain source over the live
    ``run_oi_snapshot_job`` (2026-08-24 decision): bhavcopy needs no broker
    session (the live path's own broker session was confirmed broken —
    `active_sessions` empty — during this module's own E2E verification)
    and is published well within this job's schedule margin — NSE's F&O
    bhavcopy is publicly reported to land ~30-60 min after the 15:30 IST
    close (~16:00-16:30 IST); this job runs at 17:30 IST
    (``MAX_PAIN_BHAVCOPY_CRON``, explicit ``timezone="Asia/Kolkata"`` on the
    registered job — unlike the *other* index jobs in this file, whose
    cron strings are evaluated in UTC by default since none of them set
    `timezone` explicitly, a real inaccuracy in this file's own prior
    "just after close" comments worth fixing wherever those jobs' actual
    intended wall-clock time matters). `run_oi_snapshot_job` is kept
    running alongside, not removed, as a same-day-freshness path for
    whenever a live broker session exists again.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.oi_bhavcopy_history import (
        backfill_max_pain_history,
    )

    cfg = config or {}
    symbol = str(cfg.get("symbol") or "NIFTY")
    trading_day = cfg.get("trading_day") or (
        datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
    )
    return backfill_max_pain_history(trading_day, trading_day, symbol=symbol)


def run_reinference_tick_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Frequent poll for module 3's event/heartbeat-triggered re-inference (step 5
    of the options-profitability-prediction-platform backlog item — see
    .claude/backlog/items/2026-08-22-nifty-probabilistic-forecast-engine.md).

    Cheap on a no-trigger tick (no forecast tracks run) — safe to schedule at a
    much tighter cadence than the other index jobs; `run_reinference_tick` itself
    decides per-tick whether a price move, fresh material news, or the heartbeat
    fallback actually warrants recomputing the fusion forecast.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.prediction_algorithms.reinference_trigger import (
        run_reinference_tick,
    )

    cfg = config or {}
    return run_reinference_tick(
        ticker=str(cfg.get("ticker") or "NIFTY"),
        price_materiality_pct=float(cfg.get("price_materiality_pct") or 0.5),
        news_materiality_threshold=float(cfg.get("news_materiality_threshold") or 3.0),
        heartbeat_minutes=float(cfg.get("heartbeat_minutes") or 60.0),
    )


def run_constituent_volume_snapshot_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Frequent intraday poll capturing per-constituent volume/interest snapshots (part 2
    of .claude/backlog/items/2026-08-25-options-microstructure-signal-gap.md).

    Same market-hours cadence shape as ``run_reinference_tick_job`` — this is a batch
    live-quote call (one HTTP round-trip for all NIFTY50 constituents), not a per-symbol
    fetch, so it's cheap enough to run every ~15 minutes without needing a per-tick
    materiality gate the way reinference does.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.index_research.constituent_volume_snapshot_store import (
        capture_and_append_constituent_volume_snapshot,
    )

    return capture_and_append_constituent_volume_snapshot()


def run_hub_news_entity_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Drain staging queue and optionally run heavy entity maintenance."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.news_hub_bridge import run_entity_worker_job as _fn

    try:
        summary = _fn(config)
        if summary.get("pipeline_paused") and str(summary.get("pause_reason") or "") == "llm_wiki_unavailable":
            logger.warning(
                "hub news entity job blocked: %s",
                summary.get("pause_reason"),
            )
        return summary
    except Exception as exc:
        logger.exception("hub news entity job failed")
        return {"status": "error", "error": str(exc), "had_errors": True}


def run_hub_news_ingest_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fetch live news from configured sources into hub staging."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.news_hub_bridge import run_hub_news_ingest

    cfg = config or {}
    mode = str(cfg.get("mode") or "full").strip().lower()
    sources = cfg.get("sources")
    if sources is None:
        sources = "default"
    try:
        summary = run_hub_news_ingest(
            ticker=str(cfg.get("ticker") or "NIFTY"),
            market=str(cfg.get("market") or "IN"),
            sources=sources,
            mode=mode,
            lookback_days=cfg.get("lookback_days"),
            rss_limit_per_feed=int(cfg.get("rss_limit_per_feed") or 10),
            watcher_since_hours=int(cfg.get("watcher_since_hours") or 6),
            watcher_tickers=cfg.get("watcher_tickers"),
            currents_keywords=cfg.get("currents_keywords"),
        )
        if summary.get("blocked") or (
            summary.get("pipeline_paused")
            and str(summary.get("pause_reason") or "") == "llm_wiki_unavailable"
        ):
            logger.warning(
                "hub news ingest blocked: %s (%s)",
                summary.get("pause_reason"),
                summary.get("user_message") or summary.get("detail") or "llm_wiki_unavailable",
            )
        return summary
    except Exception as exc:
        logger.exception("hub news ingest job failed (mode=%s)", mode)
        return {"status": "error", "error": str(exc), "mode": mode, "had_errors": True}


def run_news_quality_eval_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the hub-news golden-dataset eval (real step_04/step_06 LLM calls,
    scored via MLflow Correctness + DeepEval) and log a trend metric.

    Non-blocking: this never fails the scheduler even on eval error, matching
    the ``continue-on-error`` behavior of the same eval in nightly CI.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.news_hub_bridge import run_news_golden_eval

    try:
        summary = run_news_golden_eval()
        return summary
    except Exception as exc:
        logger.exception("news quality golden eval failed")
        return {"status": "error", "error": str(exc), "had_errors": True}


def run_news_dedup_quality_eval_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the semantic-dedup golden-pair eval (real embedding-based merge decisions, scored via
    MLflow accuracy/precision/recall/F1 + a DeepEval LLM-judge cross-check) and log a trend
    metric — see [[2026-08-26-dedup-golden-eval-never-scheduled-dataset-too-small]]: this eval
    existed but had zero callers outside its own test, so a regression in `cluster_threshold()`/
    `events_are_merge_candidates()` had no automatic signal.

    Non-blocking, mirroring `run_news_quality_eval_job`: never fails the scheduler on eval error.
    """
    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.news_hub_bridge import run_news_dedup_golden_eval

    try:
        summary = run_news_dedup_golden_eval()
        return summary
    except Exception as exc:
        logger.exception("news dedup quality golden eval failed")
        return {"status": "error", "error": str(exc), "had_errors": True}


def dispatch_index_job_sync(job: ScheduledResearchJob) -> None:
    """Execute one index scheduled job synchronously."""
    try:
        from trade_integrations.dataflows.index_research.pipeline_cancel import clear_pipeline_cancel

        clear_pipeline_cancel()
    except ImportError:
        pass
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_INDEX_FACTOR_SNAPSHOT:
        summary = run_index_factor_snapshot_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("index factor snapshot completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_INDEX_RESEARCH:
        summary = run_index_research_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("index research completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_INDEX_PLAN_REFRESH:
        summary = run_index_plan_refresh_job(job.config)
        logger.info("index plan refresh completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_INDEX_CALIBRATION:
        summary = run_index_calibration_job(job.config)
        logger.info("index calibration completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_FORECAST_PLATFORM_RETRAIN:
        summary = run_forecast_platform_retrain_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("forecast platform retrain completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH:
        summary = run_quantile_forecast_ledger_push_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("quantile forecast ledger push completed for job %s: %s", job.id, summary)
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
    if job_type == JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP:
        summary = run_stock_history_coverage_sweep_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("stock_history coverage sweep completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_NEWS_QUALITY_EVAL:
        summary = run_news_quality_eval_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("news quality golden eval completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_NEWS_DEDUP_QUALITY_EVAL:
        summary = run_news_dedup_quality_eval_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("news dedup quality golden eval completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH:
        summary = run_global_macro_eod_refresh_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("global macro EOD refresh completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_OI_SNAPSHOT:
        summary = run_oi_snapshot_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("OI snapshot capture completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_PUMP_DUMP_PROXY:
        summary = run_pump_dump_proxy_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("pump-dump proxy capture completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_MAX_PAIN_BHAVCOPY:
        summary = run_max_pain_bhavcopy_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("max pain bhavcopy capture completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_REINFERENCE_TICK:
        summary = run_reinference_tick_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("reinference tick completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT:
        summary = run_constituent_volume_snapshot_job(job.config)
        _attach_job_result_summary(job, summary)
        logger.info("constituent volume snapshot completed for job %s: %s", job.id, summary)
        return
    raise ValueError(f"unsupported index job_type: {job_type!r}")


async def dispatch_index_job(job: ScheduledResearchJob) -> None:
    """Run an index job without blocking the asyncio event loop."""
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, dispatch_index_job_sync)


def register_default_index_jobs(store: ScheduledResearchJobStore) -> int:
    """Register default NIFTY index jobs when missing. Returns count created."""
    _cfg = get_env_config().trade
    snapshot_cron = _cfg.index_research_snapshot_cron.strip()
    full_cron = _cfg.index_research_full_cron.strip()
    coverage_sweep_cron = _cfg.stock_history_coverage_sweep_cron.strip()
    news_quality_eval_cron = _cfg.news_quality_eval_cron.strip()
    news_dedup_quality_eval_cron = _cfg.news_dedup_quality_eval_cron.strip()
    global_macro_eod_refresh_cron = _cfg.global_macro_eod_refresh_cron.strip()
    oi_snapshot_cron = _cfg.oi_snapshot_cron.strip()
    reinference_tick_cron = _cfg.reinference_tick_cron.strip()
    pump_dump_proxy_cron = _cfg.pump_dump_proxy_cron.strip()
    max_pain_bhavcopy_cron = _cfg.max_pain_bhavcopy_cron.strip()
    constituent_volume_snapshot_cron = _cfg.constituent_volume_snapshot_cron.strip()
    validate_schedule(snapshot_cron)
    validate_schedule(full_cron)
    validate_schedule(coverage_sweep_cron)
    validate_schedule(news_quality_eval_cron)
    validate_schedule(news_dedup_quality_eval_cron)
    validate_schedule(global_macro_eod_refresh_cron)
    validate_schedule(oi_snapshot_cron)
    validate_schedule(reinference_tick_cron)
    validate_schedule(pump_dump_proxy_cron)
    validate_schedule(max_pain_bhavcopy_cron)
    validate_schedule(constituent_volume_snapshot_cron)

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
                "dispatch_timeout_ms": 3_600_000,
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
            id="nifty-forecast-platform-retrain",
            prompt=(
                "Retrain the multi-factor causal forecast platform (causal graph, "
                "regime model, quantile forecast) — saves new candidate versions only, "
                "never auto-promotes"
            ),
            schedule="0 5 * * 1",
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_FORECAST_PLATFORM_RETRAIN, "ticker": "NIFTY"},
        ),
        ScheduledResearchJob(
            id="nifty-quantile-forecast-ledger-push",
            prompt=(
                "Push a live quantile-conformal Nifty range forecast into the "
                "prediction ledger for Board 1's Advisory display"
            ),
            schedule="0 9 * * 1-5",
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            timezone="Asia/Kolkata",
            config={"job_type": JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH, "ticker": "NIFTY"},
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
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "NIFTY",
                "sources": get_env_config().trade.hub_news_full_sources,
                "lookback_days": 3,
                # "full" ingest runs a known ~52min (observed 2026-08-27); the
                # shared hub_news_ingest job_type timeout (10min) is sized for
                # the "light" RSS-only variant. See
                # 2026-08-27-scheduler-dispatch-timeouts.
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="nifty-hub-news-ingest-light",
            prompt="Light hub news ingest (all env RSS feeds)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "NIFTY",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="nifty-hub-news-entity",
            prompt="Drain staging news refs into distilled hub events",
            schedule=get_env_config().trade.hub_news_entity_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_ENTITY,
                "mode": "drain",
                "ticker": "NIFTY",
                "batch_size": 200,
                "dispatch_timeout_ms": _HUB_NEWS_ENTITY_DRAIN_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="nifty-hub-news-entity-maintenance",
            prompt="Heavy hub news maintenance (repair, backfill, compact)",
            schedule=get_env_config().trade.hub_news_entity_maintenance_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_ENTITY,
                "mode": "maintenance",
                "ticker": "NIFTY",
                "batch_size": 200,
                "lookback_days": 365,
                "dispatch_timeout_ms": 3_600_000,
            },
        ),
        ScheduledResearchJob(
            id="us-hub-news-ingest-full",
            prompt="Full US market hub news ingest (SearXNG + Currents + MarketAux, daily)",
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "SPX",
                "market": "US",
                # Explicit list, not "all" — excludes moneycontrol/searxng_sector/
                # searxng_constituent/watcher, which are Nifty-50-specific sources
                # with no US equivalent yet.
                "sources": "rss,searxng,searxng_global,marketaux,currents",
                "lookback_days": 3,
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="us-hub-news-ingest-light",
            prompt="Light US market hub news ingest (RSS)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "SPX",
                "market": "US",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        # No dedicated "us-hub-news-entity" job: nifty-hub-news-entity already
        # auto-discovers and drains every ticker with pending staging refs
        # (news_entity_worker._tickers_with_pending_staging(), not just its own
        # "ticker" config value) — SPX's queued refs from the two jobs above get
        # distilled by that same existing job, no separate drain needed.
        ScheduledResearchJob(
            id="jp-hub-news-ingest-full",
            prompt="Full Japan market hub news ingest (SearXNG, daily)",
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "NIKKEI225",
                "market": "JP",
                # No currents/marketaux: live-tested 2026-08-25, Currents'
                # country="jp" query returns 0 articles (no JP-country-tagged
                # coverage at all on this account/plan) — unlike US, there's no
                # keyword fallback wired for this yet (see this job's backlog
                # item for the open follow-up). SearXNG's market-aware query
                # widening (same _ingest_searxng_ticker/_ingest_searxng_market
                # path proven for US) is the real source here.
                "sources": "rss,searxng,searxng_global",
                "lookback_days": 3,
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="jp-hub-news-ingest-light",
            prompt="Light Japan market hub news ingest (RSS)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "NIKKEI225",
                "market": "JP",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        # Same reasoning as US: no dedicated "jp-hub-news-entity" job needed —
        # nifty-hub-news-entity's pending-staging auto-discovery drains NIKKEI225 too.
        ScheduledResearchJob(
            id="cn-hub-news-ingest-full",
            prompt="Full China market hub news ingest (SearXNG + Currents, daily)",
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "CSI300",
                "market": "CN",
                # Currents included — unlike JP, live-tested 2026-08-25:
                # category="business" + country="cn" returns real, on-topic
                # articles (Evergrande, Alibaba share placement, Shein IPO,
                # etc.), not empty like JP's country query. No marketaux
                # (not configured/no key).
                "sources": "rss,searxng,searxng_global,currents",
                "lookback_days": 3,
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="cn-hub-news-ingest-light",
            prompt="Light China market hub news ingest (RSS)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "CSI300",
                "market": "CN",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        # Same reasoning as US/JP: no dedicated "cn-hub-news-entity" job needed —
        # nifty-hub-news-entity's pending-staging auto-discovery drains CSI300 too.
        ScheduledResearchJob(
            id="ru-hub-news-ingest-full",
            prompt="Full Russia market hub news ingest (RSS + Currents keyword search, daily)",
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "MOEX",
                "market": "RU",
                # No searxng/searxng_global: live-tested 2026-08-25 with two
                # different query phrasings, both returned almost entirely
                # generic Russia country-profile pages (Wikipedia, Britannica,
                # Al Jazeera) or off-topic contamination (chicken-soup recipes,
                # Fortnite downloads — a worse Bing-mismatch than SPX's "S"
                # Wikipedia-page issue) with essentially no real market
                # articles, unlike every other wired market so far. Currents'
                # plain country="ru" query is also empty (same class of gap
                # as JP), but a keyword search finds real signal Currents'
                # country filter alone misses — see currents_keywords below.
                "sources": "rss,currents",
                "currents_keywords": ["MOEX", "Russia", "stock"],
                "lookback_days": 3,
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="ru-hub-news-ingest-light",
            prompt="Light Russia market hub news ingest (RSS)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "MOEX",
                "market": "RU",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        # Same reasoning as US/JP/CN: no dedicated "ru-hub-news-entity" job needed —
        # nifty-hub-news-entity's pending-staging auto-discovery drains MOEX too.
        ScheduledResearchJob(
            id="me-hub-news-ingest-full",
            prompt="Full Middle East market hub news ingest (RSS + Currents keyword search, daily)",
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "TASI",
                "market": "ME",
                # No searxng: live-tested 2026-08-25, both TASI/Tadawul-themed
                # queries returned total noise (Stack Overflow questions, Las
                # Vegas Burger King locations — no relation to the query terms
                # at all). Currents' plain country="sa"/"ae" query is also
                # empty (same class of gap as JP/RU), and short bare tickers
                # (TASI/DFM/ADX) as keywords collide with unrelated content —
                # but the single precise term "Tadawul" (the exchange's own
                # name) returned real, clean Gulf-market articles.
                "sources": "rss,currents",
                "currents_keywords": ["Tadawul"],
                "lookback_days": 3,
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="me-hub-news-ingest-light",
            prompt="Light Middle East market hub news ingest (RSS)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "TASI",
                "market": "ME",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        # Same reasoning as US/JP/CN/RU: no dedicated "me-hub-news-entity" job needed —
        # nifty-hub-news-entity's pending-staging auto-discovery drains TASI too.
        ScheduledResearchJob(
            id="latam-hub-news-ingest-full",
            prompt="Full Latin America market hub news ingest (RSS + Currents keyword search, daily)",
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "IBOVESPA",
                "market": "LATAM",
                # No searxng: live-tested 2026-08-25 — returned Reddit forum
                # content with no relation to the query terms, including
                # NSFW-adjacent results, worse than any other market's noise
                # seen so far. Currents' country="br" query returns only 1
                # article (politics, not market-specific); the bare keyword
                # "IBOVESPA" alone (not combined with "Brazil"/"stock", which
                # dilutes into football/coffee noise) returned real, clean
                # signal instead.
                "sources": "rss,currents",
                "currents_keywords": ["IBOVESPA"],
                "lookback_days": 3,
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="latam-hub-news-ingest-light",
            prompt="Light Latin America market hub news ingest (RSS)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "IBOVESPA",
                "market": "LATAM",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        # Same reasoning as US/JP/CN/RU/ME: no dedicated "latam-hub-news-entity" job
        # needed — nifty-hub-news-entity's pending-staging auto-discovery drains
        # IBOVESPA too.
        ScheduledResearchJob(
            id="eu-hub-news-ingest-full",
            prompt="Full Europe market hub news ingest (RSS + Currents keyword search, daily)",
            schedule=get_env_config().trade.hub_news_full_ingest_cron.strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "full",
                "ticker": "EURO_STOXX_50",
                "market": "EU",
                # No searxng: not live-tested this pass (every other market's searxng
                # attempt has failed or been skipped as redundant once a working
                # rss/currents combo was found — see RU/ME/LATAM's own notes above).
                # Currents' plain country="de" query is near-empty/off-topic (2
                # articles, one unrelated) same as JP/RU/ME's gap; keywords=
                # ("DAX", "stocks") live-tested 2026-08-27 returns 10/10 clean, real
                # European-market articles (ECB rate coverage, DAX/FTSE moves) —
                # no noise at all, the cleanest of any market's currents_keywords
                # result so far.
                "sources": "rss,currents",
                "currents_keywords": ["DAX", "stocks"],
                "lookback_days": 3,
                "dispatch_timeout_ms": _HUB_NEWS_FULL_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        ScheduledResearchJob(
            id="eu-hub-news-ingest-light",
            prompt="Light Europe market hub news ingest (RSS)",
            schedule=get_env_or(
                "HUB_NEWS_LIGHT_INGEST_CRON",
                "HUB_NEWS_INGEST_CRON",
                "0 */4 * * *",
            ).strip(),
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_HUB_NEWS_INGEST,
                "mode": "light",
                "ticker": "EURO_STOXX_50",
                "market": "EU",
                "sources": get_env_config().trade.hub_news_light_sources,
                "lookback_days": 1,
                "dispatch_timeout_ms": _HUB_NEWS_LIGHT_TIGHT_INGEST_DISPATCH_TIMEOUT_MS,
            },
        ),
        # Same reasoning as US/JP/CN/RU/ME/LATAM: no dedicated "eu-hub-news-entity" job
        # needed — nifty-hub-news-entity's pending-staging auto-discovery drains
        # EURO_STOXX_50 too.
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
        ScheduledResearchJob(
            id="nifty-news-quality-eval",
            prompt="Score hub news enrichment quality against the golden dataset (MLflow + DeepEval)",
            schedule=news_quality_eval_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_NEWS_QUALITY_EVAL,
                "ticker": "NIFTY",
                "dispatch_timeout_ms": 1_800_000,
            },
        ),
        ScheduledResearchJob(
            id="nifty-news-dedup-quality-eval",
            prompt="Score hub news semantic-dedup quality against the golden pair dataset (MLflow + DeepEval)",
            schedule=news_dedup_quality_eval_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_NEWS_DEDUP_QUALITY_EVAL,
                "ticker": "NIFTY",
                "dispatch_timeout_ms": 1_800_000,
            },
        ),
        ScheduledResearchJob(
            id="stock-history-coverage-sweep",
            prompt="Daily full-coverage backfill sweep (all stock_history buckets)",
            schedule=coverage_sweep_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP,
                "include_optional": True,
                "dispatch_timeout_ms": 1_800_000,
            },
        ),
        ScheduledResearchJob(
            id="global-macro-eod-refresh",
            prompt="Daily refresh of global macro EOD series from yfinance (oil, gold, us_10y, sp500)",
            schedule=global_macro_eod_refresh_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH,
                "lookback_days": 90,
            },
        ),
        ScheduledResearchJob(
            id="nifty-oi-snapshot",
            prompt="Daily forward-only NIFTY OI/max-pain snapshot capture (accumulates history for backtesting)",
            schedule=oi_snapshot_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_OI_SNAPSHOT,
                "underlying": "NIFTY",
            },
        ),
        ScheduledResearchJob(
            id="nifty-pump-dump-proxy",
            prompt="Daily post-close NIFTY pump-and-dump proxy capture (accumulates history for backtesting)",
            schedule=pump_dump_proxy_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_PUMP_DUMP_PROXY,
                "symbol": "NIFTY",
            },
        ),
        ScheduledResearchJob(
            id="nifty-max-pain-bhavcopy",
            prompt="Daily post-close NIFTY max-pain reconstruction from NSE's F&O bhavcopy (primary max-pain source — no broker session needed)",
            schedule=max_pain_bhavcopy_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            timezone="Asia/Kolkata",
            config={
                "job_type": JOB_TYPE_MAX_PAIN_BHAVCOPY,
                "symbol": "NIFTY",
            },
        ),
        ScheduledResearchJob(
            id="nifty-reinference-tick",
            prompt=(
                "Frequent poll for module 3's event/heartbeat-triggered fusion-forecast "
                "re-inference (cheap no-op unless a price move, material news, or the "
                "heartbeat fallback actually warrants recomputing)"
            ),
            schedule=reinference_tick_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_REINFERENCE_TICK,
                "ticker": "NIFTY",
            },
        ),
        ScheduledResearchJob(
            id="nifty50-constituent-volume-snapshot",
            prompt=(
                "Frequent intraday capture of per-constituent volume/interest snapshots "
                "(relative volume vs 20d baseline) for all NIFTY50 constituents"
            ),
            schedule=constituent_volume_snapshot_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT,
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
        poll_cron = get_env_config().trade.index_monitor_poll_cron.strip()
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

    # Tight-cadence variants: one per market's existing "-hub-news-ingest-light" job,
    # same config (RSS-only by default via hub_news_light_sources — no extra SearXNG
    # load), just a much shorter interval. Per-market news timeline density for
    # 2026-08-27-market-news-timeline-recording; dedup against already-drained refs
    # is handled by news_staging_store.enqueue_raw_ref's merged-ledger check.
    #
    # Default cadence widened from */5 to */15 (2026-08-30): at every-5-minutes across
    # 8 markets, runs were piling up in the executor's strictly-serial dispatch queue
    # (see 2026-08-30-scheduler-sequential-dispatch-drains-slowly) and individual runs
    # were blowing past the 10-min hub_news_ingest budget
    # (2026-08-30-hub-news-ingest-tight-light-dispatch-timeout-undersized) — raising
    # the timeout papers over the queue backup rather than fixing it, so instead this
    # reduces how often a fresh batch of 8 markets' tight jobs can become
    # simultaneously due, cutting worst-case queue depth ahead of any one run to a
    # third of what it was. Still far denser than -light's default 4-hour cadence, so
    # the tight job still serves its per-market timeline-density purpose.
    tight_cron = os.environ.get("HUB_NEWS_TIGHT_INGEST_CRON", "*/15 * * * *").strip() or "*/15 * * * *"
    validate_schedule(tight_cron)
    tight_jobs = []
    for job in defaults:
        if not job.id.endswith("-hub-news-ingest-light"):
            continue
        tight_prompt = (
            job.prompt.replace("Light ", "Tight-cadence ", 1)
            if job.prompt.startswith("Light ")
            else f"Tight-cadence {job.prompt}"
        )
        tight_jobs.append(
            dataclasses.replace(
                job,
                id=job.id.replace("-hub-news-ingest-light", "-hub-news-ingest-tight"),
                prompt=tight_prompt,
                schedule=tight_cron,
                config=dict(job.config),
            )
        )
    defaults.extend(tight_jobs)

    created = 0
    try:
        _ensure_trade_integrations_on_path()
        from trade_integrations.dataflows.news_hub_bridge import sync_scheduled_jobs

        sync_scheduled_jobs()
    except Exception as exc:
        logger.warning("hub news pipeline job sync failed: %s", exc)

    for job in defaults:
        if store.get(job.id) is not None:
            continue
        store.upsert(job)
        created += 1
        logger.info("registered default index research job %s (%s)", job.id, job.schedule)
    return created
