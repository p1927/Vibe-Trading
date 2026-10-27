"""Trade-plan widget read and plan-context routes. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

from datetime import datetime, timezone
from fastapi import Depends, HTTPException
from src.api.helpers import bad_gateway_on_error
from src.api.security import require_local_or_auth
from trade_integrations.trade_widgets.store import load_trade_widget
from typing import Any, Dict
from .trade_routes import _ticker_key, logger, trade_router


@trade_router.get("/widget/{widget_id}")
def get_widget(
    widget_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Reload a persisted trade-plan widget."""
    widget = load_trade_widget(widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found")
    return widget


def _widget_staleness_from_report(report: Any) -> dict[str, Any]:
    return {
        "status": report.status,
        "reasons": list(report.reasons or []),
        "spot_drift_pct": report.spot_drift_pct,
    }


def _live_context_from_report(report: Any) -> dict[str, Any]:
    return {
        "spot": report.live_spot,
        "plan_spot": report.plan_spot,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _count_verified_headlines_since(ticker: str, since: datetime) -> int:
    """Canonical verified hub stories ingested or published since analysis as_of."""
    from trade_integrations.dataflows.news_hub_bridge import now_instant, query_verified_news

    since_day = since.astimezone(timezone.utc).strftime("%Y-%m-%d")
    records = query_verified_news(as_of=now_instant(), ticker=ticker, since=since_day, limit=200)
    if not records:
        return 0

    since_iso = since.astimezone(timezone.utc).isoformat()
    seen_ids: set[str] = set()
    count = 0
    for rec in records:
        story_id = str(rec.get("canonical_story_id") or "").strip()
        if story_id and story_id in seen_ids:
            continue

        first_seen = str(rec.get("first_seen_at") or "")
        if first_seen and first_seen >= since_iso:
            if story_id:
                seen_ids.add(story_id)
            count += 1
            continue

        tags = rec.get("tags") if isinstance(rec.get("tags"), dict) else {}
        pub_day = str(tags.get("publish_day") or rec.get("published_at") or "")[:10]
        if pub_day and pub_day >= since_day:
            if story_id:
                seen_ids.add(story_id)
            count += 1
    return count


def _material_news_count(ticker: str) -> int:
    try:
        from trade_integrations.dataflows.entity_registry import india_index_tickers
        from trade_integrations.monitor.news_watcher import count_material_headlines_since, news_since

        key = ticker.strip().upper()
        is_index = key in india_index_tickers()
        since = news_since(key, index=is_index)
        if is_index:
            hub_count = _count_verified_headlines_since(key, since)
            if hub_count:
                return hub_count
        return count_material_headlines_since(key, since)
    except Exception:
        logger.exception("Material news count failed for %s", ticker)
        return 0


def _has_open_plan_position(ticker: str) -> bool:
    try:
        from trade_integrations.monitor.execution_ledger import has_open_position_for_underlying

        return bool(has_open_position_for_underlying(ticker))
    except ImportError:
        return False
    except Exception:
        logger.exception("Execution ledger lookup failed for %s", ticker)
        return False


@trade_router.get("/plan-context/{ticker}")
def get_plan_context(
    ticker: str,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Live staleness, spot drift, and news context for mounted trade widgets."""
    key = _ticker_key(ticker, default="")
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")

    try:
        from trade_integrations.monitor.service import MonitorService
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not MonitorService.is_enabled():
        return {"monitor_enabled": False}

    with bad_gateway_on_error(logger, "plan-context failed for %s", key):
        report = MonitorService().evaluate_ticker(key)

    if report is None:
        return {
            "ticker": key,
            "monitor_enabled": True,
            "staleness": {"status": "broken", "reasons": ["monitor_unavailable"], "spot_drift_pct": None},
            "live_context": {"spot": None, "plan_spot": None, "fetched_at": datetime.now(timezone.utc).isoformat()},
            "material_news_count": 0,
            "open_position": False,
        }

    return {
        "ticker": key,
        "monitor_enabled": True,
        "staleness": _widget_staleness_from_report(report),
        "live_context": _live_context_from_report(report),
        "material_news_count": _material_news_count(key),
        "open_position": _has_open_plan_position(key),
    }
