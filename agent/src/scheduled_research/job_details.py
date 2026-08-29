"""Plain-English description + optional cheap live preview per ``job_type``.

Sibling to :mod:`sections.py` (same "gather per-pipeline facts into one
dict" shape) rather than an extension of it — ``sections.py`` is narrowly
scoped to turning a job type into a section label, and folding descriptions
in would overload that single responsibility.

A preview callable must be cheap and side-effect-free: it may read local
config/registry files and call pure helper functions, but it must never
call a job's real ``run_*_job`` function or perform the job's actual
network/LLM work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .autonomous_agent_jobs import (
    JOB_TYPE_INFRA_HEAL,
    JOB_TYPE_NEWS as JOB_TYPE_AUTONOMOUS_NEWS,
    JOB_TYPE_QUANT,
    JOB_TYPE_RESEARCH as JOB_TYPE_AUTONOMOUS_RESEARCH,
    JOB_TYPE_STRATEGY_REVIEW,
    JOB_TYPE_WATCH,
)
from .capture_jobs import (
    JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT,
    JOB_TYPE_HUB_CAPTURE_INTRADAY,
)
from .hub_calibration_jobs import (
    JOB_TYPE_HUB_EVENING_MAINTENANCE,
    JOB_TYPE_HUB_MORNING_CALIBRATION,
)
from .index_jobs import (
    JOB_TYPE_COMPANY_RESEARCH_ARCHIVE,
    JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT,
    JOB_TYPE_FORECAST_PLATFORM_RETRAIN,
    JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH,
    JOB_TYPE_HUB_NEWS_ENTITY,
    JOB_TYPE_HUB_NEWS_INGEST,
    JOB_TYPE_INDEX_CALIBRATION,
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT,
    JOB_TYPE_INDEX_PLAN_REFRESH,
    JOB_TYPE_INDEX_PREDICTION_POST_CLOSE,
    JOB_TYPE_INDEX_RESEARCH,
    JOB_TYPE_MAX_PAIN_BHAVCOPY,
    JOB_TYPE_NEWS_DEDUP_QUALITY_EVAL,
    JOB_TYPE_NEWS_QUALITY_EVAL,
    JOB_TYPE_OI_SNAPSHOT,
    JOB_TYPE_PUMP_DUMP_PROXY,
    JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH,
    JOB_TYPE_REINFERENCE_TICK,
    JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP,
)
from .options_jobs import JOB_TYPE_OPTIONS_PLAN_REFRESH, JOB_TYPE_OPTIONS_POSITION_MONITOR
from .recording_wake_jobs import JOB_TYPE_RECORDING_WAKE
from .trade_data_jobs import (
    JOB_TYPE_NSE_MACRO_REFRESH,
    JOB_TYPE_NSE_REPO_CONSISTENCY,
    JOB_TYPE_RESEARCH_HISTORY_ARCHIVE,
    JOB_TYPE_TRADE_FILLS_EXPORT,
)

GENERIC_DESCRIPTION = "No description registered for this job type."

_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class JobTypeDetail:
    description: str
    preview: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = field(default=None)


def _preview_hub_news_ingest(config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolved RSS feed URLs for the job's market/ticker, plus which other
    configured sources this run would also hit (not enumerable cheaply —
    searxng/watcher/marketaux/currents all require a live call to know
    "what would it fetch")."""
    from trade_integrations.dataflows.news_hub_bridge.internal.hub_news_ingest import (
        _apply_light_source_guard,
        _parse_sources,
    )
    from trade_integrations.dataflows.rss_feeds import _resolve_url, get_sentiment_rss_feeds
    from trade_integrations.hub_storage.news_pipeline_config import load_news_pipeline_config

    mode = str(config.get("mode") or "full").strip().lower()
    market = str(config.get("market") or "IN").strip().upper()
    ticker = str(config.get("ticker") or "NIFTY").strip().upper()
    sources_cfg = config.get("sources")

    if sources_cfg is None or sources_cfg == "default":
        cfg = load_news_pipeline_config()
        sources_cfg = cfg.light_ingest_sources if mode == "light" else cfg.full_ingest_sources

    selected = _apply_light_source_guard(_parse_sources(sources_cfg), ingest_mode=mode)
    feeds = get_sentiment_rss_feeds(market)
    urls = [_resolve_url(f["url"], ticker) for f in feeds] if "rss" in selected else []
    other_sources = sorted(selected - {"rss"})

    note_parts = [f"mode={mode}, market={market}, ticker={ticker}"]
    if "rss" in selected:
        note_parts.append(f"{len(urls)} RSS feed URLs listed below")
    else:
        note_parts.append("RSS not selected for this run")
    if other_sources:
        note_parts.append(
            f"also configured (not previewable without a live call): {', '.join(other_sources)}"
        )
    return {"items": urls, "note": "; ".join(note_parts)}


def _preview_hub_capture_factor_snapshot(config: Dict[str, Any]) -> Dict[str, Any]:
    """Which capture-enabled entities/factors are currently due for a snapshot."""
    from trade_integrations.hub_capture.gate import should_capture
    from trade_integrations.hub_capture.registry import load_registry

    reg = load_registry(create=False)
    entities = reg.get("entities") or []
    entity_id = config.get("entity_id")
    if entity_id:
        target = str(entity_id).strip().upper()
        entities = [e for e in entities if str(e.get("id") or "").upper() == target]

    items = []
    for entity in entities:
        eid = str(entity.get("id") or "").upper()
        if not eid or not entity.get("capture_enabled"):
            continue
        factors = [f for f in ("flows", "vix") if should_capture(eid, f, registry=reg)]
        if factors:
            items.append({"entity_id": eid, "factors": factors})
    note = None if items else "No capture-enabled entities currently due for flows/vix capture."
    return {"items": items, "note": note}


_JOB_DETAILS: Dict[str, JobTypeDetail] = {
    JOB_TYPE_HUB_NEWS_INGEST: JobTypeDetail(
        "Fetches news from RSS (and, in full mode, SearXNG/watcher/MarketAux/Currents) into "
        "hub news staging for this ticker/market.",
        preview=_preview_hub_news_ingest,
    ),
    JOB_TYPE_HUB_CAPTURE_FACTOR_SNAPSHOT: JobTypeDetail(
        "Captures FII/DII flow and India VIX snapshots for capture-enabled entities into the "
        "proprietary hub factor history.",
        preview=_preview_hub_capture_factor_snapshot,
    ),
    JOB_TYPE_HUB_CAPTURE_INTRADAY: JobTypeDetail(
        "Captures an intraday NIFTY option-chain snapshot for proprietary factor history."
    ),
    JOB_TYPE_HUB_NEWS_ENTITY: JobTypeDetail(
        "Drains the news staging queue into distilled hub events, or (in maintenance mode) "
        "runs heavier entity/wiki repair and backfill."
    ),
    JOB_TYPE_INDEX_FACTOR_SNAPSHOT: JobTypeDetail(
        "Captures a snapshot of index-level prediction factors."
    ),
    JOB_TYPE_INDEX_RESEARCH: JobTypeDetail("Runs the index research pipeline for a ticker."),
    JOB_TYPE_INDEX_PLAN_REFRESH: JobTypeDetail("Refreshes the index trading plan for a ticker."),
    JOB_TYPE_INDEX_CALIBRATION: JobTypeDetail(
        "Runs index prediction calibration against recent outcomes."
    ),
    JOB_TYPE_COMPANY_RESEARCH_ARCHIVE: JobTypeDetail(
        "Archives a company-research document snapshot (LLM-driven)."
    ),
    JOB_TYPE_INDEX_PREDICTION_POST_CLOSE: JobTypeDetail(
        "Runs the post-close index prediction pipeline."
    ),
    JOB_TYPE_STOCK_HISTORY_COVERAGE_SWEEP: JobTypeDetail(
        "Sweeps stock price-history coverage for gaps and backfills them."
    ),
    JOB_TYPE_NEWS_QUALITY_EVAL: JobTypeDetail(
        "Runs the hub-news golden-dataset quality eval and logs a trend metric (LLM-driven)."
    ),
    JOB_TYPE_NEWS_DEDUP_QUALITY_EVAL: JobTypeDetail(
        "Runs the semantic-dedup golden-pair eval and logs a trend metric (embedding-driven)."
    ),
    JOB_TYPE_GLOBAL_MACRO_EOD_REFRESH: JobTypeDetail(
        "Refreshes end-of-day global macro indicators."
    ),
    JOB_TYPE_OI_SNAPSHOT: JobTypeDetail("Captures an options open-interest snapshot."),
    JOB_TYPE_REINFERENCE_TICK: JobTypeDetail(
        "Cheap poll that decides whether price/news materiality warrants recomputing the "
        "fusion forecast."
    ),
    JOB_TYPE_PUMP_DUMP_PROXY: JobTypeDetail("Computes a pump/dump proxy signal."),
    JOB_TYPE_MAX_PAIN_BHAVCOPY: JobTypeDetail(
        "Backfills max-pain history for a trading day from bhavcopy data."
    ),
    JOB_TYPE_CONSTITUENT_VOLUME_SNAPSHOT: JobTypeDetail(
        "Captures a per-NIFTY50-constituent volume/relative-interest snapshot."
    ),
    JOB_TYPE_FORECAST_PLATFORM_RETRAIN: JobTypeDetail(
        "Retrains the forecast-platform model (LLM/ML pipeline)."
    ),
    JOB_TYPE_QUANTILE_FORECAST_LEDGER_PUSH: JobTypeDetail(
        "Pushes quantile forecast entries to the prediction ledger."
    ),
    JOB_TYPE_OPTIONS_PLAN_REFRESH: JobTypeDetail(
        "Refreshes options research/plans for the current watchlist when stale or material "
        "news has landed."
    ),
    JOB_TYPE_OPTIONS_POSITION_MONITOR: JobTypeDetail(
        "Evaluates thesis breaks on open options ledger entries and refreshes related widgets."
    ),
    JOB_TYPE_TRADE_FILLS_EXPORT: JobTypeDetail("Exports OpenAlgo trade fills."),
    JOB_TYPE_RESEARCH_HISTORY_ARCHIVE: JobTypeDetail(
        "Archives options/stock research snapshots for a date."
    ),
    JOB_TYPE_NSE_MACRO_REFRESH: JobTypeDetail("Refreshes NSE macro reference data."),
    JOB_TYPE_NSE_REPO_CONSISTENCY: JobTypeDetail(
        "Checks NSE repo data for internal consistency."
    ),
    JOB_TYPE_HUB_MORNING_CALIBRATION: JobTypeDetail(
        "Runs the morning hub calibration orchestrator."
    ),
    JOB_TYPE_HUB_EVENING_MAINTENANCE: JobTypeDetail(
        "Runs the evening hub maintenance orchestrator."
    ),
    JOB_TYPE_WATCH: JobTypeDetail("Autonomous agent: watches for new triggers."),
    JOB_TYPE_AUTONOMOUS_RESEARCH: JobTypeDetail("Autonomous agent: runs a research pass."),
    JOB_TYPE_QUANT: JobTypeDetail("Autonomous agent: runs a quant analysis pass."),
    JOB_TYPE_INFRA_HEAL: JobTypeDetail("Autonomous agent: runs an infrastructure self-heal pass."),
    JOB_TYPE_AUTONOMOUS_NEWS: JobTypeDetail("Autonomous agent: runs a news-driven pass."),
    JOB_TYPE_STRATEGY_REVIEW: JobTypeDetail("Autonomous agent: reviews an open strategy."),
    JOB_TYPE_RECORDING_WAKE: JobTypeDetail(
        "Wakes a paused 'wait for market open' recording job and resumes its worker."
    ),
}


def job_type_detail(job_type: str) -> JobTypeDetail:
    """Static description + optional preview callable for ``job_type``.

    Never raises — an unrecognized or empty job type falls back to a
    generic description with no preview, matching :func:`sections.job_section`'s
    never-raise-on-unknown convention.
    """
    return _JOB_DETAILS.get(job_type, JobTypeDetail(GENERIC_DESCRIPTION))
