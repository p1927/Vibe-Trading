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
from dataclasses import asdict
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


# Bounded low: each entry is a real, synchronous live-vendor round-trip (not a cheap parquet
# read like the replay path's `get_replay_multi_expiry_chains`) — module docstring above
# measured a single chain fetch at ~3s; 3 sequential fetches keeps the added latency in the
# same ballpark as this file's own `_RESEARCH_TIMEOUT_S` budget for a comparable multi-stage
# call, without exposing a new query param for something this method itself already trades off
# to keep pop-overlay a "reasonably fast" request rather than a batch job.
_LIVE_TERM_STRUCTURE_MAX_EXPIRIES = 3


def _term_structure_entry_from_normalized_chain(
    chain_data: dict[str, Any], *, expiry_date: str
) -> dict[str, Any]:
    """Adapt ``IndiaOptionsChainTool``'s normalized ``{calls, puts}`` envelope (separate lists,
    one ``implied_volatility`` per leg) into the per-expiry raw-chain shape
    ``pop_engine.term_structure_from_chains`` expects — one merged row per strike carrying
    ``ce_iv``/``pe_iv``, matching what ``stock_history_bridge.get_replay_multi_expiry_chains``'s
    ``get_option_chain_at_sim_now`` chains already look like. Both sources already normalize IV
    to a fraction via the tool's own ``_normalize_iv`` before this point, so no percent
    conversion is needed here."""
    by_strike: dict[float, dict[str, float]] = {}
    for row in chain_data.get("calls") or []:
        strike, iv = row.get("strike"), row.get("implied_volatility")
        if strike is None or iv is None:
            continue
        by_strike.setdefault(float(strike), {})["ce_iv"] = float(iv)
    for row in chain_data.get("puts") or []:
        strike, iv = row.get("strike"), row.get("implied_volatility")
        if strike is None or iv is None:
            continue
        by_strike.setdefault(float(strike), {})["pe_iv"] = float(iv)
    legs = [{"strike": strike, **ivs} for strike, ivs in sorted(by_strike.items())]
    return {"expiry_date": expiry_date, "chain": legs}


async def _fetch_live_multi_expiry_batch(
    *, ticker: str, source: str, expirations: list[int]
) -> tuple[list[dict[str, Any]], str | None]:
    """Live-vendor (``indmoney``/``openalgo``) counterpart to
    ``stock_history_bridge.get_replay_multi_expiry_chains`` — real gap this closes: no live
    batch multi-expiry chain fetch existed anywhere in this pipeline (see the
    ``options-profitability-pop-engine`` backlog item's 2026-08-24 finding). Reuses the
    ``expirations`` epoch list the primary chain fetch already returned rather than re-listing
    expiries with a second vendor call. One expiry's fetch failing doesn't drop the batch —
    same per-expiry-independent-failure convention the replay-mode function uses. Returns
    ``([], reason)`` only when the whole batch can't start (no expirations at all).
    """
    if not expirations:
        return [], f"no expiries available for {ticker} ({source})"

    expiry_dates: list[str] = []
    for epoch in expirations[:_LIVE_TERM_STRUCTURE_MAX_EXPIRIES]:
        try:
            expiry_dates.append(datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat())
        except (TypeError, ValueError, OSError):
            continue
    if not expiry_dates:
        return [], f"no resolvable expiry dates for {ticker} ({source})"

    results: list[dict[str, Any]] = []
    for expiry_date in expiry_dates:
        try:
            envelope = await _fetch_india_chain_envelope(ticker, expiry_date, source)
        except Exception as exc:  # noqa: BLE001 — one expiry failing shouldn't drop the batch
            results.append({"expiry_date": expiry_date, "chain": None, "error": str(exc)[:200]})
            continue
        chain_entry = _term_structure_entry_from_normalized_chain(
            envelope["data"], expiry_date=expiry_date
        )
        results.append({"expiry_date": expiry_date, "chain": chain_entry, "error": None})
    return results, None


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
        apply_liquidity_discount: bool = Query(False),
        use_iv_term_structure: bool = Query(False),
        iv_decay_half_life_days: float | None = Query(None, gt=0),
        iv_decay_floor_fraction: float = Query(0.5, ge=0.0, le=1.0),
    ) -> Response:
        """Module 4's chain-wide probability-of-profit-by-time-T overlay: one
        row per strike (calls + puts) with a Monte Carlo POP scored under
        module 3's own directional forecast — a **physical** (model-view)
        probability, not a market-neutral one; see
        `pop_engine.py`'s module docstring. Not authoritative "will this
        trade work" advice, a research overlay for the prediction-tab chain
        view.

        ``apply_liquidity_discount`` (default off) models realistic-fill
        entry cost + an exit-side spread haircut using each strike's real
        top-of-book bid/ask — only available when ``source`` is
        ``indmoney``/``openalgo`` (``stock_simulator`` rows have no bid/ask,
        so this is a no-op per strike against that source; see
        `pop_engine.py`'s `compute_pop_at_t` docstring).

        ``use_iv_term_structure`` (default off) reprices IV-at-T from a real
        multi-expiry IV curve instead of holding ``entry_iv`` constant. For
        ``source=stock_simulator`` this requires an **armed replay session**
        (fetches `stock_history_bridge.get_replay_multi_expiry_chains`,
        which only resolves against the running `ReplayService`'s current
        `sim_now`); for ``indmoney``/``openalgo`` it instead makes up to 3
        real live vendor chain fetches (one per near-term expiry, bounded —
        see `_fetch_live_multi_expiry_batch`), no replay session needed.
        Returns 400 if the batch can't be built (replay not armed / no
        expiries / vendor errors on every expiry) or has no usable IV
        points, rather than silently falling back to constant IV.

        ``iv_decay_half_life_days`` (default off — ``None`` means constant
        IV) reprices IV-at-T via a synthetic exponential relaxation toward
        ``iv_decay_floor_fraction * entry_iv`` instead of a real term
        structure — a cheaper, data-free alternative to
        ``use_iv_term_structure`` for when no replay session is armed.
        Mutually exclusive with ``use_iv_term_structure``: passing both
        returns 400 rather than silently picking one.
        """
        if not ticker.strip():
            return JSONResponse(status_code=400, content={"ok": False, "error": "ticker is required"})
        if iv_decay_half_life_days is not None and use_iv_term_structure:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "iv_decay_half_life_days and use_iv_term_structure are mutually exclusive",
                },
            )

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

            from trade_integrations.dataflows.options_research.pop_engine import (
                compute_chain_pop_overlay,
                event_window_for_ticker,
                term_structure_from_chains,
            )

            overlay_kwargs: dict[str, Any] = {
                "spot": float(underlying_ltp),
                "forecast": forecast,
                "expiry_days": expiry_days,
                "calls": data.get("calls") or [],
                "puts": data.get("puts") or [],
                "n_paths": n_paths,
                "apply_liquidity_discount": apply_liquidity_discount,
                "iv_decay_half_life_days": iv_decay_half_life_days,
                "iv_decay_floor_fraction": iv_decay_floor_fraction,
            }

            iv_term_structure_points: list[tuple[float, float]] | None = None
            if use_iv_term_structure:
                if source == "stock_simulator":
                    from trade_integrations.dataflows.stock_history_bridge import (
                        get_replay_multi_expiry_chains,
                    )

                    underlying = str(data.get("underlying") or ticker).strip().upper()
                    exchange = str(data.get("exchange") or "NSE_INDEX").strip().upper()
                    batch, reason = await asyncio.to_thread(
                        get_replay_multi_expiry_chains, underlying=underlying, exchange=exchange
                    )
                else:  # indmoney/openalgo — real live vendor calls, no replay session involved
                    batch, reason = await _fetch_live_multi_expiry_batch(
                        ticker=ticker, source=source, expirations=data.get("expirations") or []
                    )
                if reason is not None:
                    return JSONResponse(
                        status_code=400,
                        content={"ok": False, "error": f"iv term structure unavailable: {reason}"},
                    )
                iv_term_structure_points = term_structure_from_chains(
                    batch, spot=float(underlying_ltp), as_of=today
                )
                if not iv_term_structure_points:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "ok": False,
                            "error": "no usable IV term-structure points from the replay chain batch",
                        },
                    )
                overlay_kwargs["iv_term_structure"] = iv_term_structure_points

            overlay = await asyncio.to_thread(compute_chain_pop_overlay, **overlay_kwargs)
            event_risks = await asyncio.to_thread(
                event_window_for_ticker, ticker.strip().upper(), expiry_days
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
            "apply_liquidity_discount": apply_liquidity_discount,
            "iv_decay_half_life_days": iv_decay_half_life_days,
            "iv_decay_floor_fraction": iv_decay_floor_fraction,
            "use_iv_term_structure": use_iv_term_structure,
            "iv_term_structure_points": (
                [{"days_to_expiry": d, "atm_iv": iv} for d, iv in iv_term_structure_points]
                if iv_term_structure_points is not None
                else None
            ),
            "forecast_quantiles": forecast.quantiles,
            "overlay": overlay,
            "event_risks": event_risks,
        }

    @app.get("/options/india/selector", dependencies=[Depends(require_auth)])
    async def options_selector_route(
        ticker: str = Query(..., max_length=64),
        expiry_date: str | None = Query(None, max_length=16),
        horizon_days: int = Query(7, ge=1, le=60),
        target_profit: float = Query(..., gt=0),
        rank_by: str = Query("risk_reward", pattern="^(risk_reward|pop_per_risk)$"),
        n_paths: int = Query(5_000, ge=100, le=50_000),
    ) -> Response:
        """Module 5's risk-adjusted candidate selector: generates candidate
        strategies off the live chain (module 5's `candidate_generator`),
        scores each by max-loss/max-profit + module 4's POP-by-T, and ranks
        by risk/reward (`max_loss / target_profit`) or POP-per-risk. Same
        physical-not-risk-neutral caveat as `/options/india/pop-overlay` —
        see `pop_engine.py`'s module docstring. Every scored candidate is
        returned (`options_selector.select_candidates` never filters), so
        callers see `meets_target=false` candidates too, not just the winner.
        """
        ticker_norm = ticker.strip().upper()
        if not ticker_norm:
            return JSONResponse(status_code=400, content={"ok": False, "error": "ticker is required"})

        try:
            from trade_integrations.dataflows.options_research.market import (
                options_research_ineligible_reason,
                resolve_options_instrument,
            )

            ineligible = options_research_ineligible_reason(ticker_norm)
            if ineligible:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": f"{ticker_norm} is not eligible for options research ({ineligible})"},
                )
            instrument = resolve_options_instrument(ticker_norm)
        except Exception as exc:  # noqa: BLE001 — never leak a stack frame to clients
            logger.warning("selector instrument resolution failed (ticker=%s): %s", ticker_norm, exc)
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})

        try:
            from trade_integrations.dataflows.options_research.sources.chain_openalgo import (
                fetch_chain_stage,
            )

            stage = await asyncio.to_thread(fetch_chain_stage, instrument, expiry_date=expiry_date)
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("selector chain fetch failed (ticker=%s)", ticker_norm)
            return JSONResponse(status_code=502, content={"ok": False, "error": "options chain request failed"})

        if stage.status == "error" or not stage.data:
            return JSONResponse(
                status_code=502,
                content={"ok": False, "error": "; ".join(stage.errors) or f"no live option chain for {ticker_norm}"},
            )

        chain_snapshot = stage.data
        underlying_ltp = chain_snapshot.get("underlying_ltp")
        if not underlying_ltp or underlying_ltp <= 0:
            return JSONResponse(
                status_code=502, content={"ok": False, "error": f"no underlying spot available for {ticker_norm}"}
            )
        expiry_str = chain_snapshot.get("expiry_date")
        if not expiry_str:
            return JSONResponse(status_code=502, content={"ok": False, "error": f"no resolvable expiry for {ticker_norm}"})

        try:
            from trade_integrations.dataflows.company_research.market import india_trading_date

            today = india_trading_date()
            expiry_day = datetime.strptime(str(expiry_str)[:10], "%Y-%m-%d").date()
            expiry_days = (expiry_day - today).days
            if expiry_days <= 0:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": f"resolved expiry {expiry_day} is not in the future"},
                )

            from trade_integrations.dataflows.options_research.candidate_generator import (
                generate_candidates,
            )

            candidates = await asyncio.to_thread(generate_candidates, instrument, chain_snapshot)
            if not candidates:
                return JSONResponse(
                    status_code=502,
                    content={"ok": False, "error": "no candidates could be generated from the live chain"},
                )

            forecast = await asyncio.to_thread(_quantile_forecast_for_pop, ticker_norm, horizon_days)

            from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
                context_from_hub,
            )
            from trade_integrations.dataflows.options_research.options_selector import (
                select_candidates,
            )

            # select_candidates only populates event_risks when both signals/macro_factors
            # are given — this route never passed them before, so every result's
            # event_risks was silently always empty. Cheap cached hub-doc read, same source
            # pop-overlay's own event_window_for_ticker uses.
            event_ctx = await asyncio.to_thread(context_from_hub, ticker_norm, horizon_days=expiry_days)

            results = await asyncio.to_thread(
                select_candidates,
                candidates,
                spot=float(underlying_ltp),
                forecast=forecast,
                expiry_days=expiry_days,
                target_profit=target_profit,
                n_paths=n_paths,
                rank_by=rank_by,
                signals=event_ctx.signals if event_ctx else None,
                macro_factors=event_ctx.macro_factors if event_ctx else None,
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})
        except Exception:  # noqa: BLE001 — never leak a stack frame to clients
            logger.exception("selector computation failed (ticker=%s)", ticker_norm)
            return JSONResponse(status_code=502, content={"ok": False, "error": "selector computation failed"})

        return {
            "ok": True,
            "ticker": ticker_norm,
            "expiry_date": expiry_day.isoformat(),
            "expiry_days": expiry_days,
            "horizon_days": horizon_days,
            "underlying_ltp": underlying_ltp,
            "target_profit": target_profit,
            "rank_by": rank_by,
            "distribution_type": "physical",
            "candidates": [asdict(r) for r in results],
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
