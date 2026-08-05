"""Read adapters and generic broker_sdk entry points for eToro."""

from __future__ import annotations

from typing import Any

from src.trading.connectors.etoro.client import (
    BASE_URL,
    EtoroAPIError,
    EtoroConfig,
    EtoroConfigError,
    PAPER_GUARD,
    build_config,
    info_root,
    load_config,
    make_client,
    public_config,
    save_config,
)
from src.trading.connectors.etoro.copy_trading import (
    copy_close,
    copy_poll,
    copy_precheck,
    copy_start_or_adjust,
)
from src.trading.connectors.etoro.instruments import resolve_instrument_id, search_instruments
from src.trading.connectors.etoro.trading import (
    cancel_close_order,
    cancel_order as cancel_open_order,
    close_position,
    edit_position_stops,
    place_order as place_open_order,
)

__all__ = [
    "build_config",
    "load_config",
    "save_config",
    "check_status",
    "get_account_snapshot",
    "get_positions",
    "get_open_orders",
    "get_quote",
    "get_historical_bars",
    "place_order",
    "cancel_order",
    "close_position",
    "cancel_close_order",
    "edit_position_stops",
    "copy_precheck",
    "copy_start_or_adjust",
    "copy_poll",
    "copy_close",
    "search_instruments",
    "EtoroConfig",
    "EtoroConfigError",
    "EtoroAPIError",
]


def _client(cfg: EtoroConfig):
    return make_client(cfg)


def _base_payload(cfg: EtoroConfig) -> dict[str, Any]:
    return {
        "profile": cfg.profile,
        "environment": cfg.environment,
        "paper_guard": PAPER_GUARD,
    }


def check_status(config: EtoroConfig | None = None) -> dict[str, Any]:
    from src.trading.connectors.etoro.client import _missing_fields

    cfg = config or load_config()
    report: dict[str, Any] = {
        "status": "ok",
        "config": public_config(cfg),
        "sdk": {"package": "requests", "installed": True},
        "paper_guard": PAPER_GUARD,
        "base_url": BASE_URL,
    }
    missing_fields = _missing_fields(cfg)
    if missing_fields:
        report["status"] = "error"
        report["error"] = f"eToro connector not configured: missing {', '.join(missing_fields)}."
        return report
    try:
        portfolio = get_account_snapshot(cfg)
    except (EtoroConfigError, EtoroAPIError) as exc:
        report["status"] = "error"
        report["error"] = str(exc)
        return report
    except Exception as exc:  # noqa: BLE001
        report["status"] = "error"
        report["error"] = f"eToro connector check failed: {exc}"
        return report
    report["account"] = {
        "profile": cfg.profile,
        "portfolio_status": portfolio.get("status"),
    }
    return report


def get_account_snapshot(config: EtoroConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    client = _client(cfg)
    portfolio = client.request("GET", f"{info_root(cfg)}/portfolio", allow_retry=True)
    pnl = client.request("GET", f"{info_root(cfg)}/pnl", allow_retry=True)
    return {
        "status": "ok",
        **_base_payload(cfg),
        "account": {"portfolio": portfolio, "pnl": pnl},
    }


def get_positions(config: EtoroConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    payload = _client(cfg).request("GET", f"{info_root(cfg)}/portfolio", allow_retry=True)
    positions = _extract_positions(payload)
    return {"status": "ok", **_base_payload(cfg), "positions": positions}


def get_open_orders(config: EtoroConfig | None = None, *, include_executions: bool = False) -> dict[str, Any]:
    cfg = config or load_config()
    client = _client(cfg)
    orders_payload = client.request("GET", f"{info_root(cfg)}/pendingOrders", allow_retry=True)
    orders = _extract_orders(orders_payload)
    result: dict[str, Any] = {"status": "ok", **_base_payload(cfg), "orders": orders}
    if include_executions:
        try:
            history = client.request("GET", f"{info_root(cfg)}/history", allow_retry=True)
            result["history"] = history
        except EtoroAPIError as exc:
            result["history_error"] = str(exc)
    return result


def get_quote(symbol: str, *, config: EtoroConfig | None = None, **_: Any) -> dict[str, Any]:
    cfg = config or load_config()
    instrument_id = resolve_instrument_id(symbol, cfg)
    payload = _client(cfg).request(
        "GET",
        "/api/v1/market-data/instruments/rates",
        params={"instrumentIds": instrument_id},
        allow_retry=True,
    )
    quote = _first_rate(payload, instrument_id)
    return {
        "status": "ok",
        **_base_payload(cfg),
        "symbol": symbol,
        "instrument_id": instrument_id,
        "quote": quote,
    }


def get_historical_bars(
    symbol: str,
    *,
    config: EtoroConfig | None = None,
    period: str = "1d",
    limit: int = 90,
    **_: Any,
) -> dict[str, Any]:
    cfg = config or load_config()
    instrument_id = resolve_instrument_id(symbol, cfg)
    interval = _map_interval(period)
    count = max(1, min(int(limit), 1000))
    path = f"/api/v1/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"
    payload = _client(cfg).request("GET", path, allow_retry=True)
    bars = _normalize_candles(payload)
    return {
        "status": "ok",
        **_base_payload(cfg),
        "symbol": symbol,
        "instrument_id": instrument_id,
        "period": period,
        "bars": bars,
    }


def place_order(
    config: EtoroConfig | None = None,
    *,
    symbol: str,
    side: str,
    quantity: float | None = None,
    notional: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
    request_id: str | None = None,
) -> dict[str, Any]:
    return place_open_order(
        config,
        symbol=symbol,
        side=side,
        quantity=quantity,
        notional=notional,
        order_type=order_type,
        limit_price=limit_price,
        time_in_force=time_in_force,
        request_id=request_id,
    )


def cancel_order(
    config: EtoroConfig | None = None,
    order_id: str = "",
    *,
    symbol: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return cancel_open_order(config, order_id, symbol=symbol, request_id=request_id)


def _map_interval(period: str) -> str:
    token = str(period or "1d").strip().lower()
    mapping = {
        "1m": "OneMinute",
        "5m": "FiveMinutes",
        "15m": "FifteenMinutes",
        "30m": "ThirtyMinutes",
        "1h": "OneHour",
        "4h": "FourHours",
        "1d": "OneDay",
        "1w": "OneWeek",
    }
    if token in mapping:
        return mapping[token]
    raise EtoroAPIError(f"unsupported period {period!r}")


def _extract_positions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("positions", "clientPortfolio", "portfolio", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [_position_row(item) for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = value.get("positions")
                if isinstance(nested, list):
                    return [_position_row(item) for item in nested if isinstance(item, dict)]
    if isinstance(payload, list):
        return [_position_row(item) for item in payload if isinstance(item, dict)]
    return []


def _position_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_id": item.get("positionID") or item.get("positionId") or item.get("id"),
        "instrument_id": item.get("instrumentID") or item.get("instrumentId"),
        "symbol": item.get("instrumentDisplayName") or item.get("symbol") or item.get("instrumentName"),
        "units": item.get("units") or item.get("amountInUnits"),
        "open_rate": item.get("openRate") or item.get("openPrice"),
        "pnl": item.get("profit") or item.get("pnl"),
        "is_buy": item.get("isBuy"),
        "raw": item,
    }


def _extract_orders(payload: Any) -> list[dict[str, Any]]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("items", "data", "orders", "pendingOrders"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    return [
        {
            "order_id": item.get("orderID") or item.get("orderId") or item.get("id"),
            "instrument_id": item.get("instrumentID") or item.get("instrumentId"),
            "status": item.get("statusID") or item.get("status"),
            "raw": item,
        }
        for item in items
        if isinstance(item, dict)
    ]


def _first_rate(payload: Any, instrument_id: int) -> dict[str, Any]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("items", "data", "rates"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("instrumentID") or item.get("instrumentId")
        if item_id is not None and int(item_id) == instrument_id:
            return {
                "bid": item.get("bid") or item.get("bidRate"),
                "ask": item.get("ask") or item.get("askRate"),
                "last": item.get("lastExecution") or item.get("last") or item.get("rate"),
            }
    if isinstance(payload, dict):
        return {
            "bid": payload.get("bid"),
            "ask": payload.get("ask"),
            "last": payload.get("lastExecution") or payload.get("last"),
        }
    return {}


def _normalize_candles(payload: Any) -> list[dict[str, Any]]:
    candles: list[Any] = []
    if isinstance(payload, dict):
        raw = payload.get("candles") or payload.get("data")
        if isinstance(raw, list):
            candles = raw
    elif isinstance(payload, list):
        candles = payload
    rows: list[dict[str, Any]] = []
    for candle in candles:
        if not isinstance(candle, dict):
            continue
        rows.append(
            {
                "timestamp": candle.get("fromDate") or candle.get("timestamp") or candle.get("t"),
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": candle.get("close"),
                "volume": candle.get("volume"),
            }
        )
    return rows
