"""Trade-stack widget persistence and OpenAlgo execution proxy for Vibe chat."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.security import require_local_or_auth

logger = logging.getLogger(__name__)

trade_router = APIRouter(prefix="/trade", tags=["trade"])

_WIDGET_ID_RE = re.compile(r"(?:tp|ts)_[A-Z][A-Z0-9]*_[0-9a-f]{12}")
_WIDGET_ID_INLINE_RE = re.compile(r"((?:tp|ts)_[A-Z][A-Z0-9]*_[0-9a-f]{12})")
_WIDGET_TOOL_NAMES = frozenset(
    {
        "get_options_trade_widget",
        "mcp_openalgo_get_options_trade_widget",
        "get_stock_trade_widget",
        "mcp_openalgo_get_stock_trade_widget",
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


@trade_router.post("/auto-paper/start", response_model=AutoPaperStatusResponse)
def auto_paper_start(
    body: AutoPaperStartRequest | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> AutoPaperStatusResponse:
    """Start automated intraday paper trading for today's session."""
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
