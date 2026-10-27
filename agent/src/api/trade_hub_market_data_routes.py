"""Hub market-data reads (ticks, spot, option chain, macro factors, index history, replay overview, stock-history coverage). Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

from datetime import datetime
from fastapi import Depends
from pydantic import BaseModel
from src.api.helpers import redacted_error_detail
from src.api.security import require_local_or_auth
from typing import Any
from .trade_routes import logger, trade_router


class HubMarketDataTicksResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    source: str
    ticks: list[dict[str, Any]] = []
    error: str | None = None


class HubMarketDataSpotResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    spot: dict[str, Any] | None = None
    # Whether the underlying's real trading session is open right now (IST,
    # weekday + simulator-forced-open aware). None when the check itself
    # failed — the UI should not claim either state in that case. Lets the
    # frontend tell "market closed" apart from "broker unreachable", which
    # otherwise render identically as an empty spot quote.
    session_open: bool | None = None
    error: str | None = None


class HubMarketDataOptionChainStrike(BaseModel):
    strike: float
    ce: dict[str, Any] | None = None
    pe: dict[str, Any] | None = None


class HubMarketDataOptionChainResponse(BaseModel):
    status: str
    underlying: str
    exchange: str
    expiry_date: str | None = None
    underlying_ltp: float | None = None
    underlying_prev_close: float | None = None
    strikes: list[HubMarketDataOptionChainStrike] = []
    source: str = "openalgo"
    error: str | None = None


class HubMarketDataOptionExpiriesResponse(BaseModel):
    status: str
    underlying: str
    exchange: str
    expiries: list[str] = []
    error: str | None = None


class HubMacroFactorPanelResponse(BaseModel):
    status: str
    start: str
    end: str
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    error: str | None = None


class HubMacroFactorLatestResponse(BaseModel):
    status: str
    day: str | None = None
    factors: dict[str, float | None] | None = None
    error: str | None = None


class HubMacroFactorDatesResponse(BaseModel):
    status: str
    dates: list[str] = []
    error: str | None = None


class HubIndexHistoryDaysResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    days: list[str] = []
    error: str | None = None


class HubIndexHistoryExpiriesResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    expiries: list[str] = []
    error: str | None = None


class HubIndexHistoryBarsResponse(BaseModel):
    status: str
    symbol: str
    exchange: str
    bars: list[dict[str, Any]] = []
    truncated: bool = False
    error: str | None = None


class HubConstituentsPanelResponse(BaseModel):
    status: str
    rows: list[dict[str, Any]] = []
    error: str | None = None


class HubStockHistoryCoverageResponse(BaseModel):
    """Per-week, per-bucket availability gate for the stock_simulator.

    `days[*].buckets[name]` carries the per-bucket status (present flag,
    row count, on-disk location, primary_source, fetch_command). When
    `is_complete=False` the UI should render the missing cells as white
    blocks; clicking a cell opens the matching fetch_command.
    """

    status: str
    week_start: str
    week_end: str
    symbol: str
    is_complete: bool
    missing_days: list[str] = []
    bucket_labels: list[str] = []
    days: list[dict[str, Any]] = []
    fetch_list: list[dict[str, Any]] = []
    error: str | None = None


class HubReplayDayOverviewResponse(BaseModel):
    status: str
    date: str
    equities: list[dict[str, Any]] = []
    options: dict[str, list[str]] = {}
    macro_factor_keys: list[str] = []
    constituents_available: bool = False
    error: str | None = None


@trade_router.get("/hub/market-data/ticks", response_model=HubMarketDataTicksResponse)
def hub_market_data_ticks(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    since_minutes: int = 240,
    limit: int = 500,
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataTicksResponse:
    """Recent market_ticks rows for the underlying. Empty list when Timescale disabled.

    When ``replay=1``, route through the simulator's ReplayService so the
    chart shows ticks from the recorded session (advances the sim clock and
    reads the catalog bar at sim_now). Falls back to Timescale on miss.
    """
    if replay:
        from trade_integrations.dataflows.stock_history_bridge import get_replay_market_ticks

        try:
            ticks, reason = get_replay_market_ticks(
                symbol=symbol, exchange=exchange,
                since_minutes=since_minutes, limit=limit,
            )
            return HubMarketDataTicksResponse(
                status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
                source="simulator" if ticks else "empty", ticks=ticks,
                error=reason if not ticks else None,
            )
        except Exception as exc:
            logger.exception("hub market-data ticks (replay) failed for %s/%s", symbol, exchange)
            return HubMarketDataTicksResponse(
                status="error", symbol=symbol.upper(), exchange=exchange.upper(),
                source="empty", ticks=[], error=redacted_error_detail(exc),
            )

    from trade_integrations.dataflows.stock_history_bridge import get_recent_market_ticks

    try:
        ticks = get_recent_market_ticks(
            symbol=symbol, exchange=exchange,
            since_minutes=since_minutes, limit=limit,
        )
        # Each tick's per-row ``source`` field tells the bridge where it
        # came from ("openalgo"/"indmoney_recorder_ws" from the hot tier,
        # "parquet_fallback" from the replay bundle when the hot tier is
        # empty — used to render a chart line even when the recorder
        # hasn't populated TimescaleDB yet, which previously rendered a
        # blank plot). Surface that here so the UI badge can tell the
        # truth rather than silently labelling stale bars as live ticks.
        if ticks and any(t.get("source") == "parquet_fallback" for t in ticks):
            wrapper_source = "parquet_fallback"
        else:
            wrapper_source = "timescale" if ticks else "empty"
        return HubMarketDataTicksResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
            source=wrapper_source, ticks=ticks,
        )
    except Exception as exc:
        logger.exception("hub market-data ticks failed for %s/%s", symbol, exchange)
        return HubMarketDataTicksResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            source="empty", ticks=[], error=redacted_error_detail(exc),
        )


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _spot_unavailable_message() -> str:
    """Why there is no live spot, naming the cause the simulator's /health knows: a capture loop waiting for
    a fresh INDmoney token, or the simulator being down (the tile otherwise says nothing useful)."""
    from trade_integrations.stock_simulator.client import StockSimulatorClient, StockSimulatorClientError

    base = "no spot quote available (Timescale empty and OpenAlgo unreachable)"
    try:
        waiting = StockSimulatorClient().health_check().get("capture_waiting_for_credentials") or []
    except StockSimulatorClientError as exc:
        return f"{base}; stock_simulator unreachable: {exc}"
    if waiting:
        return f"{base}; {', '.join(waiting)} capture is waiting for credentials (paste a fresh INDmoney token)"
    return base


@trade_router.get("/hub/market-data/spot", response_model=HubMarketDataSpotResponse)
def hub_market_data_spot(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataSpotResponse:
    """Current spot — Timescale first, OpenAlgo fallback. Returns 200 with
    status='error' on degraded state so the UI renders an empty-state cleanly.

    When ``replay=1``, read from the simulator's ReplayService (advances sim
    clock and returns the catalog bar at sim_now). Tag the response with
    ``source=simulator`` so the UI can badge it.
    """
    from trade_integrations.dataflows.stock_history_bridge import (
        get_live_spot_quote,
        get_replay_spot_quote,
    )

    replay_reason: str | None = None
    if replay:
        quote, replay_reason = get_replay_spot_quote(symbol=symbol, exchange=exchange)
    else:
        quote = get_live_spot_quote(symbol=symbol, exchange=exchange)

    session_open: bool | None = None
    if not replay:
        try:
            from trade_integrations.autonomous_agents.market_hours import is_trading_session_open

            session_open = is_trading_session_open(market="IN")
        except Exception:
            logger.exception("session_open check failed for %s/%s", symbol, exchange)

    if quote is None:
        return HubMarketDataSpotResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            session_open=session_open,
            error=replay_reason if replay else _spot_unavailable_message(),
        )
    return HubMarketDataSpotResponse(
        status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
        session_open=session_open,
        spot={
            "symbol": quote.symbol, "exchange": quote.exchange,
            "ltp": quote.ltp, "prev_close": quote.prev_close,
            "bid": quote.bid, "ask": quote.ask,
            "volume": quote.volume, "source": quote.source,
            "as_of": quote.as_of,
        },
    )


@trade_router.get("/hub/market-data/option-chain", response_model=HubMarketDataOptionChainResponse)
def hub_market_data_option_chain(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    strike_count: int = 10,
    expiry_date: str | None = None,
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataOptionChainResponse:
    """Option chain — live from OpenAlgo, or replay from the simulator.

    When ``replay=1``, reads through the simulator's ReplayService (recorded
    parquet chain -> Black-Scholes synthesizer fallback) instead of the live
    broker, which isn't running during a replay session — hitting the live
    path there just times out with nothing to show.
    """
    if replay:
        from trade_integrations.dataflows.stock_history_bridge import get_replay_option_chain

        data, reason = get_replay_option_chain(
            underlying=symbol, exchange=exchange,
            strike_count=strike_count, expiry_date=expiry_date,
        )
        if data is None:
            return HubMarketDataOptionChainResponse(
                status="error", underlying=symbol.upper(), exchange=exchange.upper(),
                error=reason or "no replay chain available (simulator not running?)",
            )
        legs = data.get("chain") or []
        strikes: list[HubMarketDataOptionChainStrike] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            strikes.append(HubMarketDataOptionChainStrike(
                strike=float(leg.get("strike") or leg.get("strike_price") or 0),
                ce={
                    "last_price": leg.get("ce_ltp"), "oi": leg.get("ce_oi"), "volume": leg.get("ce_volume"),
                    "iv": leg.get("ce_iv"), "delta": leg.get("ce_delta"), "gamma": leg.get("ce_gamma"),
                    "theta": leg.get("ce_theta"), "vega": leg.get("ce_vega"),
                },
                pe={
                    "last_price": leg.get("pe_ltp"), "oi": leg.get("pe_oi"), "volume": leg.get("pe_volume"),
                    "iv": leg.get("pe_iv"), "delta": leg.get("pe_delta"), "gamma": leg.get("pe_gamma"),
                    "theta": leg.get("pe_theta"), "vega": leg.get("pe_vega"),
                },
            ))
        return HubMarketDataOptionChainResponse(
            status="ok", underlying=symbol.upper(), exchange=exchange.upper(),
            expiry_date=str(data.get("expiry_date") or "")[:10] or None,
            underlying_ltp=_safe_float(data.get("underlying_ltp") or data.get("spot")),
            underlying_prev_close=None,
            strikes=strikes, source=str(data.get("source") or "simulator"),
        )

    from trade_integrations.dataflows.stock_history_bridge import get_live_option_chain

    data = get_live_option_chain(
        underlying=symbol, exchange=exchange,
        strike_count=strike_count, expiry_date=expiry_date,
    )
    if data is None:
        return HubMarketDataOptionChainResponse(
            status="error", underlying=symbol.upper(), exchange=exchange.upper(),
            error="OpenAlgo returned no chain (broker unreachable?)",
        )
    strikes_raw = data.get("strikes") or data.get("chain") or []
    strikes: list[HubMarketDataOptionChainStrike] = []
    for s in strikes_raw:
        if not isinstance(s, dict):
            continue
        strikes.append(HubMarketDataOptionChainStrike(
            strike=float(s.get("strike") or s.get("strike_price") or 0),
            ce=s.get("ce") if "ce" in s else s,
            pe=s.get("pe") if "pe" in s else s,
        ))
    return HubMarketDataOptionChainResponse(
        status="ok", underlying=symbol.upper(), exchange=exchange.upper(),
        expiry_date=str(data.get("expiry_date") or data.get("expiry") or "")[:10] or None,
        underlying_ltp=_safe_float(data.get("underlying_ltp") or data.get("ltp") or data.get("spot")),
        underlying_prev_close=_safe_float(data.get("underlying_prev_close") or data.get("prev_close")),
        strikes=strikes,
        source=str(data.get("source") or "openalgo"),
    )


@trade_router.get("/hub/market-data/option-expiries", response_model=HubMarketDataOptionExpiriesResponse)
def hub_market_data_option_expiries(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    replay: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubMarketDataOptionExpiriesResponse:
    """Expiries selectable in the option-chain dropdown.

    In replay mode, the next several recorded expiries on/after the
    currently-armed day (see `get_replay_option_expiries` for why this isn't
    scoped to "expiries with a parquet bundle covering this exact day" — that
    predicate collapses to a single entry almost every session). Live mode
    returns every expiry OpenAlgo/the recorder has ever captured for the
    underlying, since there's no "current day" to scope to.
    """
    if replay:
        from trade_integrations.dataflows.stock_history_bridge import get_replay_option_expiries

        expiries, reason = get_replay_option_expiries(underlying=symbol, exchange=exchange)
        if reason and not expiries:
            return HubMarketDataOptionExpiriesResponse(
                status="error", underlying=symbol.upper(), exchange=exchange.upper(),
                expiries=[], error=reason,
            )
        return HubMarketDataOptionExpiriesResponse(
            status="ok", underlying=symbol.upper(), exchange=exchange.upper(), expiries=expiries,
        )

    from trade_integrations.dataflows.stock_history_bridge import list_recorded_option_expiries

    try:
        all_expiries = list_recorded_option_expiries(symbol=symbol, exchange=exchange)
        # `all_expiries` holds every expiry the recorder has ever captured,
        # sorted ascending — over weeks of recording that includes long-
        # expired chains, and the dropdown defaults to index 0, so without
        # this filter it silently opens on the oldest expiry ever recorded
        # instead of the current one. Mirrors the replay path's `e >=
        # replay_date` filter (see `get_replay_option_expiries`), scoped to
        # today since live mode has no replay day to anchor to. Anchored to
        # IST (not UTC) since expiry dates are NSE trading days — using UTC
        # would misfire for ~5.5h/day when the UTC and IST calendar dates
        # differ.
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        upcoming = sorted(e for e in all_expiries if e >= today)
        return HubMarketDataOptionExpiriesResponse(
            status="ok", underlying=symbol.upper(), exchange=exchange.upper(),
            expiries=upcoming or all_expiries,
        )
    except Exception as exc:
        logger.exception("hub market-data option-expiries failed for %s/%s", symbol, exchange)
        return HubMarketDataOptionExpiriesResponse(
            status="error", underlying=symbol.upper(), exchange=exchange.upper(),
            expiries=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/macro-factors/panel", response_model=HubMacroFactorPanelResponse)
def hub_macro_factor_panel(
    start: str,
    end: str,
    _auth: None = Depends(require_local_or_auth),
) -> HubMacroFactorPanelResponse:
    """Wide-form macro factor panel for [start, end] (YYYY-MM-DD)."""
    from trade_integrations.dataflows.stock_history_bridge import get_macro_factor_panel

    try:
        df = get_macro_factor_panel(start=start, end=end)
        if df.empty:
            return HubMacroFactorPanelResponse(
                status="ok", start=start, end=end, rows=[], columns=[],
            )
        # Replace NaN with None so JSON encoding is clean.
        df_clean = df.where(df.notna(), None)
        rows = df_clean.to_dict(orient="records")
        return HubMacroFactorPanelResponse(
            status="ok", start=start, end=end,
            rows=rows, columns=[str(c) for c in df.columns.tolist()],
        )
    except Exception as exc:
        logger.exception("hub macro-factors panel failed for %s/%s", start, end)
        return HubMacroFactorPanelResponse(
            status="error", start=start, end=end, rows=[], columns=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/macro-factors/latest", response_model=HubMacroFactorLatestResponse)
def hub_macro_factor_latest(
    _auth: None = Depends(require_local_or_auth),
) -> HubMacroFactorLatestResponse:
    """Most recent day's macro factor snapshot, or {status='error'} when no data."""
    from trade_integrations.dataflows.stock_history_bridge import get_latest_macro_snapshot

    snap = get_latest_macro_snapshot()
    if snap is None:
        return HubMacroFactorLatestResponse(
            status="error", day=None, factors=None, error="no macro factor data",
        )
    return HubMacroFactorLatestResponse(
        status="ok", day=snap["day"], factors=snap["factors"],
    )


@trade_router.get("/hub/macro-factors/dates", response_model=HubMacroFactorDatesResponse)
def hub_macro_factor_dates(
    _auth: None = Depends(require_local_or_auth),
) -> HubMacroFactorDatesResponse:
    from trade_integrations.dataflows.stock_history_bridge import list_macro_factor_dates
    try:
        return HubMacroFactorDatesResponse(
            status="ok", dates=list_macro_factor_dates(),
        )
    except Exception as exc:
        logger.exception("hub macro-factors dates failed")
        return HubMacroFactorDatesResponse(
            status="error", dates=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/index-history/days", response_model=HubIndexHistoryDaysResponse)
def hub_index_history_days(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    _auth: None = Depends(require_local_or_auth),
) -> HubIndexHistoryDaysResponse:
    from trade_integrations.dataflows.stock_history_bridge import list_recorded_index_days
    try:
        return HubIndexHistoryDaysResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
            days=list_recorded_index_days(symbol=symbol, exchange=exchange),
        )
    except Exception as exc:
        logger.exception("hub index-history days failed")
        return HubIndexHistoryDaysResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            days=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/index-history/expiries", response_model=HubIndexHistoryExpiriesResponse)
def hub_index_history_expiries(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    _auth: None = Depends(require_local_or_auth),
) -> HubIndexHistoryExpiriesResponse:
    from trade_integrations.dataflows.stock_history_bridge import list_recorded_option_expiries
    try:
        return HubIndexHistoryExpiriesResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(),
            expiries=list_recorded_option_expiries(symbol=symbol, exchange=exchange),
        )
    except Exception as exc:
        logger.exception("hub index-history expiries failed")
        return HubIndexHistoryExpiriesResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            expiries=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/index-history/bars", response_model=HubIndexHistoryBarsResponse)
def hub_index_history_bars(
    symbol: str = "NIFTY",
    exchange: str = "NSE_INDEX",
    since_ist: str = "2015-01-09T09:15:00",
    until_ist: str = "2026-12-31T15:30:00",
    _auth: None = Depends(require_local_or_auth),
) -> HubIndexHistoryBarsResponse:
    from datetime import datetime
    from trade_integrations.dataflows.stock_history_bridge import get_index_history

    try:
        since_dt = datetime.fromisoformat(since_ist)
        until_dt = datetime.fromisoformat(until_ist)
        bars, truncated = get_index_history(
            symbol=symbol, exchange=exchange,
            since_ist=since_dt, until_ist=until_dt,
        )
        return HubIndexHistoryBarsResponse(
            status="ok", symbol=symbol.upper(), exchange=exchange.upper(), bars=bars,
            truncated=truncated,
        )
    except Exception as exc:
        logger.exception("hub index-history bars failed")
        return HubIndexHistoryBarsResponse(
            status="error", symbol=symbol.upper(), exchange=exchange.upper(),
            bars=[], error=redacted_error_detail(exc),
        )


@trade_router.get("/hub/replay/day-overview", response_model=HubReplayDayOverviewResponse)
def hub_replay_day_overview(
    date: str,
    _auth: None = Depends(require_local_or_auth),
) -> HubReplayDayOverviewResponse:
    """What's on disk for one replay-calendar day: equities, option-chain
    coverage per underlying, macro factor keys, constituents availability.

    Index/equity spot-bar counts for NIFTY/BANKNIFTY/SENSEX are already in the
    calendar payload the UI fetches separately — this fills in everything the
    calendar heatmap doesn't carry.
    """
    from trade_integrations.dataflows.stock_history_bridge import get_replay_day_overview

    try:
        overview = get_replay_day_overview(date=date)
        return HubReplayDayOverviewResponse(status="ok", **overview)
    except Exception as exc:
        logger.exception("hub replay day-overview failed for %s", date)
        return HubReplayDayOverviewResponse(status="error", date=date, error=redacted_error_detail(exc))


@trade_router.get("/hub/constituents/panel", response_model=HubConstituentsPanelResponse)
def hub_constituents_panel(
    start: str | None = None,
    end: str | None = None,
    limit: int = 1000,
    _auth: None = Depends(require_local_or_auth),
) -> HubConstituentsPanelResponse:
    # Constituents live under stock_history.api, reached through the stock_simulator facade
    # (StockSimulatorClient) rather than StockHistory() directly — see
    # .claude/backlog/items/2026-08-23-india-dedicated-methods-retirement.md, Tier 4.
    from trade_integrations.stock_simulator.client import StockSimulatorClient, StockSimulatorClientError

    try:
        resp = StockSimulatorClient().get_constituents_history(start=start, end=end)
        rows = resp["data"][:limit]
        return HubConstituentsPanelResponse(status="ok", rows=rows)
    except StockSimulatorClientError as exc:
        logger.exception("hub constituents panel failed")
        return HubConstituentsPanelResponse(status="error", rows=[], error=redacted_error_detail(exc))


@trade_router.get("/hub/stock-history/coverage", response_model=HubStockHistoryCoverageResponse)
def hub_stock_history_coverage(
    week: str,
    symbol: str = "NIFTY",
    include_optional: int = 0,
    _auth: None = Depends(require_local_or_auth),
) -> HubStockHistoryCoverageResponse:
    """Per-week bucket-coverage snapshot for the stock_simulator.

    `week` is any ISO date in the target ISO week (rounded to Mon..Fri).
    The response carries per-day, per-bucket status with `primary_source`
    and `fetch_command` for every missing bucket — the UI uses this to
    render the white-cell gap and surface the backfill command on click.
    """
    from src.trade.stock_simulator_facade import sim_client

    try:
        payload = sim_client().get_coverage_report(
            week_start=week,
            symbol=symbol,
            include_optional=bool(include_optional),
        )["data"]
        return HubStockHistoryCoverageResponse(status="ok", **payload)
    except Exception as exc:
        logger.exception("stock-history coverage failed for week=%s", week)
        return HubStockHistoryCoverageResponse(
            status="error", week_start="", week_end="",
            symbol=symbol, is_complete=False, error=redacted_error_detail(exc),
        )
