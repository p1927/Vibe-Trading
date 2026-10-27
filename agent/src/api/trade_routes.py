"""Trade-stack widget persistence and OpenAlgo execution proxy for Vibe chat."""

from __future__ import annotations

import json
import logging
import re
import threading
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from src.api.helpers import bad_gateway_on_error, is_unsafe_path_component, redacted_error_detail, safe_path_component
from src.api.security import require_event_stream_auth, require_local_or_auth
from trade_integrations.market_registry import market_for_ticker
from trade_integrations.trade_widgets.store import load_trade_widget

logger = logging.getLogger(__name__)

# Query keys these routes join onto hub/artifact paths (``hub_dir / ticker / ...``,
# ``runs / recipe / f"{run_id}.json"``). Path params are checked for every route as well.
_PATHISH_QUERY_KEYS = ("ticker", "entity_id", "symbol", "exchange", "recipe", "run_id", "agent_id", "source_id")


async def _reject_unsafe_path_inputs(request: Request) -> None:
    """Router-wide: refuse a path param or path-joined query value that could leave its directory.

    ``../`` in a ticker/recipe/run_id reached ``mkdir``/``read_text`` in the hub and
    forecast-engine stores, and ``..`` as ``country``/``job_id`` was interpolated, unquoted,
    into the URL the stock_simulator client calls with its control token.
    """
    values = list(request.path_params.values())
    for key in _PATHISH_QUERY_KEYS:
        values.extend(request.query_params.getlist(key))
    if any(is_unsafe_path_component(str(v)) for v in values):
        raise HTTPException(status_code=400, detail="invalid path component")


trade_router = APIRouter(prefix="/trade", tags=["trade"], dependencies=[Depends(_reject_unsafe_path_inputs)])


def _ticker_key(raw: str | None, default: str = "NIFTY") -> str:
    """Canonical (upper-case, stripped) ticker/entity id from a request; 400 when path-unsafe.

    The default applies only when the parameter is absent: a present-but-blank `ticker=` is a 422, not a silent NIFTY.
    """
    if raw is not None and not raw.strip() and default:
        raise HTTPException(status_code=422, detail="ticker must not be empty (omit it for the default)")
    return safe_path_component((default if raw is None else raw).strip().upper(), "ticker")


def _query_day(raw: str | None, name: str) -> str | None:
    """A `YYYY-MM-DD` query day (an ISO datetime keeps its date part); 422 on anything else, None when absent."""
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.strip()).date().isoformat()
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be a valid YYYY-MM-DD date, got {raw!r}") from None


def _query_window(since: str | None, until: str | None, day: str | None) -> tuple[str | None, str | None, str | None]:
    """Validated (since, until, day); 422 when `until` is before `since`."""
    s, u, d = _query_day(since, "since"), _query_day(until, "until"), _query_day(day, "day")
    if s and u and u < s:
        raise HTTPException(status_code=422, detail=f"until ({u}) is before since ({s})")
    return (since if s else None), (until if u else None), d


def _query_limit(limit: int, *, maximum: int) -> int:
    """`limit` must be >= 1 (422); above `maximum` it is clamped (the response count shows what was returned)."""
    if limit < 1:
        raise HTTPException(status_code=422, detail=f"limit must be >= 1, got {limit}")
    return min(limit, maximum)

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

# Routes below live in src/api/trade_execution_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_execution_routes import (  # noqa: E402,F401
    ExecuteBasketRequest,
    ExecuteBasketResponse,
    ExecutionModeResponse,
    TradeChargesRequest,
    TradeChargesResponse,
    _assert_basket_passes_risk_gate,
    _assert_execution_allowed,
    _openalgo_analyzer_status,
    _openalgo_config,
    _openalgo_switch_url,
    _paper_mode_env_enabled,
    _resolve_execution_mode,
    execute_basket,
    execution_mode,
    trade_charges,
)

# Routes below live in src/api/trade_widget_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_widget_routes import (  # noqa: E402,F401
    _count_verified_headlines_since,
    _has_open_plan_position,
    _live_context_from_report,
    _material_news_count,
    _widget_staleness_from_report,
    get_plan_context,
    get_widget,
)


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
    key = _ticker_key(ticker, default="")
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
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return HubPlanResponse(status="not_found", ticker=key, asset_type=asset_type, message="No hub plan")
    return HubPlanResponse(status="ok", ticker=key, asset_type=artifact.get("asset_type", asset_type), artifact=artifact)


class IndexPredictionResponse(BaseModel):
    status: str
    ticker: str = ""
    artifact: Dict[str, Any] | None = None
    message: str = ""
    degraded_reason: str | None = None


class RunIndexPredictionRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int | None = None
    refresh_constituents: bool = False
    run_forecast_lab: bool = True


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
    run_forecast_lab: bool = False
    created_at: str | None = None
    error: str | None = None
    warnings: List[str] = Field(default_factory=list)
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    artifact: Dict[str, Any] | None = None
    current_stage: str | None = None
    last_log_at: str | None = None
    last_log_message: str | None = None
    stage_elapsed_ms: float | None = None
    current_track_id: str | None = None


class IndexPredictionRunActiveResponse(BaseModel):
    status: str = "ok"
    job: IndexPredictionRunJobSnapshot | None = None


class IndexPredictionRunJobResponse(BaseModel):
    status: str = "ok"
    job: IndexPredictionRunJobSnapshot | None = None


class StartRecordingRequest(BaseModel):
    underlyings: List[str] = Field(default_factory=lambda: ["NIFTY", "BANKNIFTY", "SENSEX"])
    equities: List[str] = Field(default_factory=list)
    # Opt-in convenience: merges the live NIFTY50 constituent list (via
    # constituents.load_nifty50_constituents()) into `equities` at job
    # start, instead of requiring the caller to paste all 50 symbols.
    # Default False so existing/automated callers are unaffected.
    include_nifty50_constituents: bool = False
    poll_interval_s: int = 10                       # legacy; used only when category_intervals is None
    category_intervals: Dict[str, int] | None = None
    equity_intervals: Dict[str, int] | None = None
    ws_throttle_hz: float | None = None
    historical_config: Dict[str, Any] | None = None
    wait_for_open: bool = False


class RecordingRunStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    job_status: str
    reused: bool = False


class AutoRecordRequest(BaseModel):
    """Toggle Auto Record. Same recording-config shape as ``StartRecordingRequest``
    (minus ``wait_for_open``, which is implied) — the config is captured as the
    daily template when ``enabled=True`` and ignored when ``enabled=False``."""

    enabled: bool
    underlyings: List[str] = Field(default_factory=lambda: ["NIFTY", "BANKNIFTY", "SENSEX"])
    equities: List[str] = Field(default_factory=list)
    include_nifty50_constituents: bool = False
    poll_interval_s: int = 10                       # legacy; used only when category_intervals is None
    category_intervals: Dict[str, int] | None = None
    equity_intervals: Dict[str, int] | None = None
    ws_throttle_hz: float | None = None
    historical_config: Dict[str, Any] | None = None


class AutoRecordStatusResponse(BaseModel):
    status: str = "ok"
    enabled: bool
    config: Dict[str, Any] | None = None
    updated_at: str | None = None
    active_job_id: str | None = None
    active_job_status: str | None = None


class RecordingJobSnapshot(BaseModel):
    job_id: str
    status: str
    underlyings: List[str] = Field(default_factory=list)
    equities: List[str] = Field(default_factory=list)
    poll_interval_s: int = 10                       # legacy; reflects whichever request created the job, ignored when category_intervals is set
    category_intervals: Dict[str, int] | None = None
    equity_intervals: Dict[str, int] | None = None
    ws_throttle_hz: float | None = None
    historical_config: Dict[str, Any] | None = None
    wait_for_open: bool = False
    # Phase C: ISO timestamp of the scheduled wake deadline when the
    # recorder is in ``waiting_for_open`` state. The frontend renders
    # the "Next open at HH:MM IST" subtitle from this directly (with a
    # log-entry fallback for jobs persisted before Phase C).
    next_open_at: str | None = None
    created_at: str | None = None
    session_date: str | None = None
    error: str | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    result: Dict[str, Any] | None = None
    last_log_at: str | None = None
    last_log_message: str | None = None
    session_pct_complete: float | None = None


class RecordingActiveResponse(BaseModel):
    status: str = "ok"
    job: RecordingJobSnapshot | None = None


class RecordingJobResponse(BaseModel):
    status: str = "ok"
    job: RecordingJobSnapshot | None = None


class RecordingSessionsResponse(BaseModel):
    status: str = "ok"
    sessions: List[str] = Field(default_factory=list)


class ConstituentInfo(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    weight: float | None = None
    # No published weight for this member (D410): `weight` is then a 0.0 placeholder, not a real 0% share.
    weight_missing: bool = False


class RecordingConstituentsResponse(BaseModel):
    status: str = "ok"
    constituents: List[ConstituentInfo] = Field(default_factory=list)


class StartReplayRequest(BaseModel):
    speed: float | None = None
    loop: bool | None = None
    end_date: str | None = None


class ReplayStatusResponse(BaseModel):
    status: str = "ok"
    message: str | None = None
    replay: Dict[str, Any] | None = None


class SeekReplayRequest(BaseModel):
    time: str


class SetReplaySpeedRequest(BaseModel):
    speed: float


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
    daily: List[Dict[str, Any]] = Field(default_factory=list)
    intraday: List[Dict[str, Any]] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class ForecastReplayPoint(BaseModel):
    horizon_days: int
    target_date: str
    p50_level: float
    p10_level: float | None = None
    p90_level: float | None = None
    matured: bool
    actual_level: float | None = None
    error_pts: float | None = None
    error_pct: float | None = None
    in_band: bool | None = None
    direction_hit: bool | None = None
    projected: bool = False  # target_date is a weekday projection past the last realised close


class ForecastReplayRecord(BaseModel):
    source_id: str
    family: str
    issued_at: str
    issue_day: str
    provenance: Literal["live", "replay", "backtest"]
    spot_at_issue: float
    points: List[ForecastReplayPoint] = Field(default_factory=list)
    approximate_path: bool
    meta: Dict[str, Any] = Field(default_factory=dict)


class ForecastReplaySource(BaseModel):
    source_id: str
    family: str
    label: str
    provenance: Literal["live", "replay", "backtest"]
    status: Literal["ok", "empty", "error"]
    reason: str | None = None
    first_day: str | None = None
    last_day: str | None = None
    n_stored: int = 0  # records the store holds for the source (whole history, last run per day): an inventory
    n_returned: int | None = None  # records of THIS call for the source; null when the call did not read it


class ForecastReplayActual(BaseModel):
    date: str
    close: float


class ForecastReplayResponse(BaseModel):
    status: str
    ticker: str = ""
    actuals: List[ForecastReplayActual] = Field(default_factory=list)
    sources: List[ForecastReplaySource] = Field(default_factory=list)
    records: List[ForecastReplayRecord] = Field(default_factory=list)
    stats: Dict[str, Dict[str, Any]] =Field(default_factory=dict)
    message: str = ""


class IndexFactorHistoryResponse(BaseModel):
    status: str
    ticker: str = ""
    series: List[Dict[str, Any]] = Field(default_factory=list)
    factors: List[str] = Field(default_factory=list)
    coverage: Dict[str, int] = Field(default_factory=dict)
    coverage_notes: List[str] = Field(default_factory=list)
    message: str = ""


class ForecastEngineArtifactResponse(BaseModel):
    status: str
    available: bool = False
    ticker: str = ""
    recipe_name: str = ""
    recipe_version: str = ""
    engine_checkpoint: str = ""
    horizon_days: int = 0
    as_of: str = ""
    generated_at: str = ""
    spot: float = 0.0
    expected_return_pct: float = 0.0
    quantiles_pct: Dict[str, float] = Field(default_factory=dict)
    dataset: List[Dict[str, Any]] = Field(default_factory=list)
    covariate_keys: List[str] = Field(default_factory=list)
    message: str = ""


class ForecastEngineRecipeInfo(BaseModel):
    name: str
    version: str
    covariate_keys: List[str] = Field(default_factory=list)
    target: str = ""
    description: str = ""


class ForecastEngineRecipesResponse(BaseModel):
    status: str
    recipes: List[ForecastEngineRecipeInfo] = Field(default_factory=list)
    default_recipe: str = ""
    message: str = ""


class ForecastEngineEvaluationResponse(BaseModel):
    status: str
    available: bool = False
    ticker: str = ""
    recipe_name: str = ""
    recipe_version: str = ""
    engine_checkpoint: str = ""
    horizon_days: int = 0
    generated_at: str = ""
    n_observations: int = 0
    n_skipped_gaps: int = 0
    mean_pinball_loss: float = 0.0
    mean_naive_pinball_loss: float = 0.0
    passes_gate: bool = False
    gate_lower_bound: float = 0.0
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


class ForecastEngineRunStartRequest(BaseModel):
    # ``start``/``end`` become the run_id (an artifact file name) and ``recipe``/``ticker`` path
    # segments, so a free string here was a write outside the artifact directory.
    ticker: str = "NIFTY"
    recipe: str
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    horizon: int = 5

    @field_validator("ticker", "recipe")
    @classmethod
    def _path_safe(cls, value: str) -> str:
        if is_unsafe_path_component(value):
            raise ValueError("must be a plain name, not a path")
        return value


class ForecastEngineRunStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    run_id: str
    job_status: str
    reused: bool = False


class ForecastEngineRunJobSnapshot(BaseModel):
    job_id: str
    status: str
    created_at: str | None = None
    error: str | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)
    result: Dict[str, Any] | None = None


class ForecastEngineRunJobResponse(BaseModel):
    status: str = "ok"
    job: ForecastEngineRunJobSnapshot


class ForecastEngineRunSummary(BaseModel):
    run_id: str
    run_start: str | None = None
    run_end: str | None = None
    generated_at: str | None = None
    n_observations: int | None = None
    passes_gate: bool | None = None
    mean_pinball_loss: float | None = None


class ForecastEngineRunListResponse(BaseModel):
    status: str = "ok"
    runs: List[ForecastEngineRunSummary] = Field(default_factory=list)


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


class IndexUpcomingEventOutcome(BaseModel):
    reference_date: str | None = None
    reference_close: float | None = None
    outcome_date: str | None = None
    outcome_close: float | None = None
    return_pct: float | None = None
    session_lag: int | None = None


class HistoricalUpcomingEvent(BaseModel):
    date: str | None = None
    days_from_now: int | None = None
    event_type: str | None = None
    label: str | None = None
    symbol: str | None = None
    weight: float | None = None
    sector: str | None = None
    impact: str | None = None
    category: str | None = None
    market_outcome: IndexUpcomingEventOutcome | None = None


class HistoricalUpcomingEventsResponse(BaseModel):
    status: str = "ok"
    events: List[HistoricalUpcomingEvent] = Field(default_factory=list)
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
    artifact: Dict[str, Any] | None = None


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
    master_scheduler_env_enabled: bool = False
    master_scheduler_running: bool = False
    executor_is_running: bool = False
    news_pipeline: Dict[str, Any] = Field(default_factory=dict)
    index_quote: Dict[str, Any] = Field(default_factory=dict)
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
    """The capture set is declared in the factor registry (D367); the only editable setting is the switch."""

    capture_enabled: bool


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


class HubNewsPage(BaseModel):
    total_count: int = 0
    page: int = 1
    page_size: int = 20
    has_more: bool = False
    sort: str = "relevance"
    search: str = ""
    window_hours: int | None = None


class HubStatusResponse(BaseModel):
    status: str = "ok"
    hub: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    news_page: HubNewsPage = Field(default_factory=HubNewsPage)


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
    entity_drain_cron: str | None = None
    entity_maintenance_cron: str | None = None
    entity_drain_continuous_cron: str | None = None
    entity_backpressure_threshold: int | None = None
    full_ingest_sources: str | None = None
    light_ingest_sources: str | None = None
    full_lookback_days: int | None = None
    light_lookback_days: int | None = None
    entity_batch_size: int | None = None
    cluster_threshold: float | None = None
    relevance_gate_enabled: bool | None = None
    relevance_min_confidence: float | None = None
    relevance_score_llm_ambiguous_low: float | None = None
    relevance_score_llm_ambiguous_high: float | None = None
    relevance_rule_first: bool | None = None
    discard_retention_days: int | None = None
    wiki_search_enabled: bool | None = None
    wiki_search_top_k: int | None = None
    wiki_search_max_per_pass: int | None = None
    wiki_search_min_score: float | None = None


class HubNewsCalendarEventArticle(BaseModel):
    event_id: str = ""
    title: str = ""
    url: str = ""
    publisher: str = ""
    source: str = ""
    verification_status: str = ""
    predicted_impact: Dict[str, Any] | None = None
    actual_impact: Dict[str, Any] | None = None


class HubNewsCalendarEvent(BaseModel):
    date: str = ""
    event: str = ""
    type: str = ""
    timeline_phrase: str = ""
    date_confidence: str = ""
    index_impact_mechanism: str = ""
    verification_status: str = ""
    fact_check: Dict[str, Any] | None = None
    articles: List[HubNewsCalendarEventArticle] = Field(default_factory=list)


class HubNewsEventsCalendarResponse(BaseModel):
    status: str = "ok"
    events: List[HubNewsCalendarEvent] = Field(default_factory=list)
    message: str = ""


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


class FactorRegistryGapCandidate(BaseModel):
    candidate: str = ""
    status: str = "pending"
    distinct_ref_count: int = 0
    occurrence_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None
    sample_titles: list[str] = Field(default_factory=list)
    note: str = ""
    decided_at: str | None = None


class FactorRegistryGapReviewResponse(BaseModel):
    status: str = "ok"
    # Verbatim from the facade. The UI renders this next to the Add action so the button cannot
    # be read as "this factor now exists" — accepting queues a hand-written FactorSpec, it does
    # not modify `factors.registry`.
    accepted_means: str = ""
    counts: Dict[str, int] = Field(default_factory=dict)
    candidates: list[FactorRegistryGapCandidate] = Field(default_factory=list)
    message: str = ""


class FactorRegistryGapDecisionRequest(BaseModel):
    candidate: str = ""
    decision: str = ""  # accepted | ignored | reset
    note: str = ""


class FactorRegistryGapDecisionResponse(BaseModel):
    status: str = "ok"
    candidate: FactorRegistryGapCandidate | None = None
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


class HubNewsPipelineTraceSummaryResponse(BaseModel):
    status: str = "ok"
    summary: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class HubNewsPipelineTraceItemsResponse(BaseModel):
    status: str = "ok"
    items: list[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0
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


class ExternalPredictionsResponse(BaseModel):
    status: str = "ok"
    ticker: str = "NIFTY"
    snapshot: Dict[str, Any] | None = None
    message: str = ""


def _external_predictions_status(snapshot: Any) -> str:
    if snapshot is None:
        return "ok"
    had_errors = bool(getattr(snapshot, "had_errors", False))
    attempt_failures = int(getattr(snapshot, "refresh_attempt_failures", 0) or 0)
    if had_errors or attempt_failures > 0:
        return "partial"
    return "ok"


def _external_prediction_source_health(snapshot: Any) -> list[dict[str, Any]]:
    """Per-source health view, computed from the snapshot rather than stored — mirrors the
    vendor/capability/status pattern used for Hub's `source_availability` card."""
    if snapshot is None:
        return []
    sources_by_id = {s.id: s for s in getattr(snapshot, "sources", None) or []}
    rows: list[dict[str, Any]] = []
    for record in getattr(snapshot, "predictions", None) or []:
        source = sources_by_id.get(record.source_id)
        fetch_status = str(getattr(record, "fetch_status", "") or "unknown")
        status = {"ok": "available", "stale": "stale", "error": "error", "not_found": "error"}.get(
            fetch_status, "unknown"
        )
        provenance = dict(getattr(record, "provenance", None) or {})
        last_attempt = provenance.get("last_refresh_attempt") or {}
        rows.append(
            {
                "source_id": record.source_id,
                "display_name": (source.display_name if source else record.source_id),
                "kind": (source.kind if source else None),
                "fetch_strategy": (getattr(source, "fetch_strategy", None) if source else None),
                "last_success_at": (
                    last_attempt.get("at") if fetch_status == "ok" else provenance.get("last_success_at")
                )
                or (getattr(record, "as_of", "") if fetch_status == "ok" else ""),
                "status": status,
                "last_error_code": getattr(record, "error_message", "") or None,
            }
        )
    return rows


class ExternalPredictionsRefreshRequest(BaseModel):
    ticker: str = "NIFTY"
    horizon_days: int = 14


class ExternalPredictionsRefreshStartResponse(BaseModel):
    status: str = "ok"
    job_id: str
    job_status: str
    reused: bool = False


class ExternalPredictionsRefreshJobSnapshot(BaseModel):
    job_id: str
    status: str
    ticker: str = "NIFTY"
    horizon_days: int = 14
    created_at: str | None = None
    error: str | None = None
    logs: List[Dict[str, Any]] = Field(default_factory=list)
    snapshot: Dict[str, Any] | None = None


class ExternalPredictionsRefreshActiveResponse(BaseModel):
    status: str = "ok"
    job: ExternalPredictionsRefreshJobSnapshot | None = None


class ExternalPredictionsRefreshJobResponse(BaseModel):
    status: str = "ok"
    job: ExternalPredictionsRefreshJobSnapshot | None = None


class ExternalPredictionSourceRequest(BaseModel):
    id: str | None = None
    display_name: str
    domains: List[str] = Field(default_factory=list)
    entry_urls: List[str] = Field(default_factory=list)
    search_queries: List[str] = Field(default_factory=list)
    kind: str = "media"


class ExternalPredictionSourcesResponse(BaseModel):
    status: str = "ok"
    ticker: str = "NIFTY"
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


def _index_artifact_degraded_reason(artifact: Dict[str, Any] | None) -> str | None:
    """Surface a human-readable reason when a plan_status=incomplete artifact is served as ok.

    `plan_status`/`data_warnings` already ride inside the artifact body (see
    `hub_bridge._index_doc_to_panel`), but nothing at the response level flagged an
    incomplete plan loudly — a 200 + status:"ok" with an empty `prediction` dict looked
    identical to a genuine "nothing changed" response.
    """
    if not artifact or artifact.get("plan_status") != "incomplete":
        return None
    warnings = artifact.get("data_warnings") or []
    if warnings:
        return f"Index prediction incomplete: {warnings[0]}"
    return "Index prediction incomplete: no view/contributors available."


# ponytail: a full run stamps the fan at run start and the headline at run end, so the same run's fan
# is a little older than its own headline; anything within this slack is "same run". Ceiling: a light
# refresh inside the slack keeps showing the fan as current. Upgrade path: stamp the fan's run id on the artifact.
_FAN_SAME_RUN_SLACK = timedelta(minutes=30)


def _fan_is_stale(headline_as_of: Any, fan_predicted_at: Any) -> bool:
    """True when the fan is older than the headline by more than a run's duration, or either
    stamp is missing/unparseable (a fan that cannot be proven current is not presented as current).
    Naive stamps are UTC (the hub stores UTC)."""

    def _parse(v: Any) -> datetime | None:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    head, fan = _parse(headline_as_of), _parse(fan_predicted_at)
    return head is None or fan is None or head - fan > _FAN_SAME_RUN_SLACK


def _attach_latest_forecast_fan(artifact: dict[str, Any] | None, ticker: str) -> None:
    """Mutate ``artifact["prediction"]["forecast_fan"]`` in place with the most recently
    persisted `nifty_forecast_fan` band, when one exists.

    Shared by both index-prediction read paths (`get_index_prediction` below and the
    command-center SSE stream's own `load_hub_plan_artifact` poll) — the hub-cached artifact
    itself never carries fan data (`aggregator.py` persists the fan straight to the
    prediction ledger, not into the hub JSON blob), so every reader of the cached artifact
    needs this same enrichment step rather than just one of them.
    See .claude/backlog/items/2026-08-26-wire-nifty-forecast-fan-into-consumers.md.
    """
    if not isinstance(artifact, dict) or not isinstance(artifact.get("prediction"), dict):
        return
    try:
        from trade_integrations.dataflows.index_research.nifty_forecast_fan import (
            load_latest_fan_band,
        )

        fan_band = load_latest_fan_band(ticker=ticker)
    except Exception:
        logger.exception("index-prediction: failed to load latest forecast fan for %s", ticker)
        return
    if fan_band:
        fan_band["fan_as_of"] = fan_band.get("predicted_at")
        fan_band["fan_stale"] = _fan_is_stale(artifact.get("as_of"), fan_band.get("predicted_at"))
        artifact["prediction"]["forecast_fan"] = fan_band


@trade_router.get("/index-prediction", response_model=IndexPredictionResponse)
def get_index_prediction(
    ticker: str = "NIFTY",
    horizon_days: int | None = None,
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionResponse:
    """Load cached index research artifact for the prediction page."""
    key = _ticker_key(ticker)
    if horizon_days is not None and horizon_days < 1:
        raise HTTPException(status_code=422, detail=f"horizon_days must be >= 1, got {horizon_days}")
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
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return IndexPredictionResponse(status="not_found", ticker=key, message="No index research")
    if horizon_days is not None and artifact.get("horizon", {}).get("days") != horizon_days:
        artifact["_horizon_mismatch"] = True
    _attach_latest_forecast_fan(artifact, key)
    return IndexPredictionResponse(
        status="ok",
        ticker=key,
        artifact=artifact,
        degraded_reason=_index_artifact_degraded_reason(artifact),
    )


@trade_router.get("/index-prediction/factors", response_model=IndexFactorCatalogResponse)
def get_index_prediction_factors(
    _auth: None = Depends(require_local_or_auth),
) -> IndexFactorCatalogResponse:
    """Return catalog of macro, technical, bottom-up, and model factors."""
    with bad_gateway_on_error(logger, "index-prediction factors catalog failed"):
        from trade_integrations.dataflows.index_research.pipeline_stage_catalog import (
            list_factor_catalog,
        )

        payload = list_factor_catalog()
        return IndexFactorCatalogResponse(status="ok", **payload)


@trade_router.get("/capture-registry", response_model=CaptureRegistryResponse)
def get_capture_registry(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> CaptureRegistryResponse:
    """Return hub capture registry, factor tiers, and storage stats."""
    key = _ticker_key(entity_id, default="")
    with bad_gateway_on_error(logger, "capture-registry get failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.registry import (
            build_capture_stats,
            build_factor_tree,
            load_registry,
        )
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        reg = load_registry()
        return CaptureRegistryResponse(
            status="ok",
            registry=reg,
            factor_tree=build_factor_tree(),
            stats=build_capture_stats(key),
            coverage=capture_coverage_stats(entity_id=key),
        )


@trade_router.put("/capture-registry", response_model=CaptureRegistryResponse)
def update_capture_registry(
    body: CaptureRegistryUpdateRequest,
    _auth: None = Depends(require_local_or_auth),
) -> CaptureRegistryResponse:
    """Update capture settings for an entity (v1: NIFTY)."""
    key = _ticker_key(body.entity_id, default="")
    with bad_gateway_on_error(logger, "capture-registry update failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.registry import (
            build_capture_stats,
            build_factor_tree,
            load_registry,
            set_capture_enabled,
        )
        from trade_integrations.hub_capture.rollup import capture_coverage_stats

        set_capture_enabled(key, body.patch.capture_enabled)
        reg = load_registry()
        return CaptureRegistryResponse(
            status="ok",
            registry=reg,
            factor_tree=build_factor_tree(),
            stats=build_capture_stats(key),
            coverage=capture_coverage_stats(entity_id=key),
        )


@trade_router.post("/capture-registry/backfill")
def run_capture_registry_backfill(
    body: CaptureRegistryBackfillRequest,
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Backfill proprietary NIFTY factor history (participant OI, flows)."""
    key = _ticker_key(body.entity_id, default="")
    with bad_gateway_on_error(logger, "capture-registry backfill failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.intraday import run_capture_backfill

        return run_capture_backfill(entity_id=key, days=max(30, min(body.days, 730)))


@trade_router.post("/capture-registry/intraday")
def run_capture_registry_intraday(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> Dict[str, Any]:
    """Run one intraday chain capture now (OpenAlgo → hub)."""
    key = _ticker_key(entity_id, default="")
    with bad_gateway_on_error(logger, "capture-registry intraday failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_capture.intraday import run_intraday_capture

        return run_intraday_capture(entity_id=key)


@trade_router.get("/hub/status", response_model=HubStatusResponse)
def get_hub_status(
    entity_id: str = "NIFTY",
    search: str | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 20,
    window_hours: int | None = None,
    provenance: str | None = None,
    detail: str = "summary",
    _auth: None = Depends(require_local_or_auth),
) -> HubStatusResponse:
    """Return hub inventory: staging queue, verified news, cache health, capture stats.

    ``detail=full`` additionally runs the LLM-Wiki network probes in the news_pipeline block.
    The default is deliberately cheap: this endpoint is polled every 30s by the Hub page.
    """
    key = _ticker_key(entity_id, default="")
    size = max(1, min(int(page_size or 20), 100))
    current_page = max(1, int(page or 1))
    with bad_gateway_on_error(logger, "hub status failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.hub_storage.hub_status import build_hub_status
        from trade_integrations.dataflows.news_hub_bridge import apply_news_feed_to_hub

        # The feed pool has to cover every page the client can ask for, so the
        # inventory load scales with the requested page rather than the old 50.
        pool_size = max(50, min(current_page * size + size, 400))
        news_since: str | None = None
        if window_hours is not None and int(window_hours) > 0:
            # A window is filtered at the source (parquet/JSONL scan), not by
            # truncating an already-capped pool, so it isn't limited to the
            # page-scaled pool_size above.
            news_since = (datetime.now(timezone.utc) - timedelta(hours=int(window_hours))).isoformat()
        hub = build_hub_status(
            entity_id=key, news_limit=pool_size, news_since=news_since, detail=detail
        )
        page_meta = apply_news_feed_to_hub(
            hub,
            ticker=key,
            search=search,
            sort=sort,
            page=current_page,
            page_size=size,
            window_hours=window_hours,
            provenance=provenance,
        )
        return HubStatusResponse(status="ok", hub=hub, news_page=HubNewsPage(**page_meta))


@trade_router.post("/hub/staging/drain", response_model=HubStagingDrainResponse)
def drain_hub_staging(
    entity_id: str = "NIFTY",
    limit: int = 20,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Manually process a batch of queued staging news refs."""
    key = _ticker_key(entity_id, default="")
    with bad_gateway_on_error(logger, "hub staging drain failed"):
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


@trade_router.get("/hub/news-pipeline/config", response_model=HubNewsPipelineConfigResponse)
def get_hub_news_pipeline_config(
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineConfigResponse:
    """Return hub news ingest/distill schedule (env defaults + hub override file)."""
    with bad_gateway_on_error(logger, "hub news pipeline config read failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import get_pipeline_config

        return HubNewsPipelineConfigResponse(status="ok", config=get_pipeline_config())


@trade_router.patch("/hub/news-pipeline/config", response_model=HubNewsPipelineConfigResponse)
def patch_hub_news_pipeline_config(
    body: HubNewsPipelineConfigUpdate,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineConfigResponse:
    """Update persisted pipeline config and sync scheduled job crons."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import update_pipeline_config

        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        payload = update_pipeline_config(patch)
        return HubNewsPipelineConfigResponse(status="ok", config=payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("hub news pipeline config update failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/news-events/calendar", response_model=HubNewsEventsCalendarResponse)
def get_hub_news_events_calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsEventsCalendarResponse:
    """News-extracted future events across the whole corpus, each resolved back to its
    source article(s) for the Hub Events calendar view."""
    with bad_gateway_on_error(logger, "hub news events calendar read failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import (
            now_instant,
            list_extracted_future_events,
            query_verified_news,
        )

        rows = list_extracted_future_events(market="IN", start=start, end=end)

        # Resolve all source articles with a single bulk read rather than one facade
        # call per event_id — get_distilled_event() reloads the whole events frame on
        # every call, so calling it in a per-row loop is an N+1 that times out once the
        # corpus has more than a handful of events.
        article_by_id: Dict[str, HubNewsCalendarEventArticle] = {}
        for raw in query_verified_news(as_of=now_instant(), ticker=ticker, market="IN", include_rejected=True, limit=10_000):
            event_id = str(raw.get("event_id") or raw.get("id") or "")
            if not event_id:
                continue
            sources = raw.get("sources") or raw.get("references") or []
            first = sources[0] if sources and isinstance(sources[0], dict) else {}
            article_by_id[event_id] = HubNewsCalendarEventArticle(
                event_id=event_id,
                title=str(raw.get("title") or ""),
                url=str(raw.get("url") or first.get("url") or ""),
                publisher=str(first.get("publisher") or first.get("vendor") or ""),
                source=str(raw.get("source") or first.get("vendor") or ""),
                verification_status=str(raw.get("verification_status") or ""),
                predicted_impact=(raw.get("predicted_impact") or None),
                actual_impact=(raw.get("actual_impact") or None),
            )

        events: List[HubNewsCalendarEvent] = []
        for row in rows:
            event_ids = [str(eid) for eid in (row.get("source_event_ids") or []) if eid]
            articles = [article_by_id[eid] for eid in event_ids if eid in article_by_id]
            events.append(
                HubNewsCalendarEvent(
                    date=str(row.get("date") or ""),
                    event=str(row.get("event") or ""),
                    type=str(row.get("type") or ""),
                    timeline_phrase=str(row.get("timeline_phrase") or ""),
                    date_confidence=str(row.get("date_confidence") or ""),
                    index_impact_mechanism=str(row.get("index_impact_mechanism") or ""),
                    verification_status=str(row.get("verification_status") or ""),
                    fact_check=row.get("fact_check"),
                    articles=articles,
                )
            )

        return HubNewsEventsCalendarResponse(status="ok", events=events)

# Routes below live in src/api/trade_model_adapters_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_model_adapters_routes import (  # noqa: E402,F401
    ModelAdapter,
    ModelAdapterRateLimit,
    ModelAdapterUpdate,
    ModelAdaptersResponse,
    _model_adapter_to_dict,
    get_model_adapters,
    patch_model_adapter,
)


@trade_router.post("/hub/news-pipeline/maintenance", response_model=HubStagingDrainResponse)
def run_hub_news_maintenance_now(
    entity_id: str = "NIFTY",
    lookback_days: int = 365,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Run full entity maintainer: repair, backfill, compact, cleanup, rollup."""
    key = _ticker_key(entity_id, default="")
    with bad_gateway_on_error(logger, "hub news maintenance failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import run_entity_worker_job as run_hub_news_entity_job

        summary = run_hub_news_entity_job(
            {
                "ticker": key,
                "mode": "maintenance",
                "batch_size": 200,
                "lookback_days": max(7, min(lookback_days, 730)),
            }
        )
        if summary.get("pipeline_paused"):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(summary.get("pause_reason") or "News distillation pipeline is paused."),
            )
        if summary.get("had_errors"):
            return HubStagingDrainResponse(
                status="partial",
                summary=summary,
                message="Maintainer finished with one or more stage errors; see summary.had_errors.",
            )
        return HubStagingDrainResponse(status="ok", summary=summary)


@trade_router.post("/hub/news-pipeline/ingest", response_model=HubStagingDrainResponse)
def run_hub_news_ingest_now(
    body: HubNewsIngestRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubStagingDrainResponse:
    """Trigger ingest immediately (full or light mode)."""
    key = _ticker_key(body.ticker)
    mode = (body.mode or "full").strip().lower()
    with bad_gateway_on_error(logger, "hub news ingest now failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import run_hub_news_ingest

        summary = run_hub_news_ingest(
            ticker=key,
            mode=mode,
            sources=body.sources or "default",
            lookback_days=body.lookback_days, market=market_for_ticker(key),
        )
        if summary.get("blocked") or (
            summary.get("pipeline_paused")
            and str(summary.get("pause_reason") or "") == "llm_wiki_unavailable"
        ):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(
                    summary.get("user_message")
                    or summary.get("pause_reason")
                    or "News ingest blocked — LLM-Wiki unavailable."
                ),
            )
        if summary.get("pipeline_paused"):
            return HubStagingDrainResponse(
                status="paused",
                summary=summary,
                message=str(summary.get("pause_reason") or "News distillation pipeline is paused."),
            )
        return HubStagingDrainResponse(status="ok", summary=summary)


@trade_router.get("/hub/factor-registry-gaps", response_model=FactorRegistryGapReviewResponse)
def get_factor_registry_gaps(
    status: str | None = None,
    limit: int = 100,
    _auth: None = Depends(require_local_or_auth),
) -> FactorRegistryGapReviewResponse:
    """Factor names the news attribution pass could not place in `factors.registry`.

    Refreshed at the end of every `process_staging_batch`, so this is a plain read — it does not
    rescan or recompute. `status` filters to pending / accepted / ignored.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import factor_registry_gap_review

        payload = factor_registry_gap_review(status=status, limit=max(1, min(int(limit or 100), 500)))
        return FactorRegistryGapReviewResponse(
            status="ok",
            accepted_means=str(payload.get("accepted_means") or ""),
            counts={str(k): int(v) for k, v in (payload.get("counts") or {}).items()},
            candidates=[FactorRegistryGapCandidate(**row) for row in (payload.get("candidates") or [])],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("factor registry gap review failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/factor-registry-gaps/decision", response_model=FactorRegistryGapDecisionResponse)
def decide_factor_registry_gap_route(
    body: FactorRegistryGapDecisionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> FactorRegistryGapDecisionResponse:
    """Accept, ignore, or reset one registry-gap candidate.

    `accepted` queues the name for a hand-written `FactorSpec`; it does **not** write anything
    into `factors.registry`, which is built from Python source. Decisions are sticky — an
    answered name does not return to `pending` when the pipeline sees it again.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import decide_factor_registry_gap

        entry = decide_factor_registry_gap(
            str(body.candidate or ""),
            str(body.decision or ""),
            note=str(body.note or ""),
        )
        return FactorRegistryGapDecisionResponse(
            status="ok",
            candidate=FactorRegistryGapCandidate(**entry),
        )
    except ValueError as exc:
        # A bad candidate/decision is the caller's error, not a server fault — a 502 here would
        # look like the hub is down when the request was simply malformed.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("factor registry gap decision failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/news/discard", response_model=HubNewsDiscardResponse)
def discard_hub_news(
    body: HubNewsDiscardRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardResponse:
    """Discard one news item or discard similar cluster."""
    key = _ticker_key(body.entity_id)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import (
            discard_news_item,
            discard_similar_items,
            get_distilled_event,
            list_pending_staging_refs,
            now_instant,
            preview_discard_similar,
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
                ev = get_distilled_event(iid, as_of=now_instant())
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
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.post("/hub/news/discard/undo", response_model=HubNewsDiscardResponse)
def undo_hub_news_discard(
    body: HubNewsDiscardUndoRequest,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardResponse:
    """Restore a soft-discarded news item within retention window."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import undo_news_discard

        result = undo_news_discard(str(body.discard_id or "").strip())
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
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/hub/news/discarded", response_model=HubNewsDiscardedListResponse)
def list_hub_discarded_news(
    entity_id: str = "NIFTY",
    limit: int = 50,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsDiscardedListResponse:
    """List soft-discarded news items (30d retention)."""
    key = _ticker_key(entity_id, default="")
    with bad_gateway_on_error(logger, "hub discarded news list failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import list_discarded_news

        items = list_discarded_news(ticker=key, limit=max(1, min(limit, 200)))
        return HubNewsDiscardedListResponse(status="ok", items=items, count=len(items))


@trade_router.get("/hub/news-pipeline/trace/summary", response_model=HubNewsPipelineTraceSummaryResponse)
def get_hub_news_pipeline_trace_summary(
    entity_id: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineTraceSummaryResponse:
    """Per-source and per-stage counts for the Hub news pipeline node diagram."""
    key = _ticker_key(entity_id, default="")
    with bad_gateway_on_error(logger, "hub news pipeline trace summary failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import pipeline_trace_summary

        summary = pipeline_trace_summary(ticker=key)
        return HubNewsPipelineTraceSummaryResponse(status="ok", summary=summary)


@trade_router.get("/hub/news-pipeline/trace/items", response_model=HubNewsPipelineTraceItemsResponse)
def list_hub_news_pipeline_trace_items(
    entity_id: str = "NIFTY",
    source: str = "",
    stage: str = "",
    status: str = "",
    limit: int = 40,
    _auth: None = Depends(require_local_or_auth),
) -> HubNewsPipelineTraceItemsResponse:
    """List traced refs at a given pipeline node (source and/or stage+status)."""
    key = _ticker_key(entity_id, default="")
    with bad_gateway_on_error(logger, "hub news pipeline trace items failed"):
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.dataflows.news_hub_bridge import list_pipeline_trace_items

        items = list_pipeline_trace_items(
            ticker=key,
            source=source or None,
            stage=stage or None,
            status=status or None,
            limit=max(1, min(limit, 200)),
        )
        return HubNewsPipelineTraceItemsResponse(status="ok", items=items, count=len(items))


@trade_router.post("/index-prediction/simulate", response_model=SimulateIndexPredictionResponse)
def simulate_index_prediction(
    body: SimulateIndexPredictionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> SimulateIndexPredictionResponse:
    """What-if: adjust macro factors and recompute index forecast."""
    key = _ticker_key(body.ticker)
    with bad_gateway_on_error(logger, "index-prediction simulate failed for %s", key):
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.cascade.calibration_store import (
            load_calibration_from_doc,
        )
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


@trade_router.get("/index-prediction/playground-context", response_model=IndexPlaygroundContextResponse)
def get_index_playground_context(
    ticker: str = "NIFTY",
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPlaygroundContextResponse:
    """Headlines, events, and ranked factors for the factor impact workbench."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction playground-context failed for %s", key):
        from trade_integrations.context.hub import load_index_research_json
        from trade_integrations.dataflows.index_research.playground_context import (
            doc_as_of_iso,
            load_playground_context,
            resolve_playground_context,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path
        from src.trade.prediction_heavy_pool import run_single_flight

        ensure_trade_stack_path()
        doc = load_index_research_json(key)
        if doc is None:
            return IndexPlaygroundContextResponse(
                status="not_found",
                ticker=key,
                message="Run index analysis first",
            )

        doc_as_of = doc_as_of_iso(doc)[:19]
        if not refresh:
            cached = load_playground_context(key)
            if cached and str(cached.get("as_of") or "")[:19] == doc_as_of:
                return IndexPlaygroundContextResponse(status="ok", ticker=key, context=cached)

        flight_key = f"playground:{key}:{doc_as_of}:refresh={int(refresh)}"
        ctx = run_single_flight(
            flight_key,
            lambda: resolve_playground_context(doc, ticker=key, refresh=refresh),
        )
        return IndexPlaygroundContextResponse(status="ok", ticker=key, context=ctx)


@trade_router.get("/index-prediction/backtest", response_model=IndexBacktestResponse)
def get_index_prediction_backtest(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 500,
    horizon_days: int | None = None,
    include_bottom_up: str = "auto",
    _auth: None = Depends(require_local_or_auth),
) -> IndexBacktestResponse:
    """Load cached walk-forward backtest or recompute from factor history."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction backtest failed for %s", key):
        from trade_integrations.dataflows.index_research.backtest_runner import (
            load_backtest_report,
            run_and_save_backtest,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        backtest_kwargs = {
            "days": days,
            "horizon_days": horizon_days,
            "include_bottom_up": include_bottom_up,
        }
        from src.trade.prediction_heavy_pool import run_single_flight

        if refresh:
            flight_key = (
                f"backtest:{key}:{days}:{horizon_days}:{include_bottom_up}:refresh=1"
            )
            report = run_single_flight(
                flight_key,
                lambda: run_and_save_backtest(**backtest_kwargs),
            )
        else:
            report = load_backtest_report(key)
            if report is None:
                flight_key = (
                    f"backtest:{key}:{days}:{horizon_days}:{include_bottom_up}:cold"
                )
                report = run_single_flight(
                    flight_key,
                    lambda: run_and_save_backtest(**backtest_kwargs),
                )
        status = str(report.get("status") or "ok")
        return IndexBacktestResponse(status=status, ticker=key, report=report)


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
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction forecast-lab failed for %s", key):
        from trade_integrations.dataflows.index_research.prediction_algorithms.api import run_forecast_lab
        from trade_integrations.dataflows.index_research.prediction_algorithms.config import (
            default_combiner_id,
            lab_enabled,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
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
        lab_dict = result.to_dict()
        artifact = None
        persist = payload.get("persist", True)
        if persist is not False:
            from trade_integrations.context.hub import load_index_research_json
            from trade_integrations.dataflows.index_research.prediction_algorithms.pipeline_lab import (
                persist_forecast_lab_to_hub,
            )
            from src.trade.hub_bridge import _index_doc_to_panel

            if persist_forecast_lab_to_hub(key, lab_dict):
                doc = load_index_research_json(key)
                if doc is not None:
                    artifact = _index_doc_to_panel(doc)
                    artifact["asset_type"] = "index"
        return IndexForecastLabResponse(
            status="ok",
            ticker=key,
            result=lab_dict,
            artifact=artifact,
        )

# Routes below live in src/api/trade_track_scoreboard_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_track_scoreboard_routes import (  # noqa: E402,F401
    get_index_track_scoreboard,
    refresh_index_track_scoreboard,
)


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
            ticker=_ticker_key(ticker),
            message="Set INDEX_PREDICTION_EXEC_SIM_ENABLED=1",
        )
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction execution-backtest failed for %s", key):
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


@trade_router.get("/index-prediction/miss-analysis", response_model=IndexMissAnalysisResponse)
def get_index_prediction_miss_analysis(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexMissAnalysisResponse:
    """Load cached prediction miss RCA or recompute from backtest eval rows."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction miss-analysis failed for %s", key):
        from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
            load_miss_analysis_report,
            run_and_save_miss_analysis,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from src.trade.prediction_heavy_pool import run_single_flight

        miss_kwargs = {
            "days": days,
            "horizon_days": horizon_days,
            "ticker": key,
        }
        if refresh:
            flight_key = f"miss-analysis:{key}:{days}:{horizon_days}:refresh=1"
            report = run_single_flight(
                flight_key,
                lambda: run_and_save_miss_analysis(**miss_kwargs),
            )
        else:
            report = load_miss_analysis_report(key)
            if report is None:
                flight_key = f"miss-analysis:{key}:{days}:{horizon_days}:cold"
                report = run_single_flight(
                    flight_key,
                    lambda: run_and_save_miss_analysis(**miss_kwargs),
                )
        status = str(report.get("status") or "ok")
        return IndexMissAnalysisResponse(status=status, ticker=key, report=report)


@trade_router.get("/index-prediction/quant-review", response_model=IndexQuantReviewResponse)
def get_index_quant_review(
    ticker: str = "NIFTY",
    horizon_days: int | None = 14,
    refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> IndexQuantReviewResponse:
    """Load cached India Quant Reviewer artifact (second opinion vs Ridge)."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction quant-review failed for %s", key):
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


@trade_router.get("/index-prediction/data-audit", response_model=IndexDataAuditResponse)
def get_index_prediction_data_audit(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> IndexDataAuditResponse:
    """Load hub data completeness audit for prediction RCA."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction data-audit failed for %s", key):
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


@trade_router.get("/index-prediction/counterfactual", response_model=IndexCounterfactualResponse)
def get_index_prediction_counterfactual(
    ticker: str = "NIFTY",
    refresh: bool = False,
    days: int = 365,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> IndexCounterfactualResponse:
    """Load cached counterfactual decomposition or recompute from backtest eval rows."""
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction counterfactual failed for %s", key):
        from trade_integrations.dataflows.index_research.prediction_counterfactual import (
            load_counterfactual_report,
            run_and_save_counterfactual,
        )
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from src.trade.prediction_heavy_pool import run_single_flight

        cf_kwargs = {
            "days": days,
            "horizon_days": horizon_days,
            "ticker": key,
        }
        if refresh:
            flight_key = f"counterfactual:{key}:{days}:{horizon_days}:refresh=1"
            report = run_single_flight(
                flight_key,
                lambda: run_and_save_counterfactual(**cf_kwargs),
            )
        else:
            report = load_counterfactual_report(key)
            if report is None:
                flight_key = f"counterfactual:{key}:{days}:{horizon_days}:cold"
                report = run_single_flight(
                    flight_key,
                    lambda: run_and_save_counterfactual(**cf_kwargs),
                )
        status = str(report.get("status") or "ok")
        return IndexCounterfactualResponse(status=status, ticker=key, report=report)


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
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "index-prediction news-impact failed for %s", key):
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
                include_rejected=include_rejected, market=market_for_ticker(key),
            )
        else:
            report = news_hub_bridge.resolve_news_impact(
                ticker=key, doc=doc, limit=12, horizon_days=horizon_days, market=market_for_ticker(key)
            )
        status = str((report or {}).get("status") or "ok")
        return IndexNewsImpactResponse(status=status, ticker=key, report=report)


@trade_router.get("/index-prediction/news-impact/factor-timeline")
def get_index_prediction_news_factor_timeline(
    ticker: str = "NIFTY",
    factor_id: str = "",
    shock_pct: float | None = None,
    horizon_days: int = 14,
    _auth: None = Depends(require_local_or_auth),
) -> dict:
    """Single-factor up/down point-impact-over-time timeline for one top-ranked news factor.

    Anchored to the current index doc's spot; no cascading/second-order effects (see
    news_factor_scenario.py's module docstring for why).
    """
    key = _ticker_key(ticker)
    if not factor_id.strip():
        raise HTTPException(status_code=400, detail="factor_id is required")
    with bad_gateway_on_error(logger, "index-prediction news factor-timeline failed for %s/%s", key, factor_id):
        from trade_integrations.dataflows import news_hub_bridge

        result = news_hub_bridge.factor_scenario_timeline(
            factor_id.strip(),
            ticker=key,
            shock_pct=shock_pct,
            horizon_days=horizon_days,
        )
        return result


@trade_router.post("/index-prediction/news-scenarios/session", response_model=NewsScenarioSessionResponse)
def create_news_scenario_session(
    body: NewsScenarioSessionRequest,
    _auth: None = Depends(require_local_or_auth),
) -> NewsScenarioSessionResponse:
    """Create or resume a news-scenario advisor Vibe session bound to pipeline_as_of."""
    key = _ticker_key(body.ticker)
    pipeline_as_of = (body.pipeline_as_of or "").strip()
    if not pipeline_as_of:
        raise HTTPException(status_code=400, detail="pipeline_as_of is required")
    if body.session_id:
        safe_path_component(body.session_id, "session_id")
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
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


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
            from trade_integrations.dataflows.news_hub_bridge import (
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
        from trade_integrations.dataflows.news_hub_bridge import NewsScenarioError

        if isinstance(exc, NewsScenarioError):
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        logger.exception("news-scenario session patch failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc


@trade_router.get("/index-prediction/news-scenarios/recent", response_model=NewsEventScenarioResponse)
def list_news_scenarios(
    ticker: str = "NIFTY",
    limit: int = 10,
    _auth: None = Depends(require_local_or_auth),
) -> NewsEventScenarioResponse:
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "list news scenarios failed for %s", key):
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.news_hub_bridge import (
            list_recent_news_scenarios,
        )

        ensure_trade_stack_path()
        rows = list_recent_news_scenarios(key, limit=limit)
        return NewsEventScenarioResponse(status="ok", ticker=key, scenarios=rows)


@trade_router.get(
    "/index-prediction/news-scenarios/{scenario_id}",
    response_model=NewsEventScenarioResponse,
)
def get_news_scenario(
    scenario_id: str,
    ticker: str = "NIFTY",
    _auth: None = Depends(require_local_or_auth),
) -> NewsEventScenarioResponse:
    key = _ticker_key(ticker)
    with bad_gateway_on_error(logger, "get news scenario failed for %s", scenario_id):
        from src.trade.hub_bridge import ensure_trade_stack_path
        from trade_integrations.dataflows.news_hub_bridge import (
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


_INDEX_PREDICTION_RUN_POLL_SECONDS = 0.5
_INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS = 15.0


def _index_prediction_run_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def _reconcile_and_read_job(
    reconcile: Callable[[str], Any],
    get_record: Callable[[str], dict[str, Any] | None],
    job_id: str,
) -> dict[str, Any] | None:
    """One SSE poll tick of a file-backed job store, run off the event loop thread.

    ``reconcile`` probes the worker PID and may rewrite the job file; ``get_record`` reads
    the job file from disk and takes the store's ``_JOBS_LOCK``. Called from an async
    generator every poll tick per connected client, so on the loop thread either one would
    freeze every request on the process while it ran, ``/health`` included. Same fix as the
    Command Center legs (fork 19f04199).
    """

    def _poll() -> dict[str, Any] | None:
        reconcile(job_id)
        return get_record(job_id)

    return await asyncio.to_thread(_poll)


async def _sse_event_stream(
    *,
    job_id: str,
    request: Request,
    reconcile: Callable[[str], Any],
    get_record: Callable[[str], dict[str, Any] | None],
    frame: Callable[[str, dict[str, Any]], str],
    log_to_events: Callable[[dict[str, Any]], "list[tuple[str, dict[str, Any]]]"],
    terminal: Callable[[dict[str, Any]], "tuple[str, dict[str, Any]] | None"],
    heartbeat: Callable[[str, str], str],
    heartbeat_seconds: float,
    poll_seconds: float,
):
    """Generic poll-loop skeleton shared by the three job-store SSE endpoints
    (index-prediction run, external-predictions refresh, recording): replay
    buffered logs off the last-seen index, then poll the job store until
    ``terminal(job)`` reports a frame to emit and end the stream.

    Genuinely different per caller behavior stays caller-owned via hooks
    rather than being forced into one shape here: ``log_to_events`` (external
    predictions turns a ``source_complete`` log entry into a differently-
    shaped event instead of a raw ``log`` frame), ``terminal`` (each system's
    own done/error payload field name and status set), and ``heartbeat``
    (index-prediction/recording emit a bare SSE comment, external-predictions
    emits a real named ``heartbeat`` event with a JSON body).
    """
    import time as time_mod

    last_log_idx = 0
    last_emit = time_mod.monotonic()
    while True:
        if await request.is_disconnected():
            return

        job = await _reconcile_and_read_job(reconcile, get_record, job_id)
        if job is None:
            yield frame("error", {"message": "job not found"})
            return

        logs = list(job.get("logs") or [])
        while last_log_idx < len(logs):
            for event, data in log_to_events(logs[last_log_idx]):
                yield frame(event, data)
            last_log_idx += 1
            last_emit = time_mod.monotonic()

        done = terminal(job)
        if done is not None:
            event, data = done
            yield frame(event, data)
            return

        if time_mod.monotonic() - last_emit >= heartbeat_seconds:
            yield heartbeat(job_id, str(job.get("status") or ""))
            last_emit = time_mod.monotonic()

        await asyncio.sleep(poll_seconds)

# Routes below live in src/api/trade_external_predictions_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_external_predictions_routes import (  # noqa: E402,F401
    _external_predictions_heartbeat,
    _external_predictions_log_to_events,
    _external_predictions_refresh_event_stream,
    _external_predictions_refresh_stream_response,
    _external_predictions_terminal,
    _kick_external_predictions_refresh,
    add_external_prediction_source,
    approve_external_prediction_path,
    discover_external_prediction_sources,
    get_active_external_predictions_refresh,
    get_external_prediction_thumbnail,
    get_external_predictions,
    get_external_predictions_refresh_job,
    list_external_prediction_sources,
    refresh_external_predictions,
    remove_external_prediction_source,
    start_external_predictions_refresh,
    stream_external_predictions_refresh_job,
)


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
    key = _ticker_key(ticker)
    since, until, day = _query_window(since, until, day)
    row_limit = _query_limit(limit, maximum=100)

    def _csv(raw: str | None) -> list[str] | None:
        if not raw:
            return None
        return [part.strip() for part in raw.split(",") if part.strip()]

    with bad_gateway_on_error(logger, "index-prediction verified-news failed for %s", key):
        from trade_integrations.dataflows import news_hub_bridge
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        inv = news_hub_bridge.tag_inventory(ticker=key) if inventory else None
        items = news_hub_bridge.query_verified_news(
            as_of=news_hub_bridge.now_instant(),
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
            limit=row_limit,
        )
        items = [news_hub_bridge.without_legacy_forecast(item) for item in items]  # D410: one honesty rule, not a copy
        return IndexVerifiedNewsResponse(
            status="ok",
            ticker=key,
            count=len(items),
            items=items,
            inventory=inv,
        )


@trade_router.get("/index-prediction/day-attribution", response_model=DayAttributionResponse)
def get_index_day_attribution(
    date: str,
    days: int = 365,
    _auth: None = Depends(require_local_or_auth),
) -> DayAttributionResponse:
    """Explain factor and calendar drivers for one Nifty trading day."""
    with bad_gateway_on_error(logger, "day-attribution failed for %s", date):
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



def _index_prediction_run_log_to_events(entry: dict[str, Any]) -> "list[tuple[str, dict[str, Any]]]":
    return [("log", {"entry": entry})]


def _index_prediction_run_terminal(job: dict[str, Any]) -> "tuple[str, dict[str, Any]] | None":
    status = str(job.get("status") or "")
    if status in ("done", "done_with_warnings"):
        artifact = job.get("artifact")
        ticker = str(job.get("ticker") or "")
        if artifact is not None:
            return (
                "done",
                {"ticker": ticker, "artifact": artifact, "warnings": job.get("warnings") or []},
            )
        return ("error", {"message": "job completed without artifact"})
    if status == "error":
        return ("error", {"message": job.get("error") or "unknown error"})
    return None


def _index_prediction_run_heartbeat(job_id: str, status: str) -> str:
    return ": keepalive\n\n"


async def _index_prediction_run_event_stream(job_id: str, request: Request):
    """Replay stored logs then poll job store until done/error."""
    from src.trade.index_prediction_run_jobs import _get_job_record, reconcile_job

    async for chunk in _sse_event_stream(
        job_id=job_id,
        request=request,
        reconcile=reconcile_job,
        get_record=_get_job_record,
        frame=_index_prediction_run_sse_frame,
        log_to_events=_index_prediction_run_log_to_events,
        terminal=_index_prediction_run_terminal,
        heartbeat=_index_prediction_run_heartbeat,
        heartbeat_seconds=_INDEX_PREDICTION_RUN_HEARTBEAT_SECONDS,
        poll_seconds=_INDEX_PREDICTION_RUN_POLL_SECONDS,
    ):
        yield chunk


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


_COMMAND_CENTER_POSITIONS_POLL_SECONDS = 5.0
_COMMAND_CENTER_PREDICTION_POLL_SECONDS = 20.0
_COMMAND_CENTER_NEWS_POLL_SECONDS = 20.0
_COMMAND_CENTER_HEARTBEAT_SECONDS = 15.0
_COMMAND_CENTER_TICK_SECONDS = 1.0


def _command_center_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _command_center_snapshot_hash(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)


async def _command_center_event_stream(agent_id: str, ticker: str, request: Request):
    """Server-push replacement for Command Center's client-side poll timers +
    manual refresh buttons — per user request (see
    [[2026-08-28-command-center-real-time-push]]). Each of the 3 data legs (positions,
    prediction, news) is polled server-side on its own cadence — matching this module's
    established job-store-poll-as-SSE pattern (see `_index_prediction_run_event_stream`
    above) since none of positions/prediction/news has a real change-notification hook to
    piggyback on (confirmed while scoping: positions is recomputed fresh from the broker per
    call with no cache; the prediction artifact is written by a scheduled pipeline with no
    publish hook; news has no pub/sub either) — but a snapshot is only ever emitted to the
    client when it actually differs from the last one sent, so this isn't just polling
    relabeled as push: an unchanged tick produces no frame, no re-render, no flicker.
    """
    import time as time_mod

    from nautilus_openalgo_bridge.live_pop import compute_live_pop_for_agent

    last_positions_snapshot: str | None = None
    last_prediction_snapshot: str | None = None
    last_news_snapshot: str | None = None
    last_positions_poll = 0.0
    last_prediction_poll = 0.0
    last_news_poll = 0.0
    last_emit = time_mod.monotonic()

    while True:
        if await request.is_disconnected():
            return
        now = time_mod.monotonic()

        if agent_id and now - last_positions_poll >= _COMMAND_CENTER_POSITIONS_POLL_SECONDS:
            last_positions_poll = now
            try:
                positions = await asyncio.to_thread(compute_live_pop_for_agent, agent_id)
                snapshot = _command_center_snapshot_hash(positions)
                if snapshot != last_positions_snapshot:
                    last_positions_snapshot = snapshot
                    yield _command_center_sse_frame("positions", positions)
                    last_emit = now
            except Exception as exc:  # noqa: BLE001 — one leg's failure must not kill the stream
                logger.warning("command-center stream: positions poll failed", exc_info=exc)
                yield _command_center_sse_frame("positions_error", {"message": redacted_error_detail(exc)})
                last_emit = now

        if now - last_prediction_poll >= _COMMAND_CENTER_PREDICTION_POLL_SECONDS:
            last_prediction_poll = now
            try:
                from src.trade.hub_bridge import load_hub_plan_artifact

                def _load_prediction() -> Any:
                    artifact = load_hub_plan_artifact(ticker, "index")
                    _attach_latest_forecast_fan(artifact, ticker)
                    return artifact

                artifact = await asyncio.to_thread(_load_prediction)
                snapshot = _command_center_snapshot_hash(artifact)
                if snapshot != last_prediction_snapshot:
                    last_prediction_snapshot = snapshot
                    yield _command_center_sse_frame(
                        "prediction", {"status": "ok", "ticker": ticker, "artifact": artifact}
                    )
                    last_emit = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("command-center stream: prediction poll failed", exc_info=exc)
                yield _command_center_sse_frame("prediction_error", {"message": redacted_error_detail(exc)})
                last_emit = now

        if now - last_news_poll >= _COMMAND_CENTER_NEWS_POLL_SECONDS:
            last_news_poll = now
            try:
                from trade_integrations.context.hub import load_index_research_json
                from trade_integrations.dataflows import news_hub_bridge

                def _load_news() -> Any:
                    doc = load_index_research_json(ticker)
                    return news_hub_bridge.resolve_news_impact(
                        ticker=ticker,
                        doc=doc,
                        limit=12,
                        horizon_days=7,
                        force_rebuild=True,
                        market=market_for_ticker(ticker),
                    )

                report = await asyncio.to_thread(_load_news)
                snapshot = _command_center_snapshot_hash(report)
                if snapshot != last_news_snapshot:
                    last_news_snapshot = snapshot
                    yield _command_center_sse_frame("news", {"status": "ok", "ticker": ticker, "report": report})
                    last_emit = now
            except Exception as exc:  # noqa: BLE001
                logger.warning("command-center stream: news poll failed", exc_info=exc)
                yield _command_center_sse_frame("news_error", {"message": redacted_error_detail(exc)})
                last_emit = now

        if now - last_emit >= _COMMAND_CENTER_HEARTBEAT_SECONDS:
            yield ": keepalive\n\n"
            last_emit = now

        await asyncio.sleep(_COMMAND_CENTER_TICK_SECONDS)


@trade_router.get("/command-center/stream", dependencies=[Depends(require_event_stream_auth)])
async def command_center_stream(
    request: Request,
    agent_id: str = "",
    ticker: str = "NIFTY",
) -> StreamingResponse:
    """SSE: push positions/prediction/news updates to the Command Center dashboard as they
    change, so the page never needs a manual refresh button or client-side poll timer."""
    key = _ticker_key(ticker)
    return StreamingResponse(
        _command_center_event_stream(agent_id.strip(), key, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _kick_index_prediction_run(body: RunIndexPredictionRequest) -> tuple[str, str, bool]:
    from src.trade.index_prediction_run_jobs import _STORE, spawn_worker, start_job

    key = _ticker_key(body.ticker)
    job_id, reused = start_job(
        ticker=key,
        horizon_days=body.horizon_days,
        refresh_constituents=body.refresh_constituents,
        run_forecast_lab=body.run_forecast_lab,
    )
    _STORE.ensure_worker(job_id, reused, spawn_worker)
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


@trade_router.post("/index-prediction/run/{job_id}/cancel")
def cancel_index_prediction_run(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, str]:
    """Request cooperative cancel for an in-flight manual run."""
    from trade_integrations.dataflows.index_research.pipeline_cancel import request_pipeline_cancel
    from src.trade.index_prediction_run_jobs import _ACTIVE_STATUSES, _get_job_record, _now_iso, append_log, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    job = _get_job_record(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    status = str(job.get("status") or "")
    if status not in _ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail=f"job is not active (status={status})")
    request_pipeline_cancel(f"user_cancel:{job_id}", job_id=job_id)
    append_log(
        job_id,
        {
            "stage": "cancel",
            "message": "Cancel requested — stopping pipeline…",
            "level": "warn",
            "at": _now_iso(),
        },
    )
    return {"status": "ok", "message": "cancel requested"}


@trade_router.get("/index-prediction/run/{job_id}/stream")
async def stream_index_prediction_run_job(
    job_id: str,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """SSE: replay pipeline logs and stream until the run terminates."""
    from src.trade.index_prediction_run_jobs import _get_job_record, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    if await asyncio.to_thread(_get_job_record, job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return _index_prediction_run_stream_response(job_id, request)

# Routes below live in src/api/trade_recording_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_recording_routes import (  # noqa: E402,F401
    ArmMultiMarketRequest,
    SeekMultiMarketRequest,
    SetMultiMarketSpeedRequest,
    StartMarketTickRecordingRequest,
    _KNOWN_RECORDING_CATEGORIES,
    _RECORDING_HEARTBEAT_SECONDS,
    _RECORDING_POLL_SECONDS,
    _VALID_HISTORICAL_INTERVALS_API,
    _kick_recording,
    _normalize_recording_request,
    _recording_event_stream,
    _recording_heartbeat,
    _recording_log_to_events,
    _recording_sse_frame,
    _recording_stream_response,
    _recording_terminal,
    _run_control,
    _simulator_client,
    _simulator_not_configured,
    _validate_recording_payload,
    arm_multi_market,
    get_active_recording,
    get_auto_record,
    get_market_bundle,
    get_market_economy_factor,
    get_market_factor_coverage,
    get_market_flow_of_funds,
    get_market_global_macro,
    get_market_global_macro_live_spot,
    get_market_global_macro_ui_cards,
    get_market_index_history,
    get_market_live_spot,
    get_market_policy_factors,
    get_market_replay_calendar,
    get_market_sector_indices,
    get_market_top_constituents,
    get_markets_registry,
    get_multi_market_quote,
    get_multi_market_status,
    get_recording_constituents,
    get_recording_job,
    get_replay_calendar,
    get_replay_status,
    list_market_global_macro_refreshable_series,
    list_market_tick_recordings,
    list_recording_sessions,
    pause_multi_market,
    pause_replay,
    refresh_market_global_macro,
    resume_multi_market,
    resume_replay,
    seek_multi_market,
    seek_replay,
    set_auto_record,
    set_multi_market_speed,
    set_replay_speed,
    start_market_tick_recording,
    start_recording,
    start_replay,
    stop_market_tick_recording,
    stop_multi_market,
    stop_recording,
    stop_replay,
    stream_recording_job,
)

# Routes below live in src/api/trade_refresh_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_refresh_routes import (  # noqa: E402,F401
    _LIGHT_REFRESH_LOCK,
    _light_refresh_skip_reason,
    get_index_prediction_refresh_gate,
    refresh_index_prediction,
)


@trade_router.get("/index-prediction/history", response_model=IndexPredictionHistoryResponse)
def get_index_prediction_history(
    ticker: str = "NIFTY",
    limit: int = 50,
    horizon_days: int | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexPredictionHistoryResponse:
    """Prediction-ledger rows (last run per day + today's intraday revisions) for the ledger-only columns
    (bottom-up / macro / direction hit); the headline forecast history is read from /forecast-replay."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.prediction_ledger_bridge import list_forecast_history_bundle

        bundle = list_forecast_history_bundle(
            key,
            limit=max(1, min(limit, 200)),
            horizon_days=horizon_days,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("index-prediction history failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
    return IndexPredictionHistoryResponse(
        status="ok",
        ticker=key,
        daily=bundle["daily"],
        intraday=bundle.get("intraday") or [],
        meta=bundle.get("meta") or {},
    )


_FORECAST_REPLAY_MAX_DAYS = 400
_FORECAST_REPLAY_MAX_SOURCES = 40
_FORECAST_REPLAY_MAX_HORIZON = 366
_FORECAST_REPLAY_PROVENANCE = ("live", "replay", "backtest")


def _csv(value: str | None) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in (value or "").split(",") if v.strip()))


@trade_router.get("/index-prediction/forecast-replay", response_model=ForecastReplayResponse)
def get_index_prediction_forecast_replay(
    ticker: str = "NIFTY",
    sources: str | None = None,
    start: str | None = None,
    end: str | None = None,
    horizon: int = 14,
    provenance: str = "live",
    revisions: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastReplayResponse:
    """Every forecast source's records joined to realised closes (read-only; one bad family is a 200)."""
    sent = (ticker or "NIFTY").strip()  # an unsupported ticker is echoed in the 422 exactly as sent
    key = sent.upper()
    try:
        end_d = datetime.strptime(end, "%Y-%m-%d").date() if end else datetime.now(timezone.utc).date()
        start_d = datetime.strptime(start, "%Y-%m-%d").date() if start else end_d - timedelta(days=90)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"start/end must be YYYY-MM-DD: {exc}") from exc
    if start_d > end_d:
        raise HTTPException(status_code=422, detail=f"start {start_d} is after end {end_d}")
    if (end_d - start_d).days > _FORECAST_REPLAY_MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"window is limited to {_FORECAST_REPLAY_MAX_DAYS} days")
    if not 1 <= horizon <= _FORECAST_REPLAY_MAX_HORIZON:
        raise HTTPException(status_code=422, detail=f"horizon must be 1..{_FORECAST_REPLAY_MAX_HORIZON}")
    source_ids = _csv(sources)
    if len(source_ids) > _FORECAST_REPLAY_MAX_SOURCES:
        raise HTTPException(status_code=422, detail=f"at most {_FORECAST_REPLAY_MAX_SOURCES} sources per request")
    prov = _csv(provenance) or ["live"]
    if bad := [p for p in prov if p not in _FORECAST_REPLAY_PROVENANCE]:
        raise HTTPException(status_code=422, detail=f"unknown provenance {bad}; valid: {list(_FORECAST_REPLAY_PROVENANCE)}")
    try:
        from trade_integrations.dataflows.prediction_ledger_bridge import build_forecast_replay, forecast_replay_error_text

        bundle = build_forecast_replay(
            sent, source_ids or None, start_d.isoformat(), end_d.isoformat(), horizon, prov, revisions
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=forecast_replay_error_text(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=forecast_replay_error_text(exc)) from exc
    except Exception as exc:
        logger.exception("forecast replay failed for %s", key)
        raise HTTPException(status_code=502, detail=forecast_replay_error_text(exc)) from exc
    return ForecastReplayResponse(status="ok", ticker=key, **bundle)


@trade_router.get("/index-prediction/factor-history", response_model=IndexFactorHistoryResponse)
def get_index_factor_history(
    ticker: str = "NIFTY",
    days: int = 90,
    start: str | None = None,
    factors: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> IndexFactorHistoryResponse:
    """Return macro factor time series for historical charts."""
    key = _ticker_key(ticker)
    try:
        from trade_integrations.dataflows.prediction_ledger_bridge import (
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
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return IndexFactorHistoryResponse(
        status="ok",
        ticker=key,
        series=payload.get("series", []),
        factors=payload.get("factors", []),
        coverage=payload.get("coverage") or {},
        coverage_notes=payload.get("coverage_notes") or [],
    )

# Routes below live in src/api/trade_forecast_engine_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_forecast_engine_routes import (  # noqa: E402,F401
    _FORECAST_ENGINE_RUN_HEARTBEAT_SECONDS,
    _FORECAST_ENGINE_RUN_POLL_SECONDS,
    _forecast_engine_run_event_stream,
    _forecast_engine_run_heartbeat,
    _forecast_engine_run_log_to_events,
    _forecast_engine_run_sse_frame,
    _forecast_engine_run_terminal,
    get_forecast_engine_evaluation,
    get_forecast_engine_latest,
    get_forecast_engine_recipes,
    get_forecast_engine_run_detail,
    get_forecast_engine_run_job,
    get_forecast_engine_runs,
    start_forecast_engine_run,
    stream_forecast_engine_run_job,
)


@trade_router.get("/index-prediction/constituent-history", response_model=ConstituentHistoryResponse)
def get_constituent_history(
    symbol: str,
    days: int = 90,
    weight: float | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> ConstituentHistoryResponse:
    """Return archived company research trend for one Nifty constituent."""
    key = _ticker_key(symbol, default="")
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
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return ConstituentHistoryResponse(
        status="ok",
        symbol=payload.get("symbol", key),
        days=int(payload.get("days") or days),
        snapshot_count=int(payload.get("snapshot_count") or 0),
        has_research_archive=bool(payload.get("has_research_archive")),
        points=payload.get("points") or [],
    )

# Routes below live in src/api/trade_prediction_jobs_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_prediction_jobs_routes import (  # noqa: E402,F401
    _HISTORICAL_UPCOMING_EVENT_FIELDS,
    cancel_index_prediction_job_route,
    get_index_prediction_jobs,
    get_index_prediction_snapshots,
    get_index_prediction_upcoming_events_history,
    pause_index_prediction_job,
    resume_index_prediction_job_route,
    trigger_index_prediction_job_route,
)


@trade_router.get("/agent-debate", response_model=AgentDebateResponse)
def get_agent_debate(
    ticker: str,
    _auth: None = Depends(require_local_or_auth),
) -> AgentDebateResponse:
    """Load cached TradingAgents debate summary from the hub."""
    key = _ticker_key(ticker, default="")
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
    key = _ticker_key(body.ticker, default="")
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
                                {"ticker": key, "status": "error", "message": redacted_error_detail(exc)},
                            )
                    except Exception:
                        logger.exception("could not relay the run-debate failure for %s to session %s", key, session_id)

        threading.Thread(target=_worker, daemon=True, name=f"debate-{key}").start()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("run-debate failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
    return AgentDebateResponse(status="running", ticker=key, running=True)


# ============================================================
# /trade/hub/{market-data,macro-factors,index-history,constituents}/
# ============================================================
# All endpoints are thin pass-throughs to dataflows.stock_history_bridge.
# The bridge is the single source of truth for in-process Python callers;
# HTTP is for cross-process callers (Vibe frontend).
# Routes below live in src/api/trade_hub_market_data_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_hub_market_data_routes import (  # noqa: E402,F401
    HubConstituentsPanelResponse,
    HubIndexHistoryBarsResponse,
    HubIndexHistoryDaysResponse,
    HubIndexHistoryExpiriesResponse,
    HubMacroFactorDatesResponse,
    HubMacroFactorLatestResponse,
    HubMacroFactorPanelResponse,
    HubMarketDataOptionChainResponse,
    HubMarketDataOptionChainStrike,
    HubMarketDataOptionExpiriesResponse,
    HubMarketDataSpotResponse,
    HubMarketDataTicksResponse,
    HubReplayDayOverviewResponse,
    HubStockHistoryCoverageResponse,
    _safe_float,
    _spot_unavailable_message,
    hub_constituents_panel,
    hub_index_history_bars,
    hub_index_history_days,
    hub_index_history_expiries,
    hub_macro_factor_dates,
    hub_macro_factor_latest,
    hub_macro_factor_panel,
    hub_market_data_option_chain,
    hub_market_data_option_expiries,
    hub_market_data_spot,
    hub_market_data_ticks,
    hub_replay_day_overview,
    hub_stock_history_coverage,
)

# Routes below live in src/api/trade_stock_history_backfill_and_log_routes.py. The import sits here so they register on trade_router
# in their original order; the names are re-exported because callers and tests patch/import them from trade_routes.
from .trade_stock_history_backfill_and_log_routes import (  # noqa: E402,F401
    HubStockHistoryBackfillRunRequest,
    _OPENALGO_JOB_ID_RE,
    _OPENALGO_LOG_SOURCES,
    _SIM_LOG_KEY_RE,
    _backfill_run_call,
    _relay_log_stream,
    hub_stock_history_backfill_run_active,
    hub_stock_history_backfill_run_get,
    hub_stock_history_backfill_run_start,
    openalgo_log_stream,
    stock_simulator_log_stream,
)


@trade_router.post("/hub/stock-history/backfill-runs/{run_id}/cancel")
def hub_stock_history_backfill_run_cancel(
    run_id: str, _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _backfill_run_call("cancel_backfill_run", run_id)


def register_trade_and_watch_routes(app) -> None:
    """Mount the trade, trading-connector, and watch routers onto ``app``."""
    from src.api.trading_connector_routes import router as trading_connector_router
    from src.api.watch_routes import watch_router

    app.include_router(trade_router)
    app.include_router(trading_connector_router)
    app.include_router(watch_router)
