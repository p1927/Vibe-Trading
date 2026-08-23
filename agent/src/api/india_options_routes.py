"""Fork-only India options routes/dispatch — sidecar for ``options_routes.py``.

``options_routes.py`` is an upstream-owned file (inherited from
``HKUDS/Vibe-Trading``'s Options Lab feature). Per this repo's fork
conventions (``docs/FORK_CONVENTIONS.md``), fork-only behavior belongs in a
file the fork owns, with at most a single import + call site left in the
upstream file, so future upstream syncs to ``options_routes.py`` stay close
to a fast-forward instead of conflicting with India-specific logic woven
into its route bodies.

Exposes:

- ``fetch_india_chain(...)`` — the whole ``market="india_equity"`` branch of
  ``GET /options/chain``, called with one line from ``options_chain()``.
- ``register_india_options_routes(app, require_auth)`` — registers
  ``GET /options/research`` (the ranked-strategies endpoint, entirely new
  and fork-only), called once from ``register_options_routes()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse, Response

from src.tools.india_options_chain_tool import IndiaOptionsChainTool, list_india_underlyings
from src.tools.india_options_research_tool import IndiaOptionsResearchTool

logger = logging.getLogger(__name__)

AuthDep = Callable[..., Awaitable[Any] | Any]

# The options_research pipeline (aggregator.py) runs several sequential
# external-data stages (chain, events via nselib, analytics via yfinance/
# qfinindia) — the chain stage has its own bounded timeout, but the others
# call vendor libraries with no explicit HTTP timeout at all, so a slow/
# unreachable vendor there stalls the whole pipeline with no ceiling. Rather
# than auditing/patching every vendor call individually, bound the entire
# request here: a caller always gets an explicit response within this
# ceiling regardless of which internal stage is stuck.
#
# 180s, not something tighter: a real successful run (all stages reachable,
# no vendor slowness) measured at 156.5s end to end — the non-chain stages
# dominate, not the chain fetch (~3s alone). A shorter ceiling would kill
# legitimate slow-but-working requests, not just genuine hangs.
_RESEARCH_TIMEOUT_S = 180.0


async def fetch_india_chain(
    ticker: str, expiration: int | None, source: str | None
) -> Response:
    """Run ``IndiaOptionsChainTool`` and return its envelope as an HTTP
    response — same error-surface contract as the US chain branch this
    replaces in ``options_chain()``."""
    kwargs: dict[str, Any] = {"ticker": ticker}
    if expiration is not None:
        kwargs["expiry_date"] = datetime.fromtimestamp(
            expiration, tz=timezone.utc
        ).strftime("%Y-%m-%d")
    if source:
        kwargs["source"] = source

    try:
        raw = await asyncio.to_thread(IndiaOptionsChainTool().execute, **kwargs)
        envelope = json.loads(raw)
    except Exception:  # noqa: BLE001 — never leak a stack frame to clients
        logger.exception("india options chain tool call failed (ticker=%s)", ticker)
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": "options chain request failed"},
        )
    if not envelope.get("ok"):
        return JSONResponse(status_code=502, content=envelope)
    return envelope


async def _fetch_india_chain_envelope(
    ticker: str, expiry_date: str | None, source: str
) -> dict[str, Any]:
    """Run ``IndiaOptionsChainTool`` and return its parsed envelope, or raise
    ``RuntimeError`` with a caller-facing message — thin wrapper shared by
    ``fetch_india_chain`` (HTTP response) and the POP-overlay route below
    (needs the parsed dict, not an HTTP `Response`)."""
    kwargs: dict[str, Any] = {"ticker": ticker, "source": source}
    if expiry_date:
        kwargs["expiry_date"] = expiry_date
    raw = await asyncio.to_thread(IndiaOptionsChainTool().execute, **kwargs)
    envelope = json.loads(raw)
    if not envelope.get("ok"):
        raise RuntimeError(str(envelope.get("error") or "options chain request failed"))
    return envelope


def _quantile_forecast_for_pop(ticker: str, horizon_days: int):
    """Module 3's forecast for the POP overlay — prefers the cheap, already-
    persisted `reinference_trigger` snapshot (module 3 step 5) when it matches
    the requested horizon, else runs `quantile_fusion` fresh (module 3's own
    curated fusion track, not the full ~20-track sweep — same cost discipline
    `reinference_trigger.py` already established for this exact situation).
    """
    from trade_integrations.dataflows.index_research.prediction_algorithms.reinference_trigger import (
        load_reinference_snapshot,
    )
    from trade_integrations.dataflows.options_research.pop_engine import QuantileForecast

    snapshot = load_reinference_snapshot(ticker)
    forecast_data = (snapshot or {}).get("forecast") or {}
    quantiles = forecast_data.get("quantiles")
    if quantiles and snapshot.get("horizon_days") == horizon_days:
        return QuantileForecast(horizon_days=horizon_days, quantiles=quantiles)

    from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
        context_from_hub,
    )
    from trade_integrations.dataflows.index_research.prediction_algorithms.registry import (
        run_all_tracks,
    )

    ctx = context_from_hub(ticker, horizon_days=horizon_days)
    if ctx is None:
        raise RuntimeError(f"no cached forecast context available for {ticker}")
    tracks = run_all_tracks(ctx, track_ids=["quantile_fusion"])
    fusion = tracks.get("quantile_fusion")
    if fusion is None or not fusion.available or not fusion.quantiles:
        raise RuntimeError("module 3's quantile_fusion forecast is unavailable right now")
    return QuantileForecast(horizon_days=horizon_days, quantiles=fusion.quantiles)


def register_india_options_routes(app: FastAPI, require_auth: AuthDep) -> None:
    """Mount ``GET /options/research``, ``GET /options/india/underlyings``, and
    ``GET /options/india/pop-overlay``."""

    @app.get("/options/india/underlyings", dependencies=[Depends(require_auth)])
    async def india_underlyings(source: str = Query("stock_simulator")) -> Response:
        """Known indexes (always) + recorded equities (``stock_history`` only,
        ``null`` for live sources — enumerating those would need an ~80k-row
        scrip-master download per request, not something to do per keystroke)."""
        try:
            data = await asyncio.to_thread(list_india_underlyings, source)
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("india underlyings lookup failed (source=%s)", source)
            return JSONResponse(
                status_code=502,
                content={"ok": False, "error": "underlyings lookup failed"},
            )
        return {"ok": True, "data": data}

    @app.get("/options/india/pop-overlay", dependencies=[Depends(require_auth)])
    async def options_pop_overlay(
        ticker: str = Query(..., max_length=64),
        expiry_date: str | None = Query(None, max_length=16),
        horizon_days: int = Query(7, ge=1, le=60),
        source: str = Query("stock_simulator"),
        n_paths: int = Query(5_000, ge=100, le=50_000),
    ) -> Response:
        """Module 4's chain-wide probability-of-profit-by-time-T overlay: one
        row per strike (calls + puts) with a Monte Carlo POP scored under
        module 3's own directional forecast — a **physical** (model-view)
        probability, not a market-neutral one; see
        `pop_engine.py`'s module docstring. Not authoritative "will this
        trade work" advice, a research overlay for the prediction-tab chain
        view.
        """
        if not ticker.strip():
            return JSONResponse(status_code=400, content={"ok": False, "error": "ticker is required"})

        try:
            chain_envelope = await _fetch_india_chain_envelope(ticker, expiry_date, source)
        except Exception as exc:  # noqa: BLE001 — never leak a stack frame to clients
            logger.warning("pop-overlay chain fetch failed (ticker=%s): %s", ticker, exc)
            return JSONResponse(status_code=502, content={"ok": False, "error": str(exc)})

        data = chain_envelope["data"]
        expiration_epoch = data.get("expiration")
        if expiration_epoch is None:
            return JSONResponse(
                status_code=502,
                content={"ok": False, "error": f"no resolvable expiry for {ticker}"},
            )
        underlying_ltp = data.get("underlying_ltp")
        if not underlying_ltp or underlying_ltp <= 0:
            return JSONResponse(
                status_code=502,
                content={"ok": False, "error": f"no underlying spot available for {ticker}"},
            )

        try:
            from trade_integrations.dataflows.company_research.market import india_trading_date

            today = india_trading_date()
            expiry_day = datetime.fromtimestamp(expiration_epoch, tz=timezone.utc).date()
            expiry_days = (expiry_day - today).days
            if expiry_days <= 0:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": f"resolved expiry {expiry_day} is not in the future"},
                )

            forecast = await asyncio.to_thread(_quantile_forecast_for_pop, ticker.strip().upper(), horizon_days)

            from trade_integrations.dataflows.options_research.pop_engine import compute_chain_pop_overlay

            overlay = await asyncio.to_thread(
                compute_chain_pop_overlay,
                spot=float(underlying_ltp),
                forecast=forecast,
                expiry_days=expiry_days,
                calls=data.get("calls") or [],
                puts=data.get("puts") or [],
                n_paths=n_paths,
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("pop-overlay computation failed (ticker=%s)", ticker)
            return JSONResponse(
                status_code=502, content={"ok": False, "error": "POP overlay computation failed"}
            )

        return {
            "ok": True,
            "ticker": ticker,
            "source": source,
            "expiry_date": expiry_day.isoformat(),
            "expiry_days": expiry_days,
            "horizon_days": horizon_days,
            "underlying_ltp": underlying_ltp,
            "distribution_type": "physical",
            "forecast_quantiles": forecast.quantiles,
            "overlay": overlay,
        }

    @app.get("/options/research", dependencies=[Depends(require_auth)])
    async def options_research(
        ticker: str | None = Query(None, max_length=64),
        expiry_date: str | None = Query(None, max_length=16),
    ) -> Response:
        """Wrap ``IndiaOptionsResearchTool`` (network I/O, run in a thread)."""
        if ticker is None or not ticker.strip():
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "ticker is required"}
            )
        kwargs: dict[str, Any] = {"ticker": ticker}
        if expiry_date:
            kwargs["expiry_date"] = expiry_date
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(IndiaOptionsResearchTool().execute, **kwargs),
                timeout=_RESEARCH_TIMEOUT_S,
            )
            envelope = json.loads(raw)
        except asyncio.TimeoutError:
            # The worker thread itself isn't killed (Python can't force-stop
            # a running thread) — it finishes or keeps retrying in the
            # background and is discarded. The client gets an explicit
            # answer now instead of an indefinite wait.
            logger.warning(
                "options research request exceeded %ss (ticker=%s)",
                _RESEARCH_TIMEOUT_S, ticker,
            )
            return JSONResponse(
                status_code=504,
                content={
                    "ok": False,
                    "error": f"options research timed out after {_RESEARCH_TIMEOUT_S}s",
                },
            )
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("options research tool call failed (ticker=%s)", ticker)
            return JSONResponse(
                status_code=502,
                content={"ok": False, "error": "options research request failed"},
            )
        if not envelope.get("ok"):
            return JSONResponse(status_code=502, content=envelope)
        return envelope
