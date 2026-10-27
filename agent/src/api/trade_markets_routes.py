"""Cross-market proxy routes: registry, per-country index/factor/flow/economy reads, global macro, market tick recording, and multi-market replay control. Split out of trade_routes.py; the routes register on its trade_router."""

from __future__ import annotations

from fastapi import Depends
from pydantic import BaseModel
from src.api.security import require_local_or_auth
from typing import Any, Literal
from .trade_recording_routes import _run_control
from .trade_routes import trade_router


@trade_router.get("/markets/registry")
def get_markets_registry(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Supported markets + their headline indices, for the frontend's market picker.

    Sourced from `market_registry.py` (the Layer-1 shared registry) rather than
    duplicating the country/index list in the frontend.
    """
    from trade_integrations.market_registry import SUPPORTED_MARKETS, get_market

    markets = []
    for code in SUPPORTED_MARKETS:
        spec = get_market(code)
        markets.append(
            {
                "code": spec.code,
                "currency": spec.currency,
                "timezone": spec.timezone,
                "indices": [idx.name for idx in spec.indices],
            }
        )
    return {"status": "ok", "markets": markets}


@trade_router.get("/markets/{country}/index/{index}")
def get_market_index_history(
    country: str,
    index: str,
    period: str = "1y",
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Historical index OHLCV for a non-India market — proxies `stock_simulator`'s
    global-markets vertical (`/history/{country}/index/{index}`)."""
    return _run_control(lambda c: c.get_market_index_history(country=country, index=index, period=period))


@trade_router.get("/markets/{country}/live_spot/{index}")
def get_market_live_spot(
    country: str,
    index: str,
    max_age_seconds: int | None = None,
    force_refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(
        lambda c: c.get_live_market_spot(
            country=country, index=index, max_age_seconds=max_age_seconds, force_refresh=force_refresh
        )
    )


@trade_router.get("/markets/{country}/factors/{series}")
def get_market_policy_factors(
    country: str,
    series: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_policy_factors(country=country, series=series))


@trade_router.get("/markets/{country}/flow/{series}")
def get_market_flow_of_funds(
    country: str,
    series: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_flow_of_funds(country=country, series=series))


@trade_router.get("/markets/{country}/economy/{series}")
def get_market_economy_factor(
    country: str,
    series: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Economy factor (GDP growth, fiscal balance, current-account balance, unemployment,
    consumption share of GDP, industrial production, PMI manufacturing) for any of the 7
    `market_registry` markets — proxies `stock_simulator`'s `/history/{country}/economy/{series}`,
    added for the Economy tab frontend ([[2026-08-23-economy-section-frontend-ui]])."""
    return _run_control(lambda c: c.get_economy_factor(country=country, series=series))


@trade_router.get("/markets/{country}/sector_indices")
def get_market_sector_indices(
    country: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """List of sector/headline indices TradingView has wired for a market — proxies
    `stock_simulator`'s `/history/{country}/sector_indices`, added for the sector-indices
    frontend wiring ([[2026-08-23-tradingview-sector-constituent-integration]])."""
    return _run_control(lambda c: c.get_market_sector_indices(country=country))


@trade_router.get("/markets/{country}/bundle")
def get_market_bundle(
    country: str,
    period: str = "3mo",
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Headline + sector index histories for a market in one request — proxies
    `stock_simulator`'s `/history/{country}/bundle`. Replaces the per-index `index/{index}`
    fan-out `GlobalMarketsPanel`/`SectorIndicesPanel` used to do (up to ~15 requests for a market
    like US) — the frontend already calls this route, but it was never wired up here, so every
    non-India market card surfaced as "Not Found" (see
    2026-08-27-global-markets-non-india-bundle-route-missing)."""
    return _run_control(lambda c: c.get_market_bundle(country=country, period=period))


@trade_router.get("/markets/{country}/top_constituents")
def get_market_top_constituents(
    country: str,
    top_n: int = 10,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Top-N constituents by market cap for a market — proxies `stock_simulator`'s
    `/history/{country}/top_constituents`. Not sourced for CN/RU/US."""
    return _run_control(lambda c: c.get_market_top_constituents(country=country, top_n=top_n))


@trade_router.get("/markets/factor_coverage")
def get_market_factor_coverage(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_market_factor_coverage())


@trade_router.get("/markets/{country}/replay/calendar")
def get_market_replay_calendar(
    country: str,
    lookback_days: int | None = None,
    before: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Per-day `market_ticks` presence for a non-India market's indices — proxies
    `stock_simulator`'s `/history/{country}/replay/calendar`, driving the per-country
    Replay/Data-coverage calendar (the India-tab analog of `/replay/calendar` above).

    `lookback_days`/`before` let the frontend page further back in history a window at a time
    instead of always seeing the most recent `lookback_days` days (see `history_data.py`'s
    route docstring)."""
    return _run_control(
        lambda c: c.get_market_replay_calendar(
            country=country, lookback_days=lookback_days, before=before,
        )
    )


@trade_router.get("/markets/global_macro/refreshable_series")
def list_market_global_macro_refreshable_series(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Registered before `/markets/global_macro/{series}` below so `refreshable_series` isn't
    swallowed as a `series` path parameter (same ordering rule the auto-record route above
    already follows for `/recording/{job_id}`)."""
    return _run_control(lambda c: c.list_eod_refreshable_series())


@trade_router.get("/markets/global_macro/ui_cards")
def get_market_global_macro_ui_cards(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Card metadata for `GlobalMarketsPanel.tsx`'s GLOBAL/CURRENCY tabs, generated from the
    factor registry — registered before `/markets/global_macro/{series}` below for the same
    routing reason as `refreshable_series` above."""
    return _run_control(lambda c: c.get_global_macro_ui_cards())


@trade_router.get("/markets/global_macro/{series}")
def get_market_global_macro(
    series: str,
    start: str | None = None,
    end: str | None = None,
    field: str | None = None,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Historical read for a cross-market `global_macro_store` series — currencies
    (`usd_inr`/`usd_cny`/`usd_jpy`/`usd_rub`/`usd_sar`/`usd_brl`) and global factors
    (`gold`, `oil_brent_daily`, `oil_wti_daily`, `vix_daily`, `us_10y`, `sp500`). Proxies
    `stock_simulator`'s `/history/global_macro`, not the per-country `/markets/{country}/...`
    dispatch — these series aren't owned by any one market."""
    return _run_control(lambda c: c.get_global_macro(series=series, start=start, end=end, field=field))


@trade_router.get("/markets/global_macro/{series}/live_spot")
def get_market_global_macro_live_spot(
    series: str,
    max_age_seconds: int | None = None,
    force_refresh: bool = False,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(
        lambda c: c.get_live_macro_spot(series=series, max_age_seconds=max_age_seconds, force_refresh=force_refresh)
    )


@trade_router.post("/markets/global_macro/{series}/refresh")
def refresh_market_global_macro(
    series: str,
    lookback_days: int = 90,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """On-demand EOD-history backfill for a cross-market `global_macro_store` series
    (currencies included) — populates what `GET /markets/global_macro/{series}` reads back.
    Nothing calls this automatically; the caller decides when to (re)backfill."""
    return _run_control(lambda c: c.refresh_global_macro_eod(series=series, lookback_days=lookback_days))


class StartMarketTickRecordingRequest(BaseModel):
    kind: Literal["fx", "index"]
    country: str | None = None
    symbols: list[str] | None = None
    interval_seconds: float


@trade_router.post("/markets/recording/start")
def start_market_tick_recording(
    body: StartMarketTickRecordingRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """User-triggered append-only tick recording for FX pairs or a non-India market's
    indices — separate from the always-on `stock_simulator` capture supervisors, which only
    write one overwritten value per factor per day. See
    `stock_simulator/recorder/tick_recorder.py` for the actual poll loop."""
    return _run_control(
        lambda c: c.start_tick_recording(
            kind=body.kind, country=body.country, symbols=body.symbols, interval_seconds=body.interval_seconds
        )
    )


@trade_router.post("/markets/recording/{job_id}/stop")
def stop_market_tick_recording(
    job_id: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.stop_tick_recording(job_id))


@trade_router.get("/markets/recording/active")
def list_market_tick_recordings(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.list_tick_recordings())


class ArmMultiMarketRequest(BaseModel):
    markets: list[str]
    start_utc: str | None = None
    end_utc: str | None = None
    speed: float = 1.0
    loop: bool = False


class SeekMultiMarketRequest(BaseModel):
    time: str


class SetMultiMarketSpeedRequest(BaseModel):
    speed: float


@trade_router.post("/markets/multi_market/arm")
def arm_multi_market(
    body: ArmMultiMarketRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    """Arm a cross-market simultaneous replay session — one UTC master clock watching several
    markets at once. See `stock_simulator/multi_market_replay.py`'s module docstring: this is
    live-forward tick data + backfilled daily closes only, not a deep historical intraday scrub
    for non-India markets yet."""
    return _run_control(
        lambda c: c.arm_multi_market_replay(
            markets=body.markets,
            start_utc=body.start_utc,
            end_utc=body.end_utc,
            speed=body.speed,
            loop=body.loop,
        )
    )


@trade_router.get("/markets/multi_market/status")
def get_multi_market_status(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_multi_market_status())


@trade_router.post("/markets/multi_market/pause")
def pause_multi_market(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.pause_multi_market_replay())


@trade_router.post("/markets/multi_market/resume")
def resume_multi_market(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.resume_multi_market_replay())


@trade_router.post("/markets/multi_market/seek")
def seek_multi_market(
    body: SeekMultiMarketRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.seek_multi_market_replay(time=body.time))


@trade_router.post("/markets/multi_market/speed")
def set_multi_market_speed(
    body: SetMultiMarketSpeedRequest,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.set_multi_market_replay_speed(speed=body.speed))


@trade_router.post("/markets/multi_market/stop")
def stop_multi_market(
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.stop_multi_market_replay())


@trade_router.get("/markets/multi_market/quote")
def get_multi_market_quote(
    market: str,
    symbol: str,
    exchange: str,
    _auth: None = Depends(require_local_or_auth),
) -> dict[str, Any]:
    return _run_control(lambda c: c.get_multi_market_quote(market=market, symbol=symbol, exchange=exchange))
