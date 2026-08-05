"""Instrument search and symbol resolution for eToro."""

from __future__ import annotations

from typing import Any

from src.trading.connectors.etoro.client import EtoroAPIError, EtoroConfig, make_client


def _base_payload(cfg: EtoroConfig) -> dict[str, Any]:
    return {
        "profile": cfg.profile,
        "environment": cfg.environment,
        "paper_guard": "path_separated_key_bound",
    }


def search_instruments(
    query: str,
    config: EtoroConfig | None = None,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    from src.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    payload = make_client(cfg).request(
        "GET",
        "/api/v1/market-data/search",
        params={"search": query, "limit": max(1, min(limit, 50))},
        allow_retry=True,
    )
    return {"status": "ok", **_base_payload(cfg), "instruments": _extract_items(payload)}


def resolve_instrument_id(symbol: str, cfg: EtoroConfig) -> int:
    token = str(symbol or "").strip()
    if token.isdigit():
        return int(token)
    result = search_instruments(token, cfg, limit=5)
    items = result.get("instruments") or []
    if not items:
        raise EtoroAPIError(f"instrument not found for symbol {token!r}")
    for item in items:
        if not isinstance(item, dict):
            continue
        display = str(item.get("displayName") or item.get("symbolFull") or item.get("symbol") or "").upper()
        if display == token.upper() or token.upper() in display:
            instrument_id = item.get("instrumentId") or item.get("instrumentID")
            if instrument_id is not None:
                return int(instrument_id)
    first = items[0]
    if isinstance(first, dict):
        instrument_id = first.get("instrumentId") or first.get("instrumentID")
        if instrument_id is not None:
            return int(instrument_id)
    raise EtoroAPIError(f"could not resolve instrument id for {token!r}")


def _extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "instruments", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []
