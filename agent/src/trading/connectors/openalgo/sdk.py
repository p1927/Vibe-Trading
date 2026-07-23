"""OpenAlgo connector via REST ``/api/v1/*``.

Paper vs live follows OpenAlgo's Analyze toggle (not a separate host). Paper
profiles expect ``analyze_mode=True``; live profiles expect it OFF.

India and US symbols route through OpenAlgo quotes/history/orders. US execution
uses the Alpaca broker plugin inside OpenAlgo.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

import requests

from src.config.paths import get_runtime_root

CONFIG_FILENAME = "openalgo.json"

PROFILE_ENVIRONMENTS = {
    "paper": "paper",
    "live-readonly": "live",
    "live": "live",
}

_IN_INDEX = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "NIFTY50"})
_US_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")

_OPEN_STATUS = frozenset({"open", "trigger pending", "pending", "partially filled"})


class OpenAlgoConfigError(RuntimeError):
    """Raised when connector configuration is missing or invalid."""


@dataclass(frozen=True)
class OpenAlgoConfig:
    """OpenAlgo connector settings."""

    host: str = "http://127.0.0.1:5001"
    api_key: str = ""
    profile: str = "paper"
    timeout: float = 20.0
    readonly: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> "OpenAlgoConfig":
        payload = dict(data or {})
        profile = str(payload.get("profile") or "paper").strip().lower()
        if profile not in PROFILE_ENVIRONMENTS:
            raise OpenAlgoConfigError("profile must be 'paper', 'live-readonly' or 'live'")
        host = str(payload.get("host") or os.getenv("OPENALGO_HOST") or "http://127.0.0.1:5001").rstrip("/")
        api_key = str(payload.get("api_key") or os.getenv("OPENALGO_API_KEY") or "").strip()
        return cls(
            host=host,
            api_key=api_key,
            profile=profile,
            timeout=float(payload.get("timeout") or 20.0),
            readonly=bool(payload.get("readonly", True)),
        )

    def with_overrides(
        self,
        *,
        host: str | None = None,
        api_key: str | None = None,
        profile: str | None = None,
    ) -> "OpenAlgoConfig":
        payload = asdict(self)
        if host is not None:
            payload["host"] = host.rstrip("/")
        if api_key is not None:
            payload["api_key"] = api_key
        if profile is not None:
            payload["profile"] = profile
        return OpenAlgoConfig.from_mapping(payload)

    @property
    def environment(self) -> str:
        return PROFILE_ENVIRONMENTS.get(self.profile, "paper")

    @property
    def is_paper(self) -> bool:
        return self.environment == "paper"


_OVERRIDE_KEYS = ("host", "api_key", "profile")


def build_config(
    profile_config: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> OpenAlgoConfig:
    base = asdict(load_config())
    for key, value in dict(profile_config or {}).items():
        if value is not None:
            base[key] = value
    cfg = OpenAlgoConfig.from_mapping(base)
    clean = {k: v for k, v in dict(overrides or {}).items() if k in _OVERRIDE_KEYS and v not in (None, "")}
    return cfg.with_overrides(**clean) if clean else cfg


def config_path() -> Path:
    return get_runtime_root() / CONFIG_FILENAME


def load_config() -> OpenAlgoConfig:
    path = config_path()
    if not path.exists():
        return OpenAlgoConfig()
    try:
        return OpenAlgoConfig.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise OpenAlgoConfigError(f"invalid OpenAlgo config at {path}: {exc}") from exc


def save_config(config: OpenAlgoConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _client(cfg: OpenAlgoConfig) -> "_RestClient":
    return _RestClient(cfg)


def _missing_fields(cfg: OpenAlgoConfig) -> list[str]:
    missing: list[str] = []
    if not cfg.api_key:
        missing.append("api_key (or OPENALGO_API_KEY)")
    if not cfg.host:
        missing.append("host (or OPENALGO_HOST)")
    return missing


def _public_config(cfg: OpenAlgoConfig) -> dict[str, Any]:
    data = asdict(cfg)
    if data.get("api_key"):
        data["api_key"] = data["api_key"][:4] + "***"
    return data


def _broker_display_name(broker: str | None) -> str | None:
    if not broker:
        return None
    try:
        from trade_integrations.dataflows.broker_charges.calculate import load_presets, normalize_broker_id

        broker_id = normalize_broker_id(broker)
        presets = load_presets()
        display = presets.get("brokers", {}).get(broker_id, {}).get("display_name")
        if display:
            return str(display)
        return broker_id.replace("_", " ").title()
    except Exception:  # noqa: BLE001
        return broker.replace("_", " ").title()


def _brokerinfo(client: "_RestClient") -> dict[str, Any]:
    try:
        body = client.post("brokerinfo", {})
    except RuntimeError:
        ping = client.post("ping", {})
        data = ping.get("data") if isinstance(ping.get("data"), dict) else {}
        broker = str(data.get("broker") or "").strip().lower() or None
        return {
            "broker": broker,
            "broker_display": _broker_display_name(broker),
            "token_sync_ok": None,
            "token_sync_warning": None,
        }

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    broker = str(data.get("broker") or data.get("session_broker") or data.get("configured_broker") or "").strip().lower()
    broker = broker or None
    token_sync_ok = data.get("token_sync_ok")
    warning: str | None = None
    if data.get("is_env_token_broker"):
        if token_sync_ok is False:
            warning = (
                f"{_broker_display_name(broker) or broker} access token in OpenAlgo .env "
                "does not match the auth database — sync token in OpenAlgo UI."
            )
        elif not data.get("env_secret_set"):
            warning = (
                f"{_broker_display_name(broker) or broker} access token missing in OpenAlgo .env "
                "(BROKER_API_SECRET)."
            )
    return {
        "broker": broker,
        "broker_display": _broker_display_name(broker),
        "configured_broker": data.get("configured_broker"),
        "token_sync_ok": token_sync_ok,
        "token_sync_warning": warning,
    }


def _resolve_india(symbol: str) -> tuple[str, str]:
    try:
        from trade_integrations.openalgo.symbols import resolve_openalgo_symbol

        return resolve_openalgo_symbol(symbol)
    except Exception:  # noqa: BLE001
        raw = symbol.strip().upper()
        if raw in _IN_INDEX or raw.startswith("^"):
            mapping = {
                "^NSEI": ("NIFTY", "NSE_INDEX"),
                "NIFTY50": ("NIFTY", "NSE_INDEX"),
                "^BSESN": ("SENSEX", "BSE_INDEX"),
                "NIFTY": ("NIFTY", "NSE_INDEX"),
                "BANKNIFTY": ("BANKNIFTY", "NSE_INDEX"),
                "FINNIFTY": ("FINNIFTY", "NSE_INDEX"),
                "MIDCPNIFTY": ("MIDCPNIFTY", "NSE_INDEX"),
                "SENSEX": ("SENSEX", "BSE_INDEX"),
            }
            if raw in mapping:
                return mapping[raw]
        if raw.endswith(".NS"):
            return raw[:-3], "NSE"
        if raw.endswith(".BO"):
            return raw[:-3], "BSE"
        return raw, "NSE"


def _is_us_symbol(symbol: str) -> bool:
    raw = symbol.strip().upper()
    if raw.endswith((".NS", ".BO")) or raw in _IN_INDEX or raw.startswith("^"):
        return False
    try:
        from trade_integrations.dataflows.company_research.market import Market, detect_market

        return detect_market(raw) == Market.US
    except Exception:  # noqa: BLE001
        return bool(_US_SYMBOL_RE.fullmatch(raw))


def _assert_mode(cfg: OpenAlgoConfig, analyze: bool) -> dict[str, Any] | None:
    if cfg.is_paper and not analyze:
        return {
            "status": "error",
            "error": (
                "OpenAlgo Analyze mode is OFF but profile is paper. "
                "Enable Analyze in OpenAlgo UI (or toggle analyzer) before using openalgo-paper-*."
            ),
            "analyze_mode": analyze,
        }
    if not cfg.is_paper and analyze:
        return {
            "status": "error",
            "error": (
                "OpenAlgo Analyze mode is ON but profile is live-readonly. "
                "Switch OpenAlgo to Live mode for live broker reads."
            ),
            "analyze_mode": analyze,
        }
    return None


class _RestClient:
    def __init__(self, cfg: OpenAlgoConfig) -> None:
        self.cfg = cfg

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.cfg.host}/api/v1/{path.lstrip('/')}"
        body = {**payload, "apikey": self.cfg.api_key}
        try:
            response = requests.post(url, json=body, timeout=self.cfg.timeout)
            parsed = response.json() if response.content else {}
        except requests.RequestException as exc:
            raise RuntimeError(f"OpenAlgo request failed ({url}): {exc}") from exc
        if not isinstance(parsed, dict):
            return {"status": "error", "message": "non-JSON response"}
        if not response.ok:
            message = parsed.get("message") or parsed.get("error") or f"HTTP {response.status_code}"
            raise RuntimeError(str(message))
        if parsed.get("status") not in (None, "success", "ok"):
            message = parsed.get("message") or parsed.get("error") or "OpenAlgo error"
            raise RuntimeError(str(message))
        return parsed

    def analyzer_status(self) -> bool:
        body = self.post("analyzer", {})
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        return bool(data.get("analyze_mode"))

    def funds(self) -> dict[str, Any]:
        body = self.post("funds", {})
        data = body.get("data")
        return data if isinstance(data, dict) else body

    def positions(self) -> list[dict[str, Any]]:
        body = self.post("positionbook", {})
        rows = body.get("data")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        return []

    def orders(self) -> list[dict[str, Any]]:
        body = self.post("orderbook", {})
        data = body.get("data")
        if isinstance(data, dict):
            orders = data.get("orders")
            if isinstance(orders, list):
                return [row for row in orders if isinstance(row, dict)]
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def quote(self, symbol: str, exchange: str) -> dict[str, Any]:
        body = self.post("quotes", {"symbol": symbol, "exchange": exchange})
        data = body.get("data")
        return data if isinstance(data, dict) else {}


def _marketcontext(client: "_RestClient") -> dict[str, Any]:
    try:
        body = client.post("marketcontext", {})
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    if str(body.get("status") or "").lower() != "success" or not data:
        message = body.get("message") or body.get("error") or "MarketContext unavailable"
        return {"status": "error", "error": str(message)}
    return {"status": "success", "data": data}


def market_context(config: OpenAlgoConfig | None = None) -> dict[str, Any]:
    """Fetch authoritative OpenAlgo market context (broker, analyze mode, simulator)."""
    cfg = config or load_config()
    missing = _missing_fields(cfg)
    if missing:
        return {
            "status": "error",
            "error": f"OpenAlgo connector not configured: missing {', '.join(missing)}.",
        }
    try:
        return _marketcontext(_client(cfg))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


def check_status(config: OpenAlgoConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    report: dict[str, Any] = {
        "status": "ok",
        "config": _public_config(cfg),
        "host": cfg.host,
        "paper_guard": "openalgo_analyzer_toggle",
    }
    missing = _missing_fields(cfg)
    if missing:
        report["status"] = "error"
        report["error"] = f"OpenAlgo connector not configured: missing {', '.join(missing)}."
        return report

    try:
        client = _client(cfg)
        analyze = client.analyzer_status()
        report["analyze_mode"] = analyze
        mode_error = _assert_mode(cfg, analyze)
        if mode_error:
            report["status"] = "error"
            report["error"] = mode_error["error"]
            return report
        funds = client.funds()
        broker_meta = _brokerinfo(client)
        report.update(broker_meta)
        report["switch_url"] = f"{cfg.host.rstrip('/')}/"
        report["account"] = {
            "profile": cfg.profile,
            "is_paper": cfg.is_paper,
            "analyze_mode": analyze,
            "availablecash": funds.get("availablecash") or funds.get("available_cash"),
            "utiliseddebits": funds.get("utiliseddebits") or funds.get("utilised_debits"),
        }
        if broker_meta.get("token_sync_warning"):
            report["warning"] = broker_meta["token_sync_warning"]
    except Exception as exc:  # noqa: BLE001
        report["status"] = "error"
        report["error"] = str(exc)
    return report


def get_account_snapshot(config: OpenAlgoConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    client = _client(cfg)
    analyze = client.analyzer_status()
    mode_error = _assert_mode(cfg, analyze)
    if mode_error:
        return mode_error
    funds = client.funds()
    return {
        "status": "ok",
        "profile": cfg.profile,
        "is_paper": cfg.is_paper,
        "analyze_mode": analyze,
        "host": cfg.host,
        "account": {
            "currency": "INR",
            "cash": funds.get("availablecash") or funds.get("available_cash"),
            "utilised_debits": funds.get("utiliseddebits") or funds.get("utilised_debits"),
            "m2mrealized": funds.get("m2mrealized"),
            "m2munrealized": funds.get("m2munrealized"),
        },
    }


def get_positions(config: OpenAlgoConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    client = _client(cfg)
    mode_error = _assert_mode(cfg, client.analyzer_status())
    if mode_error:
        return mode_error
    rows = []
    for item in client.positions():
        rows.append(
            {
                "symbol": item.get("symbol") or item.get("tsym"),
                "exchange": item.get("exchange") or item.get("exch"),
                "product_type": item.get("product") or item.get("prd"),
                "quantity": item.get("quantity") or item.get("netqty"),
                "average_cost": item.get("averageprice") or item.get("netavgprc"),
                "ltp": item.get("ltp") or item.get("lp"),
                "unrealized_pnl": item.get("pnl") or item.get("urmtom"),
            }
        )
    return {"status": "ok", "profile": cfg.profile, "is_paper": cfg.is_paper, "positions": rows}


def get_open_orders(
    config: OpenAlgoConfig | None = None,
    *,
    include_executions: bool = False,
) -> dict[str, Any]:
    cfg = config or load_config()
    client = _client(cfg)
    mode_error = _assert_mode(cfg, client.analyzer_status())
    if mode_error:
        return mode_error

    open_rows: list[dict[str, Any]] = []
    closed_rows: list[dict[str, Any]] = []
    for item in client.orders():
        status = str(item.get("status") or "").strip().lower()
        row = {
            "order_id": str(item.get("orderid") or item.get("order_id") or ""),
            "symbol": item.get("symbol") or item.get("tsym"),
            "exchange": item.get("exchange") or item.get("exch"),
            "side": str(item.get("action") or item.get("side") or "").lower(),
            "order_type": str(item.get("pricetype") or item.get("order_type") or ""),
            "quantity": item.get("quantity") or item.get("qty"),
            "filled_qty": item.get("filledqty") or item.get("filled_quantity"),
            "limit_price": item.get("price"),
            "status": status,
        }
        if status in _OPEN_STATUS:
            open_rows.append(row)
        elif include_executions:
            closed_rows.append(row)

    result: dict[str, Any] = {
        "status": "ok",
        "profile": cfg.profile,
        "is_paper": cfg.is_paper,
        "open_orders": open_rows,
    }
    if include_executions:
        result["executions"] = closed_rows
    return result


def get_quote(symbol: str, *, config: OpenAlgoConfig | None = None, **_: Any) -> dict[str, Any]:
    cfg = config or load_config()
    clean = symbol.strip().upper()
    if not clean:
        return {"status": "error", "error": "symbol is required"}

    if _is_us_symbol(clean):
        return _us_quote(clean, cfg)

    oa_symbol, exchange = _resolve_india(clean)
    try:
        client = _client(cfg)
        if not cfg.is_paper:
            mode_error = _assert_mode(cfg, client.analyzer_status())
            if mode_error:
                return mode_error
        data = client.quote(oa_symbol, exchange)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}

    ltp = data.get("ltp") or data.get("last_price")
    return {
        "status": "ok",
        "symbol": clean,
        "market": "IN",
        "quote": {
            "ltp": ltp,
            "bid": data.get("bid") or data.get("bp"),
            "ask": data.get("ask") or data.get("ap"),
            "open": data.get("open"),
            "high": data.get("high"),
            "low": data.get("low"),
            "volume": data.get("volume"),
        },
    }


def _us_quote(symbol: str, cfg: OpenAlgoConfig) -> dict[str, Any]:
    try:
        from trade_integrations.openalgo.market_data import fetch_openalgo_quote

        quote = fetch_openalgo_quote(symbol)
        if not quote or quote.get("ltp") is None:
            return {"status": "error", "error": f"no OpenAlgo quote for {symbol}"}
        return {
            "status": "ok",
            "symbol": symbol,
            "market": "US",
            "profile": cfg.profile,
            "is_paper": True,
            "quote": {
                "ltp": quote.get("ltp"),
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "volume": quote.get("volume"),
                "feed": quote.get("feed") or "openalgo",
            },
            "note": "US quotes via OpenAlgo Alpaca broker plugin.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}


def get_historical_bars(
    symbol: str,
    *,
    config: OpenAlgoConfig | None = None,
    period: str = "1d",
    limit: int = 90,
    **_: Any,
) -> dict[str, Any]:
    cfg = config or load_config()
    clean = symbol.strip().upper()
    if not clean:
        return {"status": "error", "error": "symbol is required"}

    if _is_us_symbol(clean):
        return _us_history(clean, period=period, limit=limit)

    oa_symbol, exchange = _resolve_india(clean)
    interval = _history_interval(period)
    end = date.today()
    start = end - timedelta(days=max(int(limit), 1))
    try:
        client = _client(cfg)
        if not cfg.is_paper:
            mode_error = _assert_mode(cfg, client.analyzer_status())
            if mode_error:
                return mode_error
        body = client.post(
            "history",
            {
                "symbol": oa_symbol,
                "exchange": exchange,
                "interval": interval,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        rows = body.get("data") or []
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}

    bars = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        bars.append(
            {
                "time": item.get("timestamp") or item.get("time"),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("volume"),
            }
        )
    return {"status": "ok", "symbol": clean, "market": "IN", "period": period, "bars": bars}


def _us_history(symbol: str, *, period: str, limit: int) -> dict[str, Any]:
    cfg = load_config()
    interval = _history_interval(period)
    end = date.today()
    start = end - timedelta(days=max(int(limit), 1))
    try:
        client = _client(cfg)
        if not cfg.is_paper:
            mode_error = _assert_mode(cfg, client.analyzer_status())
            if mode_error:
                return mode_error
        body = client.post(
            "history",
            {
                "symbol": symbol,
                "exchange": "NASDAQ",
                "interval": interval,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        rows = body.get("data") or []
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}

    bars = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        bars.append(
            {
                "timestamp": item.get("timestamp") or item.get("date"),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("volume"),
            }
        )
    return {
        "status": "ok",
        "symbol": symbol,
        "market": "US",
        "period": period,
        "bars": bars,
        "note": "US history via OpenAlgo Alpaca broker plugin.",
    }


def _history_interval(period: str) -> str:
    token = period.strip()
    mapping = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "1d": "D",
        "1w": "W",
    }
    return mapping.get(token, "D")


def place_order(
    config: OpenAlgoConfig | None = None,
    *,
    symbol: str,
    side: str,
    quantity: float | None = None,
    notional: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
) -> dict[str, Any]:
    """Place a simple NSE/BSE equity order via OpenAlgo (paper/analyzer only)."""
    del time_in_force, notional  # CNC day orders only in v1
    cfg = config or load_config()
    if not cfg.is_paper:
        return {
            "status": "error",
            "error": "OpenAlgo connector order placement is paper-only; use openalgo-paper-trade profile.",
        }

    clean = symbol.strip().upper()
    if not clean or _is_us_symbol(clean):
        return {"status": "error", "error": "US order placement is not supported on OpenAlgo connector profiles."}

    side_token = str(side or "").strip().upper()
    if side_token not in ("BUY", "SELL"):
        return {"status": "error", "error": "side must be 'buy' or 'sell'"}

    if quantity is None:
        return {"status": "error", "error": "quantity is required for OpenAlgo orders"}
    try:
        qty = int(quantity)
        if qty <= 0:
            return {"status": "error", "error": "quantity must be positive"}
    except (TypeError, ValueError):
        return {"status": "error", "error": "quantity must be an integer"}

    type_token = str(order_type or "").strip().lower()
    if type_token not in ("market", "limit"):
        return {"status": "error", "error": "order_type must be 'market' or 'limit'"}

    oa_symbol, exchange = _resolve_india(clean)
    if exchange.endswith("_INDEX"):
        return {"status": "error", "error": "index symbols cannot be ordered; use an equity or F&O symbol."}

    payload: dict[str, Any] = {
        "strategy": "vibe_connector",
        "symbol": oa_symbol,
        "exchange": exchange if exchange in ("NSE", "BSE") else "NSE",
        "action": side_token,
        "product": "CNC",
        "pricetype": "MARKET" if type_token == "market" else "LIMIT",
        "quantity": str(qty),
    }
    if type_token == "limit":
        if limit_price is None:
            return {"status": "error", "error": "limit order requires limit_price"}
        payload["price"] = str(limit_price)

    try:
        client = _client(cfg)
        analyze = client.analyzer_status()
        mode_error = _assert_mode(cfg, analyze)
        if mode_error:
            return mode_error
        body = client.post("placeorder", payload)
        order_id = body.get("orderid") or body.get("order_id")
        if not order_id and isinstance(body.get("data"), dict):
            order_id = body["data"].get("orderid")
        if not order_id:
            return {"status": "error", "error": body.get("message") or "OpenAlgo did not return orderid"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}

    return {
        "status": "ok",
        "order_id": str(order_id),
        "symbol": clean,
        "side": side_token.lower(),
        "profile": cfg.profile,
        "is_paper": True,
        "order_type": type_token,
        "quantity": qty,
        "limit_price": limit_price,
        "analyze_mode": True,
    }


def cancel_order(
    config: OpenAlgoConfig | None = None,
    order_id: str = "",
    *,
    symbol: str | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    if not cfg.is_paper:
        return {"status": "error", "error": "OpenAlgo connector cancel is paper-only."}

    clean_id = str(order_id or "").strip()
    if not clean_id:
        return {"status": "error", "error": "order_id is required"}

    oa_symbol, exchange = _resolve_india(symbol or "")
    try:
        client = _client(cfg)
        mode_error = _assert_mode(cfg, client.analyzer_status())
        if mode_error:
            return mode_error
        client.post(
            "cancelorder",
            {
                "strategy": "vibe_connector",
                "orderid": clean_id,
                "symbol": oa_symbol or "UNKNOWN",
                "exchange": exchange if exchange in ("NSE", "BSE") else "NSE",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}

    return {
        "status": "ok",
        "order_id": clean_id,
        "symbol": symbol,
        "profile": cfg.profile,
        "is_paper": True,
        "cancelled": True,
    }
