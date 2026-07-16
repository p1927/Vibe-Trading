"""Trade-stack widget persistence and OpenAlgo execution proxy for Vibe chat."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import asyncio
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.security import require_local_or_auth

logger = logging.getLogger(__name__)

trade_router = APIRouter(prefix="/trade", tags=["trade"])

_WIDGET_ID_RE = re.compile(r"(?:tp|ts|ti)_[A-Z][A-Z0-9]*_[0-9a-f]{12}")
_WIDGET_ID_INLINE_RE = re.compile(r"((?:tp|ts|ti)_[A-Z][A-Z0-9]*_[0-9a-f]{12})")
_WIDGET_TOOL_NAMES = frozenset(
    {
        "get_options_trade_widget",
        "mcp_openalgo_get_options_trade_widget",
        "get_stock_trade_widget",
        "mcp_openalgo_get_stock_trade_widget",
        "get_index_trade_widget",
        "mcp_openalgo_get_index_trade_widget",
    }
)


def trade_widget_dir() -> Path:
    root = Path.home() / ".vibe-trading" / "trade_widgets"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_trade_widget(widget_id: str) -> Optional[Dict[str, Any]]:
    if not _WIDGET_ID_RE.fullmatch(widget_id or ""):
        return None
    path = trade_widget_dir() / f"{widget_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("type") == "trade_plan.widget" else None


def _widget_id_from_preview(preview: str) -> Optional[str]:
    """Extract widget id from tool_result preview (often escaped/truncated JSON)."""
    text = preview or ""
    inline = _WIDGET_ID_INLINE_RE.search(text)
    if inline:
        return inline.group(1)
    match = re.search(r'"widget_id"\s*:\s*"((?:tp|ts)_[^"]+)"', text)
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


@trade_router.post("/execute-basket", response_model=ExecuteBasketResponse)
def execute_basket(
    body: ExecuteBasketRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ExecuteBasketResponse:
    """Place a multi-leg basket order via OpenAlgo REST (after user confirms in widget)."""
    orders = list(body.orders or [])
    if not orders and body.widget_id:
        widget = load_trade_widget(body.widget_id)
        if not widget:
            raise HTTPException(status_code=404, detail="Widget not found")
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


def _material_news_count(ticker: str) -> int:
    try:
        from trade_integrations.monitor.news_watcher import check_material_news
        from trade_integrations.monitor.service import MonitorService

        since = MonitorService._news_since(ticker)
        return len(check_material_news(ticker, since))
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
    """Verified news → Nifty impact snapshot from hub SSOT; refresh ingests cache misses only."""
    key = (ticker or "NIFTY").strip().upper()
    try:
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.news_impact_engine import (
            build_news_impact_snapshot,
            load_news_impact_snapshot,
            save_news_impact_snapshot,
        )
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
            report = build_news_impact_snapshot(
                ticker=key,
                horizon_days=horizon_days,
                spot=spot,
                macro_factors=macro or None,
                refresh_ingest=True,
                include_rejected=include_rejected,
            )
            save_news_impact_snapshot(report, ticker=key)
        else:
            report = load_news_impact_snapshot(key)
            if report is None and doc is not None and getattr(doc, "news_impact", None):
                report = doc.news_impact
            if report is None:
                report = build_news_impact_snapshot(
                    ticker=key,
                    horizon_days=horizon_days,
                    spot=spot,
                    macro_factors=macro or None,
                    refresh_ingest=False,
                    include_rejected=include_rejected,
                )
            else:
                report = build_news_impact_snapshot(
                    ticker=key,
                    horizon_days=horizon_days,
                    spot=spot,
                    refresh_ingest=False,
                    include_rejected=include_rejected,
                )
            save_news_impact_snapshot(report, ticker=key)
        status = str((report or {}).get("status") or "ok")
        return IndexNewsImpactResponse(status=status, ticker=key, report=report)
    except Exception as exc:
        logger.exception("index-prediction news-impact failed for %s", key)
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


@trade_router.post("/index-prediction/run/stream")
async def stream_index_prediction_run(
    body: RunIndexPredictionRequest,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """Run full index research pipeline and stream activity logs via SSE."""
    key = (body.ticker or "NIFTY").strip().upper()
    event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def on_log(entry) -> None:
        event_queue.put({"type": "log", "entry": entry.to_dict()})

    def worker() -> None:
        try:
            from trade_integrations.context.hub import save_index_research
            from trade_integrations.dataflows.index_research.aggregator import run_index_research
            from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger
            from src.trade.hub_bridge import _index_doc_to_panel, ensure_trade_stack_path

            ensure_trade_stack_path()
            plog = PipelineLogger(on_entry=on_log)
            doc = run_index_research(
                key,
                horizon_days=body.horizon_days,
                refresh_constituents=body.refresh_constituents,
                pipeline=plog,
            )
            save_index_research(doc)
            artifact = _index_doc_to_panel(doc)
            artifact["asset_type"] = "index"
            event_queue.put({"type": "done", "ticker": key, "artifact": artifact})
        except Exception as exc:
            logger.exception("index-prediction stream failed for %s", key)
            event_queue.put({"type": "error", "message": str(exc)})
        finally:
            event_queue.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.to_thread(event_queue.get, timeout=0.5)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if item is None:
                break
            event_type = str(item.get("type") or "message")
            yield f"event: {event_type}\ndata: {json.dumps(item, default=str)}\n\n"
            if event_type in {"done", "error"}:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
        payload = list_factor_history_series(days=max(7, min(days, 365)), factors=factor_list)
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
