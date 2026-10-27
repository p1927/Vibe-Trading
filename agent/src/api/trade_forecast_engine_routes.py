"""Forecast-engine routes: recipes, latest artifact, evaluation, run start/job/stream/list/detail. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

import asyncio
import json
from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.api.helpers import redacted_error_detail
from src.api.security import require_local_or_auth
from typing import Any
from .trade_routes import ForecastEngineArtifactResponse, ForecastEngineEvaluationResponse, ForecastEngineRecipeInfo, ForecastEngineRecipesResponse, ForecastEngineRunJobResponse, ForecastEngineRunJobSnapshot, ForecastEngineRunListResponse, ForecastEngineRunStartRequest, ForecastEngineRunStartResponse, ForecastEngineRunSummary, _sse_event_stream, _ticker_key, logger, trade_router


@trade_router.get("/index-prediction/forecast-engine-recipes", response_model=ForecastEngineRecipesResponse)
def get_forecast_engine_recipes(
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRecipesResponse:
    """Every forecast_engine recipe registered in `forecast_engine.registry` (backend-defined,
    docs/add/forecast_engine.md) -- what the UI's dataset selector lists."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME, RECIPES

        recipes = [
            ForecastEngineRecipeInfo(
                name=r.name,
                version=r.version,
                covariate_keys=list(r.covariate_keys),
                target=r.target,
                description=r.description,
            )
            for r in RECIPES.values()
        ]
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-recipes failed")
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return ForecastEngineRecipesResponse(status="ok", recipes=recipes, default_recipe=DEFAULT_RECIPE_NAME)


@trade_router.get("/index-prediction/forecast-engine-latest", response_model=ForecastEngineArtifactResponse)
def get_forecast_engine_latest(
    ticker: str = "NIFTY",
    recipe: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineArtifactResponse:
    """The forecast_engine module's cached latest forecast for one recipe
    (docs/add/forecast_engine.md) -- dataset covariates + a TimesFM point/quantile forecast,
    refreshed offline by scripts/run_forecast_engine_latest.py. `recipe` defaults to the
    registry's default recipe when omitted. `available=False` (not a 404/502) when no artifact
    has been generated yet -- this is an expected, normal state, not a service failure."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import read_latest_forecast
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        artifact = read_latest_forecast(key, recipe_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-latest failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return ForecastEngineArtifactResponse(
            status="ok", available=False, ticker=key, recipe_name=recipe_name, message="no artifact yet"
        )

    return ForecastEngineArtifactResponse(
        status="ok",
        available=True,
        ticker=artifact.ticker,
        recipe_name=artifact.recipe_name,
        recipe_version=artifact.recipe_version,
        engine_checkpoint=artifact.engine_checkpoint,
        horizon_days=artifact.horizon_days,
        as_of=artifact.as_of,
        generated_at=artifact.generated_at,
        spot=artifact.spot,
        expected_return_pct=artifact.expected_return_pct,
        quantiles_pct=artifact.quantiles_pct,
        dataset=artifact.dataset,
        covariate_keys=artifact.covariate_keys,
    )


@trade_router.get("/index-prediction/forecast-engine-evaluation", response_model=ForecastEngineEvaluationResponse)
def get_forecast_engine_evaluation(
    ticker: str = "NIFTY",
    recipe: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineEvaluationResponse:
    """One recipe's walk-forward backtest history -- predicted vs actual NIFTY over past forecast
    cutoffs (docs/add/forecast_engine.md rule 5), refreshed offline by the same job as the latest
    forecast. `available=False` when no evaluation has been run for this recipe yet."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import read_evaluation_artifact
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        artifact = read_evaluation_artifact(key, recipe_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-evaluation failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return ForecastEngineEvaluationResponse(
            status="ok", available=False, ticker=key, recipe_name=recipe_name, message="no evaluation yet"
        )

    return ForecastEngineEvaluationResponse(
        status="ok",
        available=True,
        ticker=artifact.ticker,
        recipe_name=artifact.recipe_name,
        recipe_version=artifact.recipe_version,
        engine_checkpoint=artifact.engine_checkpoint,
        horizon_days=artifact.horizon_days,
        generated_at=artifact.generated_at,
        n_observations=artifact.n_observations,
        n_skipped_gaps=artifact.n_skipped_gaps,
        mean_pinball_loss=artifact.mean_pinball_loss,
        mean_naive_pinball_loss=artifact.mean_naive_pinball_loss,
        passes_gate=artifact.passes_gate,
        gate_lower_bound=artifact.gate_lower_bound,
        observations=artifact.observations,
    )


@trade_router.post(
    "/index-prediction/forecast-engine-run/start",
    response_model=ForecastEngineRunStartResponse,
    status_code=202,
)
def start_forecast_engine_run(
    body: ForecastEngineRunStartRequest,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRunStartResponse:
    """Queue an on-demand forecast_engine evaluation run over a user-chosen date range and
    return a trackable job_id + the run_id it will be filed under once done."""
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from src.trade.forecast_engine_run_jobs import kick_forecast_engine_run

    job_id, run_id, job_status, reused = kick_forecast_engine_run(
        ticker=body.ticker, recipe=body.recipe, start=body.start, end=body.end, horizon=body.horizon
    )
    return ForecastEngineRunStartResponse(job_id=job_id, run_id=run_id, job_status=job_status, reused=reused)


@trade_router.get(
    "/index-prediction/forecast-engine-run/{job_id}", response_model=ForecastEngineRunJobResponse
)
def get_forecast_engine_run_job(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRunJobResponse:
    from src.trade.forecast_engine_run_jobs import get_job, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    snap = get_job(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return ForecastEngineRunJobResponse(job=ForecastEngineRunJobSnapshot(**snap))


_FORECAST_ENGINE_RUN_POLL_SECONDS = 0.5
_FORECAST_ENGINE_RUN_HEARTBEAT_SECONDS = 15.0


def _forecast_engine_run_sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _forecast_engine_run_log_to_events(entry: dict[str, Any]) -> "list[tuple[str, dict[str, Any]]]":
    return [("log", {"entry": entry})]


def _forecast_engine_run_terminal(job: dict[str, Any]) -> "tuple[str, dict[str, Any]] | None":
    status = str(job.get("status") or "")
    if status == "done":
        return ("done", {"result": job.get("result")})
    if status == "error":
        return ("error", {"message": job.get("error") or "unknown error"})
    return None


def _forecast_engine_run_heartbeat(job_id: str, status: str) -> str:
    return ": keepalive\n\n"


async def _forecast_engine_run_event_stream(job_id: str, request: Request):
    """Replay stored logs then poll job store until done/error."""
    from src.trade.forecast_engine_run_jobs import _get_job_record, reconcile_job

    async for chunk in _sse_event_stream(
        job_id=job_id,
        request=request,
        reconcile=reconcile_job,
        get_record=_get_job_record,
        frame=_forecast_engine_run_sse_frame,
        log_to_events=_forecast_engine_run_log_to_events,
        terminal=_forecast_engine_run_terminal,
        heartbeat=_forecast_engine_run_heartbeat,
        heartbeat_seconds=_FORECAST_ENGINE_RUN_HEARTBEAT_SECONDS,
        poll_seconds=_FORECAST_ENGINE_RUN_POLL_SECONDS,
    ):
        yield chunk


@trade_router.get("/index-prediction/forecast-engine-run/{job_id}/stream")
async def stream_forecast_engine_run_job(
    job_id: str,
    request: Request,
    _auth: None = Depends(require_local_or_auth),
) -> StreamingResponse:
    """SSE: replay run logs and stream until the run terminates."""
    from src.trade.forecast_engine_run_jobs import _get_job_record, job_id_valid

    if not job_id_valid(job_id):
        raise HTTPException(status_code=400, detail="invalid job_id")
    if await asyncio.to_thread(_get_job_record, job_id) is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return StreamingResponse(
        _forecast_engine_run_event_stream(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@trade_router.get("/index-prediction/forecast-engine-runs", response_model=ForecastEngineRunListResponse)
def get_forecast_engine_runs(
    ticker: str = "NIFTY",
    recipe: str = "",
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineRunListResponse:
    """Past named runs for one recipe -- newest first."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import list_runs
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        runs = list_runs(key, recipe_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-runs failed for %s", key)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    return ForecastEngineRunListResponse(runs=[ForecastEngineRunSummary(**r) for r in runs])


@trade_router.get(
    "/index-prediction/forecast-engine-run-detail", response_model=ForecastEngineEvaluationResponse
)
def get_forecast_engine_run_detail(
    ticker: str = "NIFTY",
    recipe: str = "",
    run_id: str = "",
    _auth: None = Depends(require_local_or_auth),
) -> ForecastEngineEvaluationResponse:
    """One past run's full evaluation detail -- same field shape as
    ``/index-prediction/forecast-engine-evaluation`` (the "latest" evaluation), just for a named
    historical run instead of the always-overwritten latest one."""
    key = _ticker_key(ticker)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import read_run_artifact
        from trade_integrations.forecast_engine.registry import DEFAULT_RECIPE_NAME

        recipe_name = (recipe or DEFAULT_RECIPE_NAME).strip()
        artifact = read_run_artifact(key, recipe_name, run_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("forecast-engine-run-detail failed for %s/%s", key, run_id)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc

    if artifact is None:
        return ForecastEngineEvaluationResponse(
            status="ok", available=False, ticker=key, recipe_name=recipe_name, message="run not found"
        )

    return ForecastEngineEvaluationResponse(
        status="ok",
        available=True,
        ticker=artifact.ticker,
        recipe_name=artifact.recipe_name,
        recipe_version=artifact.recipe_version,
        engine_checkpoint=artifact.engine_checkpoint,
        horizon_days=artifact.horizon_days,
        generated_at=artifact.generated_at,
        n_observations=artifact.n_observations,
        n_skipped_gaps=artifact.n_skipped_gaps,
        mean_pinball_loss=artifact.mean_pinball_loss,
        mean_naive_pinball_loss=artifact.mean_naive_pinball_loss,
        passes_gate=artifact.passes_gate,
        gate_lower_bound=artifact.gate_lower_bound,
        observations=artifact.observations,
    )
