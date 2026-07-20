"""Trade-stack widget persistence and OpenAlgo execution proxy for Vibe chat."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.security import require_local_or_auth
from trade_integrations.trade_widgets.store import load_trade_widget

logger = logging.getLogger(__name__)

trade_router = APIRouter(prefix="/trade", tags=["trade"])

_WIDGET_ID_RE = re.compile(r"(?:tp|ts|ti|ns)_[A-Z][A-Z0-9]*_[0-9a-f]{12}")
_WIDGET_ID_INLINE_RE = re.compile(r"((?:tp|ts|ti|ns)_[A-Z][A-Z0-9]*_[0-9a-f]{12})")
_WIDGET_TOOL_NAMES = frozenset(
    {
        "get_options_trade_widget",
        "mcp_openalgo_get_options_trade_widget",
        "get_stock_trade_widget",
        "mcp_openalgo_get_stock_trade_widget",
        "get_index_trade_widget",
        "mcp_openalgo_get_index_trade_widget",
        "get_news_scenario_widget",
        "mcp_openalgo_get_news_scenario_widget",
    }
)


def trade_widget_dir() -> Path:
    from trade_integrations.trade_widgets.store import trade_widget_dir as _dir

    return _dir()


def trade_widget_dir() -> Path:
    from trade_integrations.trade_widgets.store import trade_widget_dir as _dir

    return _dir()


def _widget_id_from_preview(preview: str) -> Optional[str]:
    """Extract widget id from tool_result preview (often escaped/truncated JSON)."""
    text = preview or ""
    inline = _WIDGET_ID_INLINE_RE.search(text)
    if inline:
        return inline.group(1)
    match = re.search(r'"widget_id"\s*:\s*"((?:tp|ts|ti|ns)_[^"]+)"', text)
    if match and _WIDGET_ID_RE.fullmatch(match.group(1)):
        return match.group(1)
    return None


def trade_plan_widget_frame_from_tool_result(event: Any) -> Optional[str]:
    """Build trade_plan.widget SSE frame from MCP tool_result."""
    data = getattr(event, "data", None)
    if getattr(event, "event_type", None) != "tool_result" or not isinstance(data, dict):
        return None
    tool = str(data.get("tool") or "")
    if tool not in _WIDGET_TOOL_NAMES or data.get("status") != "ok":
        return None
    preview = str(data.get("preview") or "")
    widget_id = _widget_id_from_preview(preview)
    if not widget_id:
        return None
    widget = load_trade_widget(widget_id)
    if widget is None:
        return None
    from src.session.events import SSEEvent

    frame = SSEEvent(
        event_type="trade_plan.widget",
        data=widget,
        session_id=getattr(event, "session_id", "") or "",
    )
    return frame.to_sse()


class ExecuteBasketRequest(BaseModel):
    widget_id: str | None = None
    orders: List[Dict[str, Any]] = Field(default_factory=list)
    strategy: str = "vibe_trade_plan"


class ExecuteBasketResponse(BaseModel):
    status: str
    results: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""
    execution_mode: str = "live"


class TradeChargesRequest(BaseModel):
    legs: List[Dict[str, Any]] = Field(default_factory=list)
    spot: float | None = None
    broker_preset: str = "zerodha"
    include_exit: bool = True


class TradeChargesResponse(BaseModel):
    status: str
    charges: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class ExecutionModeResponse(BaseModel):
    mode: str
    analyze_mode: bool
    paper_env: bool
    live_allowed: bool
    switch_url: str = ""


def _openalgo_switch_url(host: str) -> str:
    return f"{host.rstrip('/')}/"


def _resolve_execution_mode(analyze: bool, paper_env: bool) -> ExecutionModeResponse:
    """OpenAlgo UI is authoritative; paper_env only blocks live from Vibe."""
    mode = "paper" if analyze else "live"
    live_allowed = not paper_env
    try:
        host, _ = _openalgo_config()
        switch_url = _openalgo_switch_url(host)
    except HTTPException:
        switch_url = ""
    return ExecutionModeResponse(
        mode=mode,
        analyze_mode=analyze,
        paper_env=paper_env,
        live_allowed=live_allowed,
        switch_url=switch_url,
    )


def _openalgo_config() -> tuple[str, str]:
    host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001").rstrip("/")
    api_key = os.getenv("OPENALGO_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENALGO_API_KEY not configured for execution",
        )
    return host, api_key


def _paper_mode_env_enabled() -> bool:
    return os.getenv("OPENALGO_PAPER_MODE", "true").strip().lower() in ("1", "true", "yes")


def _openalgo_analyzer_status(host: str, api_key: str) -> bool:
    try:
        response = requests.post(
            f"{host}/api/v1/analyzer",
            json={"apikey": api_key},
            timeout=15,
        )
        body = response.json() if response.content else {}
    except requests.RequestException as exc:
        logger.warning("OpenAlgo analyzer status failed: %s", exc)
        return False
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return bool(data.get("analyze_mode"))


def _ensure_openalgo_analyzer_mode(host: str, api_key: str) -> bool:
    """Enable OpenAlgo analyzer (paper) mode when OPENALGO_PAPER_MODE is on."""
    if _openalgo_analyzer_status(host, api_key):
        return True
    try:
        response = requests.post(
            f"{host}/api/v1/analyzer/toggle",
            json={"apikey": api_key, "mode": True},
            timeout=15,
        )
        body = response.json() if response.content else {}
    except requests.RequestException as exc:
        logger.warning("OpenAlgo analyzer toggle failed: %s", exc)
        return False
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return bool(data.get("analyze_mode", True))


def _assert_execution_allowed(analyze: bool) -> None:
    """Block live basket execution from Vibe when OPENALGO_PAPER_MODE safety lock is on."""
    if not analyze and _paper_mode_env_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Live execution is disabled (OPENALGO_PAPER_MODE=true). "
                "Switch OpenAlgo to Analyze mode, or set OPENALGO_PAPER_MODE=false in .env "
                "after you are ready for real orders."
            ),
        )


@trade_router.get("/execution-mode", response_model=ExecutionModeResponse)
def execution_mode(
    _auth: None = Depends(require_local_or_auth),
) -> ExecutionModeResponse:
    """Return OpenAlgo paper/live mode (toggle lives in OpenAlgo UI only)."""
    paper_env = _paper_mode_env_enabled()
    try:
        host, api_key = _openalgo_config()
        analyze = _openalgo_analyzer_status(host, api_key)
    except HTTPException:
        analyze = paper_env
    return _resolve_execution_mode(analyze, paper_env)


@trade_router.post("/charges", response_model=TradeChargesResponse)
def trade_charges(
    body: TradeChargesRequest,
    _auth: None = Depends(require_local_or_auth),
) -> TradeChargesResponse:
    """Compute per-leg and round-trip charges for adjusted strategy legs."""
    legs = list(body.legs or [])
    if not legs:
        raise HTTPException(status_code=400, detail="legs array required")
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.options_research.payoff_charges import (
            calculate_charges,
            calculate_charges_with_exit,
        )

        spot = float(body.spot or 0.0)
        if body.include_exit and spot > 0:
            charges = calculate_charges_with_exit(
                legs,
                spot=spot,
                broker_preset=body.broker_preset,
            )
        else:
            charges = calculate_charges(legs, broker_preset=body.broker_preset)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("trade charges failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return TradeChargesResponse(status="success", charges=charges)


@trade_router.post("/execute-basket", response_model=ExecuteBasketResponse)
def execute_basket(
    body: ExecuteBasketRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ExecuteBasketResponse:
    """Place a multi-leg basket order via OpenAlgo REST (after user confirms in widget)."""
    orders = list(body.orders or [])
    widget: dict | None = None
    if not orders and body.widget_id:
        widget = load_trade_widget(body.widget_id)
        if not widget:
            raise HTTPException(status_code=404, detail="Widget not found")
        agent_id = str(widget.get("autonomous_agent_id") or widget.get("agent_id") or "").strip()
        if agent_id:
            try:
                from trade_integrations.execution.enforce import is_bridge_autonomous_agent

                if is_bridge_autonomous_agent(agent_id):
                    raise HTTPException(
                        status_code=403,
                        detail="Direct execute-basket blocked for autonomous agents — use plan approval flow",
                    )
            except HTTPException:
                raise
            except ImportError:
                pass
        for step in widget.get("implementation_steps") or []:
            if step.get("action") == "execute_basket" and step.get("payload"):
                orders = (step["payload"] or {}).get("orders") or []
                break
    if not orders:
        raise HTTPException(status_code=400, detail="No orders to execute")

    host, api_key = _openalgo_config()
    if _paper_mode_env_enabled():
        _ensure_openalgo_analyzer_mode(host, api_key)
    analyze = _openalgo_analyzer_status(host, api_key)
    _assert_execution_allowed(analyze)
    execution_mode = "paper" if analyze else "live"

    payload = {"apikey": api_key, "strategy": body.strategy, "orders": orders}
    try:
        response = requests.post(
            f"{host}/api/v1/basketorder",
            json=payload,
            timeout=45,
        )
        body_json = response.json() if response.content else {}
    except requests.RequestException as exc:
        logger.warning("OpenAlgo basket order failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"OpenAlgo request failed: {exc}") from exc

    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=body_json.get("message") or str(body_json),
        )

    results = body_json.get("results") or body_json.get("data") or []
    if isinstance(results, dict):
        results = [results]
    if not isinstance(results, list):
        results = []

    if body.widget_id:
        widget = load_trade_widget(body.widget_id)
        if widget:
            try:
                from src.trade.hub_bridge import ensure_trade_stack_path

                ensure_trade_stack_path()
                from trade_integrations.monitor.execution_ledger import record_execution_from_widget

                record_execution_from_widget(
                    widget,
                    results,
                    execution_mode=execution_mode,
                )
            except Exception:
                logger.warning(
                    "Failed to record execution ledger for widget %s",
                    body.widget_id,
                    exc_info=True,
                )

    mode_label = "Paper" if execution_mode == "paper" else "Live"
    return ExecuteBasketResponse(
        status=str(body_json.get("status") or "success"),
        results=results,
        message=str(body_json.get("message") or f"Basket submitted ({mode_label})"),
        execution_mode=execution_mode,
    )


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
    from trade_integrations.dataflows.news_hub_bridge import query_verified_news

    since_day = since.astimezone(timezone.utc).strftime("%Y-%m-%d")
    records = query_verified_news(ticker=ticker, since=since_day, limit=200)
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
        from trade_integrations.dataflows.company_research.india_symbols import india_index_tickers
        from trade_integrations.monitor.news_watcher import count_material_headlines_since
        from trade_integrations.monitor.service import MonitorService

        key = ticker.strip().upper()
        since = MonitorService._news_since(key)
        if key in india_index_tickers():
            since = _index_news_since(key, fallback=since)
            hub_count = _count_verified_headlines_since(key, since)
            if hub_count:
                return hub_count
        return count_material_headlines_since(key, since)
    except Exception:
        logger.exception("Material news count failed for %s", ticker)
        return 0


def _index_news_since(ticker: str, *, fallback: datetime) -> datetime:
    """Prefer index research as_of for index prediction live monitor."""
    try:
        from trade_integrations.context.hub import load_index_research_json

        doc = load_index_research_json(ticker)
    except Exception:
        return fallback
    if doc is None:
        return fallback
    as_of = getattr(doc, "as_of", None)
    if as_of is None and isinstance(doc, dict):
        as_of = doc.get("as_of")
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            return as_of.replace(tzinfo=timezone.utc)
        return as_of
    if isinstance(as_of, str):
        text = as_of.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return fallback


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
    key = (ticker or "").strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")

    try:
        from trade_integrations.monitor.service import MonitorService
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not MonitorService.is_enabled():
        return {"monitor_enabled": False}

    try:
        report = MonitorService().evaluate_ticker(key)
    except Exception as exc:
        logger.exception("plan-context failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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


class HubPlanResponse(BaseModel):
    status: str
    ticker: str = ""
    asset_type: str = "options"
    artifact: Dict[str, Any] | None = None
    message: str = ""


class AgentDebateResponse(BaseModel):
    status: str
    ticker: str = ""
    running: bool = False
    debate: Dict[str, Any] | None = None
    message: str = ""


class RunDebateRequest(BaseModel):
    ticker: str
    asset_type: str = "options"
    session_id: str | None = None
    refresh: bool = False


@trade_router.get("/hub-plan", response_model=HubPlanResponse)
def get_hub_plan(
    ticker: str,
    asset: str = "options",
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> HubPlanResponse:
    """Load structured trade plan from the shared hub for the research side panel."""
    key = (ticker or "").strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")
    asset_type = (asset or "options").strip().lower()
    try:
        from src.trade.hub_bridge import load_hub_plan_artifact, prefetch_hub_plan

        if refresh:
            artifact = prefetch_hub_plan(key, asset_type)
        else:
            artifact = load_hub_plan_artifact(key, asset_type)
            if artifact is None:
                artifact = prefetch_hub_plan(key, asset_type)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub-plan failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if artifact is None:
        return HubPlanResponse(status="not_found", ticker=key, asset_type=asset_type, message="No hub plan")
    return HubPlanResponse(status="ok", ticker=key, asset_type=artifact.get("asset_type", asset_type), artifact=artifact)


class IndexPredictionResponse(BaseModel):
    status: str
    ticker: str = ""
    artifact: Dict[str, Any] | None = None
    message: str = ""


class RunIndexPredictionRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = None
    refresh_constituents: bool = False


class IndexPredictionRunStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    job_status: str
    reused: bool = False


class IndexPredictionRunJobSnapshot(BaseModel):
    job_id: str
    status: str
    ticker: str = ""
    horizon_days: int | None = None
    refresh_constituents: bool = False
    created_at: str | None = None
    error: str | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    artifact: Dict[str, Any] | None = None


class IndexPredictionRunActiveResponse(BaseModel):
    status: str = "ok"
    job: IndexPredictionRunJobSnapshot | None = None


class IndexPredictionRunJobResponse(BaseModel):
    status: str = "ok"
    job: IndexPredictionRunJobSnapshot | None = None


class RefreshIndexPredictionRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = None
    force: bool = False


class IndexPredictionRefreshResponse(BaseModel):
    status: str
    ticker: str = ""
    reason: str = ""
    artifact: Dict[str, Any] | None = None
    message: str = ""


class IndexPredictionHistoryResponse(BaseModel):
    status: str
    ticker: str = ""
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    daily: List[Dict[str, Any]] = Field(default_factory=list)
    intraday: List[Dict[str, Any]] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class IndexFactorHistoryResponse(BaseModel):
    status: str
    ticker: str = ""
    series: List[Dict[str, Any]] = Field(default_factory=list)
    factors: List[str] = Field(default_factory=list)
    coverage: Dict[str, int] = Field(default_factory=dict)
    coverage_notes: List[str] = Field(default_factory=list)
    message: str = ""


class ConstituentHistoryResponse(BaseModel):
    status: str
    symbol: str = ""
    days: int = 90
    snapshot_count: int = 0
    has_research_archive: bool = False
    points: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class IndexPredictionSnapshotsResponse(BaseModel):
    status: str
    ticker: str = ""
    snapshots: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class IndexBacktestResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexForecastLabResponse(BaseModel):
    status: str
    ticker: str = ""
    result: Dict[str, Any] | None = None
    message: str = ""


class IndexTrackScoreboardResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexExecutionBacktestResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexMissAnalysisResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexDataAuditResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexCounterfactualResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class IndexPredictionJobsResponse(BaseModel):
    status: str
    env: Dict[str, Any] = Field(default_factory=dict)
    master_scheduler_running: bool = False
    jobs: List[Dict[str, Any]] = Field(default_factory=list)
    job: Dict[str, Any] | None = None
    message: str = ""


class DayAttributionResponse(BaseModel):
    status: str
    date: str = ""
    attribution: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class IndexFactorCatalogResponse(BaseModel):
    status: str
    macro_and_technical: List[Dict[str, Any]] = Field(default_factory=list)
    bottom_up: List[Dict[str, Any]] = Field(default_factory=list)
    constituent_research: List[Dict[str, Any]] = Field(default_factory=list)
    constituent_market_data: List[Dict[str, Any]] = Field(default_factory=list)
    news_and_sentiment: List[Dict[str, Any]] = Field(default_factory=list)
    derivatives: List[Dict[str, Any]] = Field(default_factory=list)
    pipeline_modules: List[Dict[str, Any]] = Field(default_factory=list)
    model_layers: List[Dict[str, Any]] = Field(default_factory=list)
    total_macro_keys: int = 0
    message: str = ""


class CaptureRegistryEntityPatch(BaseModel):
    capture_enabled: bool | None = None
    factor_groups: List[str] | None = None
    retention_days: Dict[str, int] | None = None
    schedules: Dict[str, str] | None = None


class CaptureRegistryUpdateRequest(BaseModel):
    entity_id: str = "NIFTY"
    patch: CaptureRegistryEntityPatch


class CaptureRegistryBackfillRequest(BaseModel):
    entity_id: str = "NIFTY"
    days: int = 365


class CaptureRegistryResponse(BaseModel):
    status: str
    registry: Dict[str, Any] = Field(default_factory=dict)
    factor_tree: List[Dict[str, Any]] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubStatusResponse(BaseModel):
    status: str = "ok"
    hub: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubStagingDrainResponse(BaseModel):
    status: str = "ok"
    summary: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubNewsPipelineConfigResponse(BaseModel):
    status: str = "ok"
    config: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubNewsPipelineConfigUpdate(BaseModel):
    full_ingest_cron: str | None = None
    light_ingest_cron: str | None = None
    light_ingest_enabled: bool | None = None
    entity_drain_cron: str | None = None
    full_ingest_sources: str | None = None
    light_ingest_sources: str | None = None
    full_lookback_days: int | None = None
    light_lookback_days: int | None = None
    entity_batch_size: int | None = None
    cluster_threshold: float | None = None
    relevance_gate_enabled: bool | None = None
    relevance_min_confidence: float | None = None
    relevance_rule_first: bool | None = None
    discard_retention_days: int | None = None


class HubNewsDiscardRequest(BaseModel):
    entity_id: str = "NIFTY"
    item_id: str = ""
    source_kind: str = "staging"
    reason: str | None = None
    discard_similar: bool = False


class HubNewsDiscardUndoRequest(BaseModel):
    entity_id: str = "NIFTY"
    discard_id: str = ""


class HubNewsDiscardResponse(BaseModel):
    status: str = "ok"
    discarded_count: int = 0
    discard_ids: list[str] = Field(default_factory=list)
    discarded: list[Dict[str, Any]] = Field(default_factory=list)
    similar_preview: Dict[str, Any] | None = None
    message: str = ""


class HubNewsDiscardedListResponse(BaseModel):
    status: str = "ok"
    items: list[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    message: str = ""


class HubNewsIngestRequest(BaseModel):
    mode: str = "full"
    ticker: str = "NIFTY"
    sources: str | None = None
    lookback_days: int | None = None


class SimulateIndexPredictionRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = None
    factor_overrides: Dict[str, float] = Field(default_factory=dict)
    primary_factor: str | None = None
    primary_shock_pct: float | None = None
    cascade: bool = True
    event_preset_id: str | None = None
    force_heuristic_cascade: bool = False


class SimulateIndexPredictionResponse(BaseModel):
    status: str
    ticker: str = ""
    simulation: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class IndexPlaygroundContextResponse(BaseModel):
    status: str
    ticker: str = ""
    context: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class IndexNewsImpactResponse(BaseModel):
    status: str
    ticker: str = ""
    report: Dict[str, Any] | None = None
    message: str = ""


class NewsScenarioSessionRequest(BaseModel):
    ticker: str = "NIFTY"
    pipeline_as_of: str
    horizon_days: int | None = 14
    session_id: str | None = None


class NewsScenarioSessionPatchRequest(BaseModel):
    date_range: Dict[str, Any] | None = None
    selected_outcome_id: str | None = None
    active_draft_id: str | None = None
    active_scenario_id: str | None = None


class NewsScenarioSessionResponse(BaseModel):
    status: str = "ok"
    session_id: str = ""
    pipeline_as_of: str = ""
    ticker: str = "NIFTY"
    message: str = ""


class NewsEventScenarioResponse(BaseModel):
    status: str = "ok"
    ticker: str = "NIFTY"
    scenario: Dict[str, Any] | None = None
    scenarios: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class IndexVerifiedNewsResponse(BaseModel):
    status: str
    ticker: str = ""
    count: int = 0
    items: list[Dict[str, Any]] = Field(default_factory=list)
    inventory: Dict[str, Any] | None = None
    message: str = ""


class IndexQuantReviewResponse(BaseModel):
    status: str
    ticker: str = ""
    review: Dict[str, Any] | None = None
    message: str = ""


class RunIndexQuantReviewRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = 14
    refresh: bool = False


@trade_router.get("/index-prediction", response_model=IndexPredictionResponse)
def get_index_prediction(
    ticker: str = "NIFTY",
    horizon_days: int | None = None,
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionResponse:
    """Load cached index research artifact for the prediction page."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from src.trade.hub_bridge import load_hub_plan_artifact, prefetch_index_hub_plan

        if refresh:
            artifact = prefetch_index_hub_plan(key)
        else:
            artifact = load_hub_plan_artifact(key, "index")
            if artifact is None:
                artifact = prefetch_index_hub_plan(key)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction GET failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if artifact is None:
        return IndexPredictionResponse(status="not_found", ticker=key, message="No index research")
    if horizon_days is not None and artifact.get("horizon", {}).get("days") != horizon_days:
        artifact["_horizon_mismatch"] = True
    return IndexPredictionResponse(status="ok", ticker=key, artifact=artifact)


@trade_router.post("/index-prediction/run", response_model=IndexPredictionResponse)
def run_index_prediction(
    body: RunIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionResponse:
    """Run full index research pipeline and persist to hub."""
    key = (body.ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.context.hub import save_index_research
        from trade_integrations.dataflows.index_research.aggregator import run_index_research
        from src.trade.hub_bridge import _index_doc_to_panel, ensure_trade_stack_path

        ensure_trade_stack_path()
        doc = run_index_research(
            key,
            horizon_days=body.horizon_days,
            refresh_constituents=body.refresh_constituents,
        )
        save_index_research(doc)
        try:
            from trade_integrations.dataflows.index_research.prediction_algorithms.config import (
                lab_enabled,
                scoreboard_auto_refresh,
            )
            from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
                load_scoreboard,
                scoreboard_needs_promotion_history,
                scoreboard_needs_refresh,
            )
            from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.walk_forward import (
                run_track_walk_forward,
            )

            if lab_enabled() and scoreboard_auto_refresh():
                cached = load_scoreboard(key)
                if (
                    not cached
                    or scoreboard_needs_refresh(cached, horizon_days=body.horizon_days, history_days=365)
                    or scoreboard_needs_promotion_history(cached)
                ):
                    run_track_walk_forward(
                        ticker=key,
                        days=365,
                        horizon_days=body.horizon_days,
                    )
        except Exception as exc:
            logger.debug("track scoreboard auto-refresh skipped for %s: %s", key, exc)
        artifact = _index_doc_to_panel(doc)
        artifact["asset_type"] = "index"
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction run failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexPredictionResponse(status="ok", ticker=key, artifact=artifact)


@trade_router.get("/index-prediction/factors", response_model=IndexFactorCatalogResponse)
def get_index_prediction_factors(
    _auth: None = Depends(require_local_or_auth),
) -> IndexFactorCatalogResponse:
    """Return catalog of macro, technical, bottom-up, and model factors."""
    try:
        from trade_integrations.dataflows.index_research.factor_catalog import list_factor_catalog

        payload = list_factor_catalog()
        return IndexFactorCatalogResponse(status="ok", **payload)
    except Exception as exc:
        logger.exception("index-prediction factors catalog failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/capture-registry", response_model=CaptureRegistryResponse)
def get_capture_registry(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> CaptureRegistryResponse:
    """Return hub capture registry, factor tiers, and storage stats."""
    key = entity_id.strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.registry import (
            build_capture_stats,
            build_factor_tree,
            load_registry,
        )
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        reg = load_registry(create=True)
        return CaptureRegistryResponse(
            status="ok",
            registry=reg,
            factor_tree=build_factor_tree(),
            stats=build_capture_stats(key),
            coverage=capture_coverage_stats(entity_id=key),
        )
    except Exception as exc:
        logger.exception("capture-registry get failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.put("/capture-registry", response_model=CaptureRegistryResponse)
def update_capture_registry(
    body: CaptureRegistryUpdateRequest,
    _auth: None = Depends(require_local_or_auth),
) -> CaptureRegistryResponse:
    """Update capture settings for an entity (v1: NIFTY)."""
    key = body.entity_id.strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.registry import (
            build_capture_stats,
            build_factor_tree,
            load_registry,
            update_entity,
        )
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        patch = body.patch.model_dump(exclude_none=True)
        update_entity(key, patch)
        reg = load_registry(create=False)
        return CaptureRegistryResponse(
            status="ok",
            registry=reg,
            factor_tree=build_factor_tree(),
            stats=build_capture_stats(key),
            coverage=capture_coverage_stats(entity_id=key),
        )
    except Exception as exc:
        logger.exception("capture-registry update failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/capture-registry/backfill")
def run_capture_registry_backfill(
    body: CaptureRegistryBackfillRequest,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Backfill proprietary NIFTY factor history (participant OI, flows)."""
    key = body.entity_id.strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.intraday import run_capture_backfill

        return run_capture_backfill(entity_id=key, days=max(30, min(body.days, 730)))
    except Exception as exc:
        logger.exception("capture-registry backfill failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/capture-registry/intraday")
def run_capture_registry_intraday(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Run one intraday chain capture now (OpenAlgo → hub)."""
    key = entity_id.strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.intraday import run_intraday_capture

        return run_intraday_capture(entity_id=key)
    except Exception as exc:
        logger.exception("capture-registry intraday failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/hub/status", response_model=HubStatusResponse)
def get_hub_status(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> HubStatusResponse:
    """Return hub inventory: staging queue, verified news, cache health, capture stats."""
    key = entity_id.strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_storage.hub_status import build_hub_status

        return HubStatusResponse(status="ok", hub=build_hub_status(entity_id=key))
    except Exception as exc:
        logger.exception("hub status failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/hub/staging/drain", response_model=HubStagingDrainResponse)
def drain_hub_staging(
    entity_id: str = "NIFTY",
    limit: int = 20,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Manually process a batch of queued staging news refs."""
    key = entity_id.strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import process_staging_batch

        summary = process_staging_batch(ticker=key, limit=max(1, min(limit, 100)))
        if summary.get("pipeline_paused") or summary.get("paused"):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(summary.get("pause_reason") or "News distillation pipeline is paused."),
            )
        return HubStagingDrainResponse(status="ok", summary=summary)
    except Exception as exc:
        logger.exception("hub staging drain failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/hub/news-pipeline/config", response_model=HubNewsPipelineConfigResponse)
def get_hub_news_pipeline_config(
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineConfigResponse:
    """Return hub news ingest/distill schedule (env defaults + hub override file)."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_storage.news_pipeline_config import config_for_api

        return HubNewsPipelineConfigResponse(status="ok", config=config_for_api())
    except Exception as exc:
        logger.exception("hub news pipeline config read failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.patch("/hub/news-pipeline/config", response_model=HubNewsPipelineConfigResponse)
def patch_hub_news_pipeline_config(
    body: HubNewsPipelineConfigUpdate,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineConfigResponse:
    """Update persisted pipeline config and sync scheduled job crons."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_storage.news_pipeline_config import (
            config_for_api,
            sync_scheduled_jobs_from_config,
            update_news_pipeline_config,
        )

        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        update_news_pipeline_config(patch)
        sync_result = sync_scheduled_jobs_from_config()
        payload = config_for_api()
        payload["scheduler_sync"] = sync_result
        return HubNewsPipelineConfigResponse(status="ok", config=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub news pipeline config update failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/hub/news-pipeline/ingest", response_model=HubStagingDrainResponse)
def run_hub_news_ingest_now(
    body: HubNewsIngestRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Trigger ingest immediately (full or light mode)."""
    key = (body.ticker or "NIFTY").strip().upper()
    mode = (body.mode or "full").strip().lower()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import run_hub_news_ingest

        summary = run_hub_news_ingest(
            ticker=key,
            mode=mode,
            sources=body.sources or "default",
            lookback_days=body.lookback_days,
        )
        return HubStagingDrainResponse(status="ok", summary=summary)
    except Exception as exc:
        logger.exception("hub news ingest now failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/hub/news/discard", response_model=HubNewsDiscardResponse)
def discard_hub_news(
    body: HubNewsDiscardRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardResponse:
    """Discard one news item or discard similar cluster."""
    key = (body.entity_id or "NIFTY").strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.index_research.news_discard import (
            discard_news_item,
            discard_similar_items,
            preview_discard_similar,
        )
        from trade_integrations.dataflows.news_hub_bridge import (
            get_distilled_event,
            list_pending_staging_refs,
        )

        reason = str(body.reason or "manual discard")
        if body.discard_similar:
            anchor: dict[str, Any] = {}
            iid = str(body.item_id or "").strip()
            if body.source_kind == "staging" or iid.startswith("ref:"):
                for ref in list_pending_staging_refs(ticker=key, limit=10_000):
                    if str(ref.get("ref_id") or "") == iid:
                        anchor = {**ref, "provenance": "staging"}
                        break
            else:
                ev = get_distilled_event(iid)
                if ev:
                    anchor = {**ev, "provenance": "distilled_event"}
            if not anchor:
                raise HTTPException(status_code=404, detail=f"item not found: {iid}")
            preview = preview_discard_similar(anchor, ticker=key)
            result = discard_similar_items(anchor, ticker=key, reason=reason)
            return HubNewsDiscardResponse(
                status="ok",
                discarded_count=int(result.get("discarded_count") or 0),
                discard_ids=list(result.get("discard_ids") or []),
                discarded=list(result.get("discarded") or []),
                similar_preview=preview,
            )

        result = discard_news_item(
            str(body.item_id or ""),
            ticker=key,
            source_kind=str(body.source_kind or "staging"),
            reason=reason,
        )
        rows = list(result.get("discarded") or [])
        return HubNewsDiscardResponse(
            status="ok",
            discarded_count=int(result.get("count") or len(rows)),
            discard_ids=[str(r.get("discard_id") or "") for r in rows if r.get("discard_id")],
            discarded=rows,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub news discard failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/hub/news/discard/undo", response_model=HubNewsDiscardResponse)
def undo_hub_news_discard(
    body: HubNewsDiscardUndoRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardResponse:
    """Restore a soft-discarded news item within retention window."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.index_research.news_discard import undo_discard

        result = undo_discard(str(body.discard_id or "").strip())
        if not result.get("restored"):
            return HubNewsDiscardResponse(
                status="failed",
                message=str(result.get("reason") or "restore failed"),
            )
        return HubNewsDiscardResponse(status="ok", message="restored", discarded=[result])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub news discard undo failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/hub/news/discarded", response_model=HubNewsDiscardedListResponse)
def list_hub_discarded_news(
    entity_id: str = "NIFTY",
    limit: int = 50,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardedListResponse:
    """List soft-discarded news items (30d retention)."""
    key = entity_id.strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.index_research.news_discard import list_discarded

        items = list_discarded(ticker=key, limit=max(1, min(limit, 200)))
        return HubNewsDiscardedListResponse(status="ok", items=items, count=len(items))
    except Exception as exc:
        logger.exception("hub discarded news list failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/index-prediction/simulate", response_model=SimulateIndexPredictionResponse)
def simulate_index_prediction(
    body: SimulateIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> SimulateIndexPredictionResponse:
    """What-if: adjust macro factors and recompute index forecast."""
    key = (body.ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.cascade.calibration_store import (
            load_calibration_from_doc,
        )
        from trade_integrations.dataflows.index_research.cascade.types import CascadeCalibration
        from trade_integrations.dataflows.index_research.simulate import (
            macro_factors_from_rows,
            simulate_index_prediction as run_simulate,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        doc = load_index_research_json(key)
        if doc is None or not doc.spot:
            return SimulateIndexPredictionResponse(
                status="not_found",
                ticker=key,
                message="Run index analysis first",
            )

        macro = macro_factors_from_rows(doc.global_factors or [])
        pred = doc.prediction or {}
        bottom_up = float(pred.get("bottom_up_return_pct") or 0.0)
        headline = float(pred.get("expected_return_pct") or 0.0)
        horizon_days = body.horizon_days or (doc.horizon or {}).get("days")
        calibration = load_calibration_from_doc(doc)
        india_vix = macro.get("india_vix")
        if india_vix is None and isinstance(doc.regime, dict):
            india_vix = doc.regime.get("india_vix")

        simulation = run_simulate(
            macro_factors=macro,
            factor_overrides=body.factor_overrides,
            spot=float(doc.spot),
            bottom_up_return_pct=bottom_up,
            horizon_days=horizon_days,
            headline_return_pct=headline,
            primary_factor=body.primary_factor,
            primary_shock_pct=body.primary_shock_pct,
            cascade=body.cascade,
            event_preset_id=body.event_preset_id,
            event_impact_curves=doc.event_impact_curves or [],
            cascade_calibration=calibration,
            india_vix=float(india_vix) if india_vix is not None else None,
            force_heuristic_cascade=bool(body.force_heuristic_cascade),
        )
        if simulation.get("error"):
            return SimulateIndexPredictionResponse(
                status="error",
                ticker=key,
                message=str(simulation["error"]),
            )
        return SimulateIndexPredictionResponse(status="ok", ticker=key, simulation=simulation)
    except Exception as exc:
        logger.exception("index-prediction simulate failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/playground-context", response_model=IndexPlaygroundContextResponse)
def get_index_playground_context(
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> IndexPlaygroundContextResponse:
    """Headlines, events, and ranked factors for the factor impact workbench."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.playground_context import (
            build_playground_context,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        doc = load_index_research_json(key)
        if doc is None:
            return IndexPlaygroundContextResponse(
                status="not_found",
                ticker=key,
                message="Run index analysis first",
            )
        ctx = build_playground_context(doc, ticker=key)
        return IndexPlaygroundContextResponse(status="ok", ticker=key, context=ctx)
    except Exception as exc:
        logger.exception("index-prediction playground-context failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/backtest", response_model=IndexBacktestResponse)
def get_index_prediction_backtest(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 180,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexBacktestResponse:
    """Load cached walk-forward backtest or recompute from factor history."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.backtest_runner import (
            load_backtest_report,
            run_and_save_backtest,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if refresh:
            report = run_and_save_backtest(
                days=days,
                horizon_days=horizon_days,
            )
        else:
            report = load_backtest_report(key)
            if report is None:
                report = run_and_save_backtest(
                    days=days,
                    horizon_days=horizon_days,
                )
        status = str(report.get("status") or "ok")
        return IndexBacktestResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction backtest failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/forecast-lab", response_model=IndexForecastLabResponse)
@trade_router.post("/index-prediction/forecast-lab", response_model=IndexForecastLabResponse)
def index_prediction_forecast_lab(
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    mode: str = "tracks_only",
    combiner_id: str | None = None,
    use_hub_cache: bool = True,
    body: Dict[str, Any] | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexForecastLabResponse:
    """Plug-and-play forecast lab — independent tracks + optional combiner."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.prediction_algorithms.api import run_forecast_lab
        from trade_integrations.dataflows.index_research.prediction_algorithms.config import (
            default_combiner_id,
            lab_enabled,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
            build_track_context,
            context_from_hub,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import (
            resolve_active_combiner,
            resolve_combiner_runtime_kwargs,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if not lab_enabled():
            return IndexForecastLabResponse(
                status="disabled",
                ticker=key,
                message="INDEX_PREDICTION_LAB_ENABLED=0",
            )

        payload = body or {}
        hz = int(payload.get("horizon_days") or horizon_days)
        run_mode = str(payload.get("mode") or mode or "tracks_only")
        combiner = payload.get("combiner_id") or combiner_id
        use_cache = payload.get("use_hub_cache", use_hub_cache)

        ctx = None
        if use_cache:
            ctx = context_from_hub(key, horizon_days=hz)
        if ctx is None:
            ctx = build_track_context(ticker=key, spot=0.0, horizon_days=hz)
            return IndexForecastLabResponse(
                status="error",
                ticker=key,
                message="hub_cache_unavailable",
            )

        lab_mode_val = "combine" if run_mode == "combine" else "tracks_only"
        active = None
        runtime_kwargs: dict[str, Any] = {}
        if lab_mode_val == "combine":
            active = combiner or resolve_active_combiner(default=default_combiner_id(), ticker=key)
            if active:
                runtime_kwargs = resolve_combiner_runtime_kwargs(
                    str(active),
                    ticker=key,
                    as_of_day=getattr(ctx, "as_of_day", None),
                )
        result = run_forecast_lab(
            ctx,
            mode=lab_mode_val,
            combiner_id=combiner or active,
            mae_by_track=runtime_kwargs.get("mae_by_track"),
            lam=runtime_kwargs.get("lam"),
        )
        return IndexForecastLabResponse(status="ok", ticker=key, result=result.to_dict())
    except Exception as exc:
        logger.exception("index-prediction forecast-lab failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/track-scoreboard", response_model=IndexTrackScoreboardResponse)
def get_index_track_scoreboard(
    ticker: str = "NIFTY",
    refresh: bool = False,
    cache_only: bool = False,
    days: int = 365,
    horizon_days: int | None = None,
    eval_step: int = 5,
    _auth: None = Depends(require_local_or_auth),
) -> IndexTrackScoreboardResponse:
    """Load cached per-track scoreboard or recompute walk-forward."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
            load_scoreboard,
            normalize_scoreboard_report,
            scoreboard_needs_refresh,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.walk_forward import (
            run_track_walk_forward,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import (
            enrich_scoreboard_with_live,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        history_days = max(days, 730)
        if cache_only:
            report = load_scoreboard(key)
            report = normalize_scoreboard_report(report or {"status": "empty", "ticker": key})
            report["needs_refresh"] = scoreboard_needs_refresh(
                report,
                horizon_days=horizon_days,
                history_days=history_days,
            )
        elif refresh:
            report = run_track_walk_forward(
                ticker=key,
                days=days,
                horizon_days=horizon_days,
                eval_step=eval_step,
            )
            report["needs_refresh"] = False
        else:
            report = load_scoreboard(key)
            if scoreboard_needs_refresh(
                report,
                horizon_days=horizon_days,
                history_days=history_days,
            ):
                report = run_track_walk_forward(
                    ticker=key,
                    days=history_days,
                    horizon_days=horizon_days,
                    eval_step=eval_step,
                )
            report = normalize_scoreboard_report(report or {})
            report["needs_refresh"] = False
        report = normalize_scoreboard_report(report or {})
        report = enrich_scoreboard_with_live(report, ticker=key)
        status = str(report.get("status") or "ok")
        return IndexTrackScoreboardResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction track-scoreboard failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/execution-backtest", response_model=IndexExecutionBacktestResponse)
def get_index_execution_backtest(
    ticker: str = "NIFTY",
    track: str = "quant_ridge",
    strategy: str = "futures_trend",
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexExecutionBacktestResponse:
    """Load or compute execution simulation from track scoreboard."""
    from trade_integrations.dataflows.index_research.prediction_algorithms.config import exec_sim_enabled

    if not exec_sim_enabled():
        return IndexExecutionBacktestResponse(
            status="disabled",
            ticker=(ticker or "NIFTY").strip().upper(),
            message="Set INDEX_PREDICTION_EXEC_SIM_ENABLED=1",
        )
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.execution_sim.runner import (
            execution_backtest_path,
            run_execution_backtest,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        path = execution_backtest_path(key)
        if refresh or not path.is_file():
            report = run_execution_backtest(
                ticker=key,
                track_id=(track or "quant_ridge").strip(),
                strategy=(strategy or "futures_trend").strip(),
                persist=True,
            )
        else:
            import json

            report = json.loads(path.read_text(encoding="utf-8"))
        status = str(report.get("status") or "ok")
        return IndexExecutionBacktestResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction execution-backtest failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/miss-analysis", response_model=IndexMissAnalysisResponse)
def get_index_prediction_miss_analysis(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexMissAnalysisResponse:
    """Load cached prediction miss RCA or recompute from backtest eval rows."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            load_miss_analysis_report,
            run_and_save_miss_analysis,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if refresh:
            report = run_and_save_miss_analysis(
                days=days,
                horizon_days=horizon_days,
                ticker=key,
            )
        else:
            report = load_miss_analysis_report(key)
            if report is None:
                report = run_and_save_miss_analysis(
                    days=days,
                    horizon_days=horizon_days,
                    ticker=key,
                )
        status = str(report.get("status") or "ok")
        return IndexMissAnalysisResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction miss-analysis failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/index-prediction/miss-analysis/run", response_model=IndexMissAnalysisResponse)
def run_index_prediction_miss_analysis(
    ticker: str = "NIFTY",
    days: int = 365,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexMissAnalysisResponse:
    """Recompute prediction miss RCA from walk-forward backtest."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.backtest_runner import run_and_save_backtest
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            run_and_save_miss_analysis,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        backtest = run_and_save_backtest(days=days, horizon_days=horizon_days)
        report = run_and_save_miss_analysis(
            days=days,
            horizon_days=horizon_days,
            ticker=key,
            backtest_report=backtest,
        )
        status = str(report.get("status") or "ok")
        return IndexMissAnalysisResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction miss-analysis run failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/quant-review", response_model=IndexQuantReviewResponse)
def get_index_quant_review(
    ticker: str = "NIFTY",
    horizon_days: int | None = 14,
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexQuantReviewResponse:
    """Load cached India Quant Reviewer artifact (second opinion vs Ridge)."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.bridge.quant_review import run_quant_review
        from trade_integrations.context.hub import (
            is_quant_review_cache_fresh,
            load_quant_review_json,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if refresh or not is_quant_review_cache_fresh(key):
            review = run_quant_review(key, horizon_days=horizon_days, save=True)
        else:
            review = load_quant_review_json(key)
            if review is None:
                review = run_quant_review(key, horizon_days=horizon_days, save=True)
        return IndexQuantReviewResponse(status="ok", ticker=key, review=review)
    except Exception as exc:
        logger.exception("index-prediction quant-review failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/index-prediction/quant-review/run", response_model=IndexQuantReviewResponse)
def run_index_quant_review(
    body: RunIndexQuantReviewRequest,
    _auth: None = Depends(require_local_or_auth),
) -> IndexQuantReviewResponse:
    """Run India Quant Reviewer and persist to hub."""
    key = (body.ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.bridge.quant_review import run_quant_review
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        review = run_quant_review(
            key,
            horizon_days=body.horizon_days,
            save=True,
        )
        return IndexQuantReviewResponse(status="ok", ticker=key, review=review)
    except Exception as exc:
        logger.exception("index-prediction quant-review run failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/data-audit", response_model=IndexDataAuditResponse)
def get_index_prediction_data_audit(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> IndexDataAuditResponse:
    """Load hub data completeness audit for prediction RCA."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.hub_data_audit import (
            load_data_audit_report,
            run_and_save_data_audit,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if refresh:
            report = run_and_save_data_audit(
                days=days,
                horizon_days=horizon_days,
                ticker=key,
            )
        else:
            report = load_data_audit_report(key)
            if report is None:
                report = run_and_save_data_audit(
                    days=days,
                    horizon_days=horizon_days,
                    ticker=key,
                )
        status = str(report.get("status") or "ok")
        return IndexDataAuditResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction data-audit failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/counterfactual", response_model=IndexCounterfactualResponse)
def get_index_prediction_counterfactual(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> IndexCounterfactualResponse:
    """Load cached counterfactual decomposition or recompute from backtest eval rows."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.prediction_counterfactual import (
            load_counterfactual_report,
            run_and_save_counterfactual,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if refresh:
            report = run_and_save_counterfactual(
                days=days,
                horizon_days=horizon_days,
                ticker=key,
            )
        else:
            report = load_counterfactual_report(key)
            if report is None:
                report = run_and_save_counterfactual(
                    days=days,
                    horizon_days=horizon_days,
                    ticker=key,
                )
        status = str(report.get("status") or "ok")
        return IndexCounterfactualResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction counterfactual failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/news-impact", response_model=IndexNewsImpactResponse)
def get_index_prediction_news_impact(
    ticker: str = "NIFTY",
    refresh: bool = False,
    horizon_days: int = 14,
    include_rejected: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexNewsImpactResponse:
    """Verified news → Nifty impact snapshot from hub SSOT.

    Default load is hub-read-only (``resolve_news_impact``). ``refresh=true`` runs
    index-level ingest for NIFTY only (tiered sources allowed) — not Nifty-50 batch.
    """
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows import news_hub_bridge
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        spot: float | None = None
        macro: dict[str, float] = {}
        doc = load_index_research_json(key)
        if doc is not None:
            spot = float(getattr(doc, "spot", 0) or 0) or None
            for row in getattr(doc, "global_factors", None) or []:
                if row.get("factor") is not None and row.get("value") is not None:
                    macro[str(row["factor"])] = float(row["value"])

        if refresh:
            report = news_hub_bridge.refresh_news_impact(
                ticker=key,
                horizon_days=horizon_days,
                spot=spot,
                macro_factors=macro or None,
                refresh_ingest=True,
                include_rejected=include_rejected,
            )
        else:
            report = news_hub_bridge.resolve_news_impact(ticker=key, doc=doc, limit=12)
        status = str((report or {}).get("status") or "ok")
        return IndexNewsImpactResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction news-impact failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.post("/index-prediction/news-scenarios/session", response_model=NewsScenarioSessionResponse)
def create_news_scenario_session(
    body: NewsScenarioSessionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> NewsScenarioSessionResponse:
    """Create or resume a news-scenario advisor Vibe session bound to pipeline_as_of."""
    key = (body.ticker or "NIFTY").strip().upper()
    pipeline_as_of = (body.pipeline_as_of or "").strip()
    if not pipeline_as_of:
        raise HTTPException(status_code=400, detail="pipeline_as_of is required")
    try:
        from src.api.state import _get_session_service
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.pipeline_snapshot import (
            normalize_as_of,
            resolve_bound_pipeline_doc,
        )

        ensure_trade_stack_path()
        resolve_bound_pipeline_doc(key, pipeline_as_of)

        svc = _get_session_service()
        if svc is None:
            raise HTTPException(status_code=503, detail="session runtime not enabled")

        bound = normalize_as_of(pipeline_as_of)

        if body.session_id:
            existing = svc.get_session(body.session_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="session not found")
            if str((existing.config or {}).get("session_kind") or "") != "news_scenario_advisor":
                raise HTTPException(status_code=403, detail="not a news scenario session")
            existing_as_of = normalize_as_of((existing.config or {}).get("pipeline_as_of"))
            if existing_as_of == bound:
                return NewsScenarioSessionResponse(
                    session_id=existing.session_id,
                    pipeline_as_of=pipeline_as_of,
                    ticker=key,
                )
        else:
            for existing in svc.list_sessions(limit=200):
                cfg = existing.config or {}
                if str(cfg.get("session_kind") or "") != "news_scenario_advisor":
                    continue
                if str(cfg.get("pipeline_ticker") or "NIFTY").upper() != key:
                    continue
                if normalize_as_of(cfg.get("pipeline_as_of")) == bound:
                    return NewsScenarioSessionResponse(
                        session_id=existing.session_id,
                        pipeline_as_of=pipeline_as_of,
                        ticker=key,
                    )

        session = svc.create_session(
            title=f"news-scenario:{key}",
            config={
                "session_kind": "news_scenario_advisor",
                "pipeline_ticker": key,
                "pipeline_as_of": pipeline_as_of,
                "horizon_days": body.horizon_days or 14,
                "system_note": (
                    "News Predictions advisor — use pipeline tools only; "
                    "load_skill news-scenario-advisor on first turn."
                ),
            },
        )
        return NewsScenarioSessionResponse(
            session_id=session.session_id,
            pipeline_as_of=pipeline_as_of,
            ticker=key,
        )
    except HTTPException:
        raise
    except Exception as exc:
        from trade_integrations.dataflows.index_research.pipeline_snapshot import (
            MissingSnapshotError,
            StaleSnapshotError,
        )

        if isinstance(exc, (MissingSnapshotError, StaleSnapshotError)):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.exception("news-scenario session create failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.patch(
    "/index-prediction/news-scenarios/session/{session_id}",
    response_model=NewsScenarioSessionResponse,
)
def patch_news_scenario_session(
    session_id: str,
    body: NewsScenarioSessionPatchRequest,
    _auth: None = Depends(require_local_or_auth),
) -> NewsScenarioSessionResponse:
    """Update date_range / selection fields on a news-scenario session."""
    try:
        from src.api.state import _get_session_service

        svc = _get_session_service()
        if svc is None:
            raise HTTPException(status_code=503, detail="session runtime not enabled")
        session = svc.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        cfg = dict(session.config or {})
        if str(cfg.get("session_kind") or "") != "news_scenario_advisor":
            raise HTTPException(status_code=403, detail="not a news scenario session")
        if body.date_range is not None:
            from trade_integrations.dataflows.index_research.news_event_scenarios import (
                validate_scenario_date_range,
            )

            cfg["date_range"] = validate_scenario_date_range(body.date_range)
        if body.selected_outcome_id is not None:
            cfg["selected_outcome_id"] = body.selected_outcome_id
        if body.active_draft_id is not None:
            cfg["active_draft_id"] = body.active_draft_id
        if body.active_scenario_id is not None:
            cfg["active_scenario_id"] = body.active_scenario_id
        session.config = cfg
        svc.store.update_session(session)
        return NewsScenarioSessionResponse(
            session_id=session.session_id,
            pipeline_as_of=str(cfg.get("pipeline_as_of") or ""),
            ticker=str(cfg.get("pipeline_ticker") or "NIFTY"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        from trade_integrations.dataflows.index_research.news_event_scenarios import NewsScenarioError

        if isinstance(exc, NewsScenarioError):
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        logger.exception("news-scenario session patch failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/news-scenarios/recent", response_model=NewsEventScenarioResponse)
def list_news_scenarios(
    ticker: str = "NIFTY",
    limit: int = 10,
    _auth: None = Depends(require_local_or_auth),
) -> NewsEventScenarioResponse:
    key = (ticker or "NIFTY").strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.news_event_scenarios import (
            list_recent_news_scenarios,
        )

        ensure_trade_stack_path()
        rows = list_recent_news_scenarios(key, limit=limit)
        return NewsEventScenarioResponse(status="ok", ticker=key, scenarios=rows)
    except Exception as exc:
        logger.exception("list news scenarios failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get(
    "/index-prediction/news-scenarios/{scenario_id}",
    response_model=NewsEventScenarioResponse,
)
def get_news_scenario(
    scenario_id: str,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> NewsEventScenarioResponse:
    key = (ticker or "NIFTY").strip().upper()
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.index_research.news_event_scenarios import (
            load_news_event_scenario,
        )

        ensure_trade_stack_path()
        scenario = load_news_event_scenario(key, scenario_id)
        if scenario is None:
            return NewsEventScenarioResponse(
                status="not_found",
                ticker=key,
                message=f"Scenario {scenario_id} not found",
            )
        return NewsEventScenarioResponse(status="ok", ticker=key, scenario=scenario)
    except Exception as exc:
        logger.exception("get news scenario failed for %s", scenario_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/verified-news", response_model=IndexVerifiedNewsResponse)
def get_index_verified_news(
    ticker: str = "NIFTY",
    since: str | None = None,
    until: str | None = None,
    day: str | None = None,
    symbols: str | None = None,
    topics: str | None = None,
    factors: str | None = None,
    themes: str | None = None,
    tags: str | None = None,
    include_rejected: bool = False,
    inventory: bool = False,
    limit: int = 25,
    _auth: None = Depends(require_local_or_auth),
) -> IndexVerifiedNewsResponse:
    """Filter verified hub news by date, symbol, topic, factor, or theme tags."""
    key = (ticker or "NIFTY").strip().upper()

    def _csv(raw: str | None) -> list[str] | None:
        if not raw:
            return None
        return [part.strip() for part in raw.split(",") if part.strip()]

    try:
        from trade_integrations.dataflows import news_hub_bridge
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        inv = news_hub_bridge.tag_inventory(ticker=key) if inventory else None
        items = news_hub_bridge.query_verified_news(
            ticker=key,
            since=since,
            until=until,
            publish_day=day,
            symbols=_csv(symbols),
            topics=_csv(topics),
            factors=_csv(factors),
            themes=_csv(themes),
            tags=_csv(tags),
            include_rejected=include_rejected,
            limit=max(1, min(limit, 100)),
        )
        return IndexVerifiedNewsResponse(
            status="ok",
            ticker=key,
            count=len(items),
            items=items,
            inventory=inv,
        )
    except Exception as exc:
        logger.exception("index-prediction verified-news failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@trade_router.get("/index-prediction/day-attribution", response_model=DayAttributionResponse)
def get_index_day_attribution(
    date: str,
    days: int = 365,
    _auth: None = Depends(require_local_or_auth),
) -> DayAttributionResponse:
    """Explain factor and calendar drivers for one Nifty trading day."""
    try:
        from trade_integrations.dataflows.index_research.day_attribution import explain_nifty_day
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = explain_nifty_day(date, history_days=max(30, min(days, 365)))
        status = str(payload.get("status") or "ok")
        if status == "error":
            return DayAttributionResponse(status="error", message=str(payload.get("message") or "failed"))
        if status == "not_found":
            return DayAttributionResponse(
                status="not_found",
                date=str(payload.get("date") or date),
                message=str(payload.get("message") or "Date not found"),
            )
        return DayAttributionResponse(status="ok", date=str(payload.get("date") or date), attribution=payload)
    except Exception as exc:
        logger.exception("day-attribution failed for %s", date)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


_INDEX_PREDICTION_RUN_POLL_SECONDS = 0.5
_INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS = 15.0


def _index_prediction_run_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def _index_prediction_run_event_stream(job_id: str, request: Request):
    """Replay stored logs then poll job store until done/error."""
    import time as time_mod

    from src.trade.index_prediction_run_jobs import INDEX_PREDICTION_RUN_JOBS, _JOBS_LOCK

    last_log_idx = 0
    last_emit = time_mod.monotonic()
    while True:
        if await request.is_disconnected():
            return

        with _JOBS_LOCK:
            job = INDEX_PREDICTION_RUN_JOBS.get(job_id)
            if job is None:
                yield _index_prediction_run_sse_frame("error", {"message": "job not found"})
                return
            status = str(job.get("status") or "")
            logs = list(job.get("logs") or [])
            artifact = job.get("artifact")
            error = job.get("error")
            ticker = str(job.get("ticker") or "")

        while last_log_idx < len(logs):
            yield _index_prediction_run_sse_frame("log", {"entry": logs[last_log_idx]})
            last_log_idx += 1
            last_emit = time_mod.monotonic()

        if status == "done":
            if artifact is not None:
                yield _index_prediction_run_sse_frame(
                    "done",
                    {"ticker": ticker, "artifact": artifact},
                )
            return
        if status == "error":
            yield _index_prediction_run_sse_frame("error", {"message": error or "unknown error"})
            return

        if time_mod.monotonic() - last_emit >= _INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_emit = time_mod.monotonic()

        await asyncio.sleep(_INDEX_PREDICTION_RUN_POLL_SECONDS)


def _index_prediction_run_stream_response(job_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _index_prediction_run_event_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _kick_index_prediction_run(body: RunIndexPredictionRequest) -> tuple[str, str, bool]:
    from src.trade.index_prediction_run_jobs import spawn_worker, start_job

    key = (body.ticker or "NIFTY").strip().upper()
    job_id, reused = start_job(
        ticker=key,
        horizon_days=body.horizon_days,
        refresh_constituents=body.refresh_constituents,
    )
    if not reused:
        spawn_worker(job_id)
    from src.trade.index_prediction_run_jobs import get_job

    snap = get_job(job_id) or {}
    return job_id, str(snap.get("status") or "queued"), reused


@trade_router.post(
    "/index-prediction/run/start",
    response_model=IndexPredictionRunStartResponse,
    status_code=202,
)
def start_index_prediction_run(
    body: RunIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRunStartResponse:
    """Queue index research pipeline and return a trackable job_id."""
    job_id, job_status, reused = _kick_index_prediction_run(body)
    return IndexPredictionRunStartResponse(
        job_id=job_id,
        job_status=job_status,
        reused=reused,
    )


@trade_router.get("/index-prediction/run/active", response_model=IndexPredictionRunActiveResponse)
def get_active_index_prediction_run(
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRunActiveResponse:
    from src.trade.index_prediction_run_jobs import get_active_job

    snap = get_active_job(ticker)
    if snap is None:
        return IndexPredictionRunActiveResponse(job=None)
    return IndexPredictionRunActiveResponse(job=IndexPredictionRunJobSnapshot(**snap))


@trade_router.get("/index-prediction/run/{job_id}", response_model=IndexPredictionRunJobResponse)
def get_index_prediction_run_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRunJobResponse:
    from src.trade.index_prediction_run_jobs import get_job, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    snap = get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return IndexPredictionRunJobResponse(job=IndexPredictionRunJobSnapshot(**snap))


@trade_router.get("/index-prediction/run/{job_id}/stream")
async def stream_index_prediction_run_job(
    job_id: str,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """SSE: replay pipeline logs and stream until the run terminates."""
    from src.trade.index_prediction_run_jobs import INDEX_PREDICTION_RUN_JOBS, _JOBS_LOCK, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    with _JOBS_LOCK:
        if job_id not in INDEX_PREDICTION_RUN_JOBS:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return _index_prediction_run_stream_response(job_id, request)


@trade_router.post("/index-prediction/run/stream")
async def stream_index_prediction_run(
    body: RunIndexPredictionRequest,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """Run full index research pipeline and stream activity logs via SSE (legacy)."""
    job_id, _, _ = _kick_index_prediction_run(body)
    return _index_prediction_run_stream_response(job_id, request)


@trade_router.post("/index-prediction/refresh", response_model=IndexPredictionRefreshResponse)
def refresh_index_prediction(
    body: RefreshIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionRefreshResponse:
    """Lightweight macro + cached-constituent refresh for live polling."""
    key = (body.ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.light_refresh import run_index_light_refresh
        from src.trade.hub_bridge import _index_doc_to_panel, ensure_trade_stack_path

        ensure_trade_stack_path()
        doc, reason = run_index_light_refresh(
            key,
            horizon_days=body.horizon_days,
            force=body.force,
        )
        artifact = _index_doc_to_panel(doc)
        artifact["asset_type"] = "index"
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction refresh failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexPredictionRefreshResponse(
        status="ok",
        ticker=key,
        reason=reason,
        artifact=artifact,
    )


@trade_router.get("/index-prediction/history", response_model=IndexPredictionHistoryResponse)
def get_index_prediction_history(
    ticker: str = "NIFTY",
    limit: int = 50,
    horizon_days: int | None = None,
    daily_last: bool = True,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionHistoryResponse:
    """Return prediction ledger rows for timeline chart."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.prediction_ledger import (
            list_forecast_history_bundle,
            list_prediction_history,
        )

        if daily_last:
            bundle = list_forecast_history_bundle(
                key,
                limit=max(1, min(limit, 200)),
                horizon_days=horizon_days,
            )
            rows = bundle["daily"]
            return IndexPredictionHistoryResponse(
                status="ok",
                ticker=key,
                rows=rows,
                daily=rows,
                intraday=bundle.get("intraday") or [],
                meta=bundle.get("meta") or {},
            )

        rows = list_prediction_history(
            key,
            limit=max(1, min(limit, 200)),
            horizon_days=horizon_days,
            daily_last=False,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction history failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexPredictionHistoryResponse(status="ok", ticker=key, rows=rows)


@trade_router.get("/index-prediction/factor-history", response_model=IndexFactorHistoryResponse)
def get_index_factor_history(
    ticker: str = "NIFTY",
    days: int = 90,
    start: str | None = None,
    factors: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexFactorHistoryResponse:
    """Return macro factor time series for historical charts."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.prediction_ledger import (
            list_factor_history_series,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        factor_list = [f.strip() for f in factors.split(",") if f.strip()] if factors else None
        payload = list_factor_history_series(
            days=max(7, min(days, 5000)),
            start=start,
            factors=factor_list,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction factor-history failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexFactorHistoryResponse(
        status="ok",
        ticker=key,
        series=payload.get("series", []),
        factors=payload.get("factors", []),
        coverage=payload.get("coverage") or {},
        coverage_notes=payload.get("coverage_notes") or [],
    )


@trade_router.get("/index-prediction/constituent-history", response_model=ConstituentHistoryResponse)
def get_constituent_history(
    symbol: str,
    days: int = 90,
    weight: float | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> ConstituentHistoryResponse:
    """Return archived company research trend for one Nifty constituent."""
    key = (symbol or "").strip().upper()
    if not key:
        return ConstituentHistoryResponse(status="error", message="symbol required")
    try:
        from trade_integrations.dataflows.index_research.constituent_history import (
            build_constituent_history_series,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = build_constituent_history_series(key, days=max(7, min(days, 365)), weight=weight)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("constituent-history failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ConstituentHistoryResponse(
        status="ok",
        symbol=payload.get("symbol", key),
        days=int(payload.get("days") or days),
        snapshot_count=int(payload.get("snapshot_count") or 0),
        has_research_archive=bool(payload.get("has_research_archive")),
        points=payload.get("points") or [],
    )


@trade_router.get("/index-prediction/jobs", response_model=IndexPredictionJobsResponse)
def get_index_prediction_jobs(
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """List scheduled cron jobs that feed the prediction pipeline."""
    try:
        from src.trade.index_prediction_jobs import list_index_prediction_jobs
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = list_index_prediction_jobs()
    except Exception as exc:
        logger.exception("index-prediction jobs list failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return IndexPredictionJobsResponse(
        status=payload.get("status", "ok"),
        env=payload.get("env") or {},
        master_scheduler_running=bool(payload.get("master_scheduler_running")),
        jobs=payload.get("jobs") or [],
    )


@trade_router.post("/index-prediction/jobs/{job_id}/pause", response_model=IndexPredictionJobsResponse)
def pause_index_prediction_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """Pause one index prediction cron job (sets status cancelled)."""
    try:
        from src.trade.index_prediction_jobs import pause_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = pause_index_prediction_job(job_id)
    except Exception as exc:
        logger.exception("pause index job %s failed", job_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if payload.get("status") == "error":
        return IndexPredictionJobsResponse(status="error", message=str(payload.get("message") or "not found"))
    return IndexPredictionJobsResponse(status="ok", job=payload.get("job"))


@trade_router.post("/index-prediction/jobs/{job_id}/resume", response_model=IndexPredictionJobsResponse)
def resume_index_prediction_job_route(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionJobsResponse:
    """Resume a paused index prediction cron job."""
    try:
        from src.trade.index_prediction_jobs import resume_index_prediction_job
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        payload = resume_index_prediction_job(job_id)
    except Exception as exc:
        logger.exception("resume index job %s failed", job_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if payload.get("status") == "error":
        return IndexPredictionJobsResponse(status="error", message=str(payload.get("message") or "not found"))
    return IndexPredictionJobsResponse(status="ok", job=payload.get("job"))


@trade_router.get("/index-prediction/snapshots", response_model=IndexPredictionSnapshotsResponse)
def get_index_prediction_snapshots(
    ticker: str = "NIFTY",
    limit: int = 10,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionSnapshotsResponse:
    """Return versioned index research snapshots."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.dataflows.index_research.snapshots import (
            list_index_research_snapshots,
        )

        snapshots = list_index_research_snapshots(key, limit=max(1, min(limit, 30)))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction snapshots failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return IndexPredictionSnapshotsResponse(status="ok", ticker=key, snapshots=snapshots)


@trade_router.get("/agent-debate", response_model=AgentDebateResponse)
def get_agent_debate(
    ticker: str,
    _auth: None = Depends(require_local_or_auth),
) -> AgentDebateResponse:
    """Load cached TradingAgents debate summary from the hub."""
    key = (ticker or "").strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")
    try:
        from src.trade.hub_bridge import is_debate_running, load_debate_artifact
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if is_debate_running(key):
        return AgentDebateResponse(status="running", ticker=key, running=True)
    debate = load_debate_artifact(key)
    if debate is None:
        return AgentDebateResponse(status="not_found", ticker=key, message="No agent debate yet")
    return AgentDebateResponse(status="ok", ticker=key, debate=debate)


@trade_router.post("/run-debate", response_model=AgentDebateResponse)
def run_debate(
    body: RunDebateRequest,
    _auth: None = Depends(require_local_or_auth),
) -> AgentDebateResponse:
    """Start TradingAgents multi-agent debate (async) or return cached hub summary."""
    key = (body.ticker or "").strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="ticker required")
    asset_type = (body.asset_type or "options").strip().lower()
    try:
        from src.trade.hub_bridge import (
            ensure_trade_stack_path,
            is_debate_running,
            load_debate_artifact,
            run_agent_debate_sync,
        )

        ensure_trade_stack_path()
        if not body.refresh:
            from trade_integrations.context.hub import is_agent_debate_cache_fresh

            cached = load_debate_artifact(key)
            if cached and is_agent_debate_cache_fresh(key):
                return AgentDebateResponse(status="ok", ticker=key, debate=cached)
        if is_debate_running(key):
            return AgentDebateResponse(status="running", ticker=key, running=True)

        session_id = (body.session_id or "").strip()

        def _worker() -> None:
            try:
                debate = run_agent_debate_sync(key, asset_type=asset_type)
                if session_id:
                    from src.api.state import _get_session_service  # noqa: PLC0415

                    svc = _get_session_service()
                    if svc:
                        svc.event_bus.emit(
                            session_id,
                            "research.debate",
                            {"ticker": key, "status": "ready", "debate": debate},
                        )
            except Exception as exc:
                logger.exception("Background run-debate failed for %s", key)
                if session_id:
                    try:
                        from src.api.state import _get_session_service  # noqa: PLC0415

                        svc = _get_session_service()
                        if svc:
                            svc.event_bus.emit(
                                session_id,
                                "research.debate",
                                {"ticker": key, "status": "error", "message": str(exc)},
                            )
                    except Exception:
                        pass

        threading.Thread(target=_worker, daemon=True, name=f"debate-{key}").start()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("run-debate failed for %s", key)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AgentDebateResponse(status="running", ticker=key, running=True)


class AutoPaperStatusResponse(BaseModel):
    enabled: bool
    env_enabled: bool
    halted: bool = False
    halt_reason: str | None = None
    budget_inr: float = 20_000.0
    watchlist: List[str] = Field(default_factory=list)
    open_positions: int = 0
    trades_today: int = 0
    last_tick_at: str | None = None
    last_tick: Dict[str, Any] | None = None
    market_open: bool = False


class AutoPaperStartRequest(BaseModel):
    budget_inr: float | None = None
    watchlist: List[str] | None = None
    primary_ticker: str | None = None
    goal: str | None = None
    mandate: str | None = None
    max_daily_loss_inr: float | None = None
    agent_mode: bool = True
    prompt: str | None = None
    vibe_session_id: str | None = None
    dispatch: bool = False


class AutoPaperBootstrapRequest(BaseModel):
    prompt: str | None = None
    ticker: str = "NIFTY"
    budget_inr: float | None = None
    watchlist: List[str] | None = None
    max_daily_loss_inr: float | None = None
    goal: str | None = None
    mandate: str | None = None
    vibe_session_id: str | None = None
    resume: bool = False
    fresh_session: bool = False
    dispatch: bool = True


@trade_router.get("/auto-paper/status", response_model=AutoPaperStatusResponse)
def auto_paper_status(
    _auth: None = Depends(require_local_or_auth),
) -> AutoPaperStatusResponse:
    """Return automated paper trading session state."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.auto_paper.config import get_auto_paper_config, is_auto_paper_active
        from trade_integrations.auto_paper.engine import is_market_session_open
        from trade_integrations.auto_paper.session_store import load_session
        from trade_integrations.monitor.execution_ledger import list_open_entries
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cfg = get_auto_paper_config()
    session = load_session()
    return AutoPaperStatusResponse(
        enabled=is_auto_paper_active() or bool(session.get("enabled")),
        env_enabled=cfg.enabled,
        halted=bool(session.get("halted")),
        halt_reason=session.get("halt_reason"),
        budget_inr=float(session.get("budget_inr") or cfg.budget_inr),
        watchlist=session.get("watchlist") or list(cfg.watchlist),
        open_positions=len(list_open_entries()),
        trades_today=int(session.get("trades_today") or 0),
        last_tick_at=session.get("last_tick_at"),
        last_tick=session.get("last_tick"),
        market_open=is_market_session_open(cfg),
    )


@trade_router.post("/auto-paper/bootstrap")
async def auto_paper_bootstrap(
    body: AutoPaperBootstrapRequest,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Create a Vibe UI session, inject the paper-trading prompt, and start the agent."""
    try:
        from src.api.state import _get_session_service
        from src.trade.auto_paper_bootstrap import bootstrap_auto_paper_in_vibe
        from trade_integrations.auto_paper.config import get_auto_paper_config
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    svc = _get_session_service()
    if svc is None:
        raise HTTPException(status_code=501, detail="Session runtime not enabled")

    cfg = get_auto_paper_config()
    budget = float(body.budget_inr if body.budget_inr is not None else cfg.budget_inr)
    watchlist = (
        [item.strip().upper() for item in body.watchlist if item.strip()]
        if body.watchlist
        else list(cfg.watchlist)
    )
    return await bootstrap_auto_paper_in_vibe(
        svc,
        prompt=body.prompt,
        ticker=body.ticker,
        budget_inr=budget,
        watchlist=watchlist,
        max_daily_loss_inr=float(body.max_daily_loss_inr or cfg.max_daily_loss_inr),
        goal=body.goal,
        mandate=body.mandate,
        vibe_session_id=body.vibe_session_id,
        resume=body.resume,
        fresh_session=body.fresh_session,
        dispatch=body.dispatch,
    )


@trade_router.post("/auto-paper/start")
async def auto_paper_start(
    body: AutoPaperStartRequest | None = None,
    _auth: None = Depends(require_local_or_auth),
):
    """Start automated intraday paper trading; optionally bootstrap Vibe UI session + prompt."""
    if body and (body.prompt or body.dispatch):
        try:
            from src.api.state import _get_session_service
            from src.trade.auto_paper_bootstrap import bootstrap_auto_paper_in_vibe
            from trade_integrations.auto_paper.config import get_auto_paper_config
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        svc = _get_session_service()
        if svc is None:
            raise HTTPException(status_code=501, detail="Session runtime not enabled")

        cfg = get_auto_paper_config()
        budget = float(body.budget_inr if body.budget_inr is not None else cfg.budget_inr)
        watchlist = (
            [item.strip().upper() for item in body.watchlist if item.strip()]
            if body.watchlist
            else list(cfg.watchlist)
        )
        primary = (body.primary_ticker or "").strip().upper() or (watchlist[0] if watchlist else "NIFTY")
        if primary not in watchlist:
            watchlist.insert(0, primary)
        result = await bootstrap_auto_paper_in_vibe(
            svc,
            prompt=body.prompt,
            ticker=primary,
            budget_inr=budget,
            watchlist=watchlist,
            max_daily_loss_inr=float(body.max_daily_loss_inr or cfg.max_daily_loss_inr),
            goal=body.goal,
            mandate=body.mandate,
            vibe_session_id=body.vibe_session_id,
            resume=False,
            dispatch=body.dispatch,
        )
        result["status_snapshot"] = auto_paper_status()
        return result

    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.auto_paper.config import get_auto_paper_config
        from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
        from trade_integrations.auto_paper.session_store import save_session, start_session
        from trade_integrations.auto_paper.agent_mandate import DEFAULT_GOAL
        from src.scheduled_research.auto_paper_jobs import ensure_vibe_research_jobs
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    cfg = get_auto_paper_config()
    budget = float(body.budget_inr) if body and body.budget_inr is not None else cfg.budget_inr
    watchlist = (
        [item.strip().upper() for item in body.watchlist if item.strip()]
        if body and body.watchlist
        else list(cfg.watchlist)
    )
    primary = (
        (body.primary_ticker or "").strip().upper()
        if body and body.primary_ticker
        else (watchlist[0] if watchlist else "NIFTY")
    )
    if primary and primary not in watchlist:
        watchlist.insert(0, primary)
    try:
        client = OpenAlgoClient()
        client.ensure_analyzer_mode()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    start_session(budget_inr=budget, watchlist=watchlist)
    session_patch = {
        "agent_mode": body.agent_mode if body else True,
        "primary_ticker": primary,
        "goal": (body.goal if body and body.goal else DEFAULT_GOAL),
        "mandate": body.mandate if body and body.mandate else None,
        "max_daily_loss_inr": (
            float(body.max_daily_loss_inr)
            if body and body.max_daily_loss_inr is not None
            else cfg.max_daily_loss_inr
        ),
    }
    from trade_integrations.auto_paper.session_store import load_session

    session = load_session()
    session.update({k: v for k, v in session_patch.items() if v is not None})
    from trade_integrations.auto_paper.lifecycle import default_lifecycle

    if not session.get("lifecycle"):
        session["lifecycle"] = default_lifecycle()
    save_session(session)

    if session.get("agent_mode", True):
        ensure_vibe_research_jobs()

    return auto_paper_status()


@trade_router.post("/auto-paper/stop", response_model=AutoPaperStatusResponse)
def auto_paper_stop(
    _auth: None = Depends(require_local_or_auth),
) -> AutoPaperStatusResponse:
    """Stop automated paper trading."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.auto_paper.mcp_actions import stop_auto_paper
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    stop_auto_paper()
    return auto_paper_status()


class AutoPaperResumeRequest(BaseModel):
    vibe_session_id: str | None = None
    dispatch: bool = True
    fresh_session: bool = True
    prompt: str | None = None


@trade_router.post("/auto-paper/resume")
async def auto_paper_resume(
    body: AutoPaperResumeRequest | None = None,
    dispatch: bool = True,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Resume paper trading in Vibe UI — fresh attempt with continuity in the prompt."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path
        from src.api.state import _get_session_service
        from src.trade.auto_paper_bootstrap import bootstrap_auto_paper_in_vibe
        from trade_integrations.auto_paper.session_store import load_session
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    svc = _get_session_service()
    if svc is None:
        raise HTTPException(status_code=501, detail="Session runtime not enabled")

    paper = load_session()
    ticker = str(paper.get("primary_ticker") or (paper.get("watchlist") or ["NIFTY"])[0])
    should_dispatch = body.dispatch if body is not None else dispatch
    return await bootstrap_auto_paper_in_vibe(
        svc,
        prompt=body.prompt if body else None,
        ticker=ticker,
        vibe_session_id=body.vibe_session_id if body else None,
        resume=True,
        fresh_session=body.fresh_session if body is not None else True,
        dispatch=should_dispatch,
    )


@trade_router.post("/auto-paper/tick")
def auto_paper_tick(
    dry_run: bool = False,
    agent: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Run one auto paper cycle or one agent turn when agent=true."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        if agent:
            import asyncio

            from trade_integrations.auto_paper.runner import resolve_runner

            vibe_url = os.getenv(
                "VIBE_BACKEND_URL",
                f"http://127.0.0.1:{os.getenv('VIBE_BACKEND_PORT', '8899')}",
            )
            runner = resolve_runner(vibe_url=vibe_url)
            result = asyncio.run(runner.run_once())
            return result.to_dict()
        from trade_integrations.auto_paper.engine import run_auto_paper_tick
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return run_auto_paper_tick(dry_run=dry_run)
