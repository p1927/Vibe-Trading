"""Scheduled options monitor jobs (plan refresh + position monitor stub)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule

logger = logging.getLogger(__name__)

OPTIONS_MONITOR_ENABLE_SCHEDULER_ENV = "OPTIONS_MONITOR_ENABLE_SCHEDULER"
DEFAULT_OPTIONS_POLL_CRON = "*/5 * * * *"

JOB_TYPE_OPTIONS_PLAN_REFRESH = "options_plan_refresh"
JOB_TYPE_OPTIONS_POSITION_MONITOR = "options_position_monitor"

OPTIONS_JOB_TYPES = frozenset(
    {JOB_TYPE_OPTIONS_PLAN_REFRESH, JOB_TYPE_OPTIONS_POSITION_MONITOR}
)

_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_options_scheduler_enabled(value: str | None = None) -> bool:
    """Return whether default options monitor jobs should register on startup."""
    if value is not None:
        return value.strip().lower() in _TRUE_VALUES
    return (
        os.getenv(OPTIONS_MONITOR_ENABLE_SCHEDULER_ENV, "").strip().lower()
        in _TRUE_VALUES
    )


def is_options_monitor_active() -> bool:
    """Both master monitor switch and scheduler env must be enabled."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.monitor.config import is_monitor_enabled

    return is_monitor_enabled() and is_options_scheduler_enabled()


def _ensure_trade_integrations_on_path() -> None:
    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def _parse_as_of(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def refresh_options_research(ticker: str, *, config: dict[str, Any] | None = None) -> bool:
    """Run the options research pipeline and persist to hub when eligible."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.context.hub import save_options_research
    from trade_integrations.dataflows.options_research.aggregator import run_options_research
    from trade_integrations.dataflows.options_research.market import is_options_research_eligible

    sym = str(ticker).strip().upper()
    if not sym or not is_options_research_eligible(sym):
        logger.debug("options research refresh skipped for %s: not eligible", sym or ticker)
        return False

    cfg = config or {}
    doc = run_options_research(
        sym,
        expiry_date=cfg.get("expiry_date"),
        lookahead_days=cfg.get("lookahead_days"),
    )
    save_options_research(doc)
    return True


def _news_since_for_ticker(ticker: str) -> datetime:
    _ensure_trade_integrations_on_path()
    from trade_integrations.context.hub import load_options_research_json

    doc = load_options_research_json(ticker)
    if doc is None:
        return datetime.now(timezone.utc) - timedelta(days=1)

    as_of = _parse_as_of(getattr(doc, "as_of", None))
    if as_of is None and isinstance(doc, dict):
        as_of = _parse_as_of(doc.get("as_of"))
    if as_of is not None:
        return as_of
    return datetime.now(timezone.utc) - timedelta(days=1)


def _ticker_needs_refresh(ticker: str, *, config: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
    _ensure_trade_integrations_on_path()
    from trade_integrations.monitor.news_watcher import check_material_news
    from trade_integrations.monitor.service import MonitorService

    reasons: list[str] = []
    since = _news_since_for_ticker(ticker)
    headlines = check_material_news(ticker, since)
    if headlines:
        reasons.append("material_news")

    report = MonitorService().evaluate_ticker(ticker)
    if report is not None and report.status in {"stale", "broken"}:
        reasons.extend(report.reasons or [report.status])

    return bool(reasons), reasons


def run_options_plan_refresh_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh watchlist tickers when plans are stale or material news appears."""
    if not is_options_monitor_active():
        logger.info("options plan refresh skipped: monitor or scheduler disabled")
        return {"skipped": True, "reason": "monitor_disabled"}

    _ensure_trade_integrations_on_path()
    from trade_integrations.dataflows.options_research.prediction_ledger import (
        reconcile_options_predictions,
    )
    from trade_integrations.monitor.config import get_monitor_config

    reconciled = reconcile_options_predictions()

    cfg = config or {}
    watchlist = cfg.get("watchlist")
    if not watchlist:
        watchlist = list(get_monitor_config().watchlist)
    try:
        from trade_integrations.autonomous_agents.market import agent_execution_market
        from trade_integrations.autonomous_agents.store import list_agents
        from trade_integrations.dataflows.options_research.market import is_options_research_eligible

        extra: set[str] = set()
        for agent in list_agents() or []:
            if str(agent.get("status") or "") not in ("running", "paused"):
                continue
            if agent_execution_market(agent) == "US":
                continue
            for sym in agent.get("symbols") or []:
                s = str(sym).strip().upper()
                if s and is_options_research_eligible(s):
                    extra.add(s)
        if extra:
            watchlist = sorted(set(watchlist) | extra)
    except Exception:
        pass

    refreshed: list[dict[str, Any]] = []
    skipped: list[str] = []
    ineligible: list[str] = []

    for raw_ticker in watchlist:
        ticker = str(raw_ticker).strip().upper()
        if not ticker:
            continue

        from trade_integrations.dataflows.options_research.market import is_options_research_eligible

        if not is_options_research_eligible(ticker):
            ineligible.append(ticker)
            continue

        needs_refresh, reasons = _ticker_needs_refresh(ticker, config=cfg)
        if not needs_refresh:
            skipped.append(ticker)
            continue

        if refresh_options_research(ticker, config=cfg):
            refreshed.append({"ticker": ticker, "reasons": reasons})
            logger.info("options plan refreshed for %s (%s)", ticker, ", ".join(reasons))

    return {
        "skipped": False,
        "reconciled_predictions": reconciled,
        "refreshed": refreshed,
        "unchanged": skipped,
        "ineligible": ineligible,
    }


def run_options_position_monitor_job(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate thesis breaks for open ledger entries and refresh superseding widgets."""
    if not is_options_monitor_active():
        logger.info("options position monitor skipped: monitor or scheduler disabled")
        return {"skipped": True, "reason": "monitor_disabled"}

    _ensure_trade_integrations_on_path()
    from trade_integrations.context.hub import save_options_research
    from trade_integrations.dataflows.options_research.aggregator import run_options_research
    from trade_integrations.dataflows.options_research.widget_payload import (
        build_options_trade_widget_from_doc,
    )
    from trade_integrations.monitor.execution_ledger import list_open_entries
    from trade_integrations.monitor.service import MonitorService

    cfg = config or {}
    service = MonitorService()
    broken: list[dict[str, Any]] = []
    refreshed: list[dict[str, Any]] = []

    for entry in list_open_entries():
        widget_id = str(entry.get("widget_id") or "").strip()
        underlying = str(entry.get("underlying") or "").strip().upper()
        if not widget_id or not underlying:
            continue

        report = service.evaluate_position_thesis(widget_id)
        if report is None or not report.broken:
            continue

        broken.append(
            {
                "widget_id": widget_id,
                "underlying": underlying,
                "reasons": report.reasons,
                "severity": report.severity,
            }
        )

        from trade_integrations.dataflows.options_research.market import is_options_research_eligible

        if not is_options_research_eligible(underlying):
            continue

        doc = run_options_research(
            underlying,
            expiry_date=cfg.get("expiry_date"),
            lookahead_days=cfg.get("lookahead_days"),
        )
        save_options_research(doc)
        revision_reason = "; ".join(report.reasons)
        widget = build_options_trade_widget_from_doc(
            doc,
            supersedes=widget_id,
            revision_reason=revision_reason,
        )
        new_widget_id = widget.get("widget_id")
        if new_widget_id:
            widget_dir = Path.home() / ".vibe-trading" / "trade_widgets"
            widget_dir.mkdir(parents=True, exist_ok=True)
            widget_path = widget_dir / f"{new_widget_id}.json"
            widget_path.write_text(
                json.dumps(widget, indent=2, default=str),
                encoding="utf-8",
            )

        refreshed.append(
            {
                "old_widget_id": widget_id,
                "new_widget_id": new_widget_id,
                "underlying": underlying,
                "revision_reason": revision_reason,
            }
        )
        logger.info(
            "thesis break for %s (%s) — refreshed widget %s",
            underlying,
            revision_reason,
            new_widget_id,
        )

        try:
            from trade_integrations.auto_paper.session_store import load_session, save_session

            paper_session = load_session()
            if paper_session.get("enabled") and paper_session.get("autonomous"):
                urgent = list(paper_session.get("urgent_alerts") or [])
                urgent.append(
                    {
                        "type": "thesis_break",
                        "widget_id": widget_id,
                        "underlying": underlying,
                        "reasons": list(report.reasons or []),
                    }
                )
                paper_session["urgent_alerts"] = urgent[-20:]
                save_session(paper_session)

                try:
                    from src.scheduled_research.auto_paper_jobs import dispatch_thesis_break_agent_turn

                    asyncio.get_event_loop().create_task(
                        dispatch_thesis_break_agent_turn(
                            underlying,
                            widget_id,
                            list(report.reasons or []),
                        )
                    )
                except RuntimeError:
                    asyncio.run(
                        dispatch_thesis_break_agent_turn(
                            underlying,
                            widget_id,
                            list(report.reasons or []),
                        )
                    )
                except Exception:
                    logger.debug("thesis break agent dispatch failed", exc_info=True)

                agent_id = str(paper_session.get("autonomous_agent_id") or "").strip()
                if agent_id:
                    try:
                        from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning

                        asyncio.get_event_loop().create_task(
                            dispatch_full_reasoning(agent_id, turn_kind="strategy_revision")
                        )
                    except Exception:
                        logger.debug("autonomous agent thesis revision skipped", exc_info=True)
        except Exception:
            logger.debug("auto paper urgent alert skipped", exc_info=True)

    return {"skipped": False, "broken": broken, "refreshed": refreshed}


def dispatch_options_job_sync(job: ScheduledResearchJob) -> None:
    """Execute one options scheduled job synchronously."""
    job_type = str(job.config.get("job_type") or "")
    if job_type == JOB_TYPE_OPTIONS_PLAN_REFRESH:
        summary = run_options_plan_refresh_job(job.config)
        logger.info("options plan refresh completed for job %s: %s", job.id, summary)
        return
    if job_type == JOB_TYPE_OPTIONS_POSITION_MONITOR:
        summary = run_options_position_monitor_job(job.config)
        logger.info("options position monitor completed for job %s: %s", job.id, summary)
        return
    raise ValueError(f"unsupported options job_type: {job_type!r}")


async def dispatch_options_job(job: ScheduledResearchJob) -> None:
    """Run an options job without blocking the asyncio event loop."""
    await asyncio.to_thread(dispatch_options_job_sync, job)


def register_default_options_jobs(store) -> int:
    """Register default options monitor jobs when missing. Returns count created."""
    if not is_options_scheduler_enabled():
        return 0

    _ensure_trade_integrations_on_path()
    from trade_integrations.monitor.config import get_monitor_config

    poll_cron = os.getenv("OPTIONS_MONITOR_POLL_CRON", DEFAULT_OPTIONS_POLL_CRON).strip()
    validate_schedule(poll_cron)
    watchlist = list(get_monitor_config().watchlist)

    now_ms = int(time.time() * 1000)
    defaults = [
        ScheduledResearchJob(
            id="options-plan-refresh",
            prompt="Refresh stale options research plans for monitor watchlist",
            schedule=poll_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={
                "job_type": JOB_TYPE_OPTIONS_PLAN_REFRESH,
                "watchlist": watchlist,
            },
        ),
        ScheduledResearchJob(
            id="options-position-monitor",
            prompt="Monitor open options plan positions for thesis breaks",
            schedule=poll_cron,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_OPTIONS_POSITION_MONITOR},
        ),
    ]

    created = 0
    for job in defaults:
        if store.get(job.id) is not None:
            continue
        store.upsert(job)
        created += 1
        logger.info("registered default options monitor job %s (%s)", job.id, job.schedule)
    return created
