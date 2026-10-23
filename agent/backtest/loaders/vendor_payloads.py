"""Validate source envelopes once, before callers interpret empty vendor rows."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def _at(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"vendor response missing {'.'.join(keys)}")
        value = value[key]
    return value


def validate_payload(payload: Any, *, url: str, host_key: str, params: dict | None = None) -> Any:
    """Known provider layouts require explicit collections; an empty list is valid."""
    if isinstance(payload, dict):
        if (
            payload.get("error")
            or payload.get("success") is False
            or str(payload.get("status", "")).lower() in {"error", "failed", "failure"}
            or ("rc" in payload and str(payload["rc"]) != "0")
        ):
            raise ValueError(f"{host_key}: vendor returned a failure envelope")
    parts = urlsplit(url)
    host, path = parts.hostname or "", parts.path
    rows = None
    requires_collection = False
    expected = list
    if host.endswith("eastmoney.com"):
        if "/api/data/" in path:
            requires_collection = True
            rows = _at(payload, "result", "data")
        elif "/api/suggest/" in path:
            requires_collection = True
            rows = _at(payload, "QuotationCodeTable", "Data")
        elif path == "/report/list":
            requires_collection = True
            rows = _at(payload, "data")
        elif path == "/search/jsonp":
            requires_collection = True
            rows = _at(payload, "result", "cmsArticleWebOld")
        elif "kline" in path:
            requires_collection = True
            rows = _at(payload, "data", "klines")
        elif path.endswith(("/slist/get", "/clist/get")):
            requires_collection = True
            rows = _at(payload, "data", "diff")
            expected = (list, dict)
        elif path.endswith("/kamt/get"):
            _at(payload, "data", "hk2sh")
            _at(payload, "data", "hk2sz")
    elif host.endswith("iwencai.com"):
        requires_collection = True
        rows = _at(payload, "data", "answer")
    elif host == "gamma-api.polymarket.com" and path == "/public-search":
        requires_collection = True
        rows = _at(payload, "events")
    elif host.endswith("yahoo.com") and "/finance/search" in path:
        finance = payload.get("finance") if isinstance(payload, dict) else None
        if isinstance(finance, dict) and finance.get("error"):
            raise ValueError("yahoo: vendor returned a failure envelope")
        requires_collection = True
        rows = _at(payload, "news" if params and "newsCount" in params else "quotes")
    if rows is not None and not isinstance(rows, expected):
        raise ValueError(f"{host_key}: vendor collection has changed shape")
    # Explicit null is not the same response as a confirmed empty collection.
    if requires_collection and rows is None:
        raise ValueError(f"{host_key}: vendor returned a null collection")
    return payload
