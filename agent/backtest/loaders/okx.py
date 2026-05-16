"""OKX spot candle loader (crypto).

Uses OKX V5 public REST API (no auth).
Supports 1m/5m/15m/30m/1H/4H/1D.
Up to 300 bars per request; paginates with ``after`` for longer history.
"""

import os
import time
from typing import Dict, List, Optional

import pandas as pd
import requests

from backtest.loaders.base import validate_date_range
from backtest.loaders.registry import register

BASE_URL = "https://www.okx.com/api/v5"
_MAX_PER_PAGE = 300
# P12-b parity: OKX already sets a per-request timeout but had no retry
# budget, so a transient blip dropped the whole symbol and a slow tier
# could stall ~max_pages*timeout. Bound it like the ccxt loader.
_OKX_TIMEOUT = int(os.getenv("OKX_TIMEOUT_S", "15"))
_OKX_FETCH_BUDGET_S = float(os.getenv("OKX_FETCH_BUDGET_S", "60"))
_OKX_MAX_RETRIES = 3
_OKX_BACKOFF = (0.5, 1.5, 4.0)  # seconds; len == _OKX_MAX_RETRIES


@register
class DataLoader:
    """OKX crypto OHLCV loader."""

    name = "okx"
    markets = {"crypto"}
    requires_auth = False

    def is_available(self) -> bool:
        """Always available (public API, no auth)."""
        return True

    def __init__(self) -> None:
        """No credentials required for public candles."""
        pass

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        fields: Optional[List[str]] = None,
        interval: str = "1D",
    ) -> Dict[str, pd.DataFrame]:
        """Fetch crypto OHLCV via OKX public API.

        Args:
            codes: Symbols like ``["BTC-USDT", "ETH-USDT"]``.
            start_date: Start date (YYYY-MM-DD).
            end_date: End date (YYYY-MM-DD).
            fields: Ignored (OKX has no extra fields).
            interval: Bar size (1m/5m/15m/30m/1H/4H/1D), default ``1D``.

        Returns:
            Mapping symbol -> DataFrame.
        """
        validate_date_range(start_date, end_date)

        if fields:
            print(f"[WARN] OKX ignores extra fields: {fields}")

        valid_intervals = {"1m", "5m", "15m", "30m", "1H", "4H", "1D"}
        if interval not in valid_intervals:
            print(f"[WARN] unsupported OKX interval {interval}, using 1D")
            interval = "1D"

        codes = [c.replace("/", "-").upper() for c in codes]

        start_ts = int(pd.Timestamp(start_date).timestamp() * 1000)
        end_ts = int((pd.Timestamp(end_date) + pd.Timedelta(days=1)).timestamp() * 1000)

        max_pages = 200 if interval in ("1m", "5m") else 50 if interval in ("15m", "30m") else 20

        result: Dict[str, pd.DataFrame] = {}
        for symbol in codes:
            try:
                df = self._fetch_candles(symbol, start_ts, end_ts, interval, max_pages)
                if df is not None and not df.empty:
                    result[symbol] = df
            except Exception as exc:
                print(f"[WARN] failed to fetch {symbol}: {exc}")
        return result

    def _fetch_candles(
        self, inst_id: str, start_ts: int, end_ts: int,
        bar: str = "1D", max_pages: int = 20,
    ) -> Optional[pd.DataFrame]:
        """Paginated candle download.

        Args:
            inst_id: OKX instrument id.
            start_ts: Start time (ms).
            end_ts: End time (ms).
            bar: Bar size.
            max_pages: Max pagination rounds.

        Returns:
            OHLCV DataFrame or None.
        """
        all_rows: list = []
        after = str(end_ts)
        deadline = time.monotonic() + _OKX_FETCH_BUDGET_S

        for _ in range(max_pages):
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"OKX fetch for {inst_id} exceeded "
                    f"{_OKX_FETCH_BUDGET_S:.0f}s budget"
                )
            params = {
                "instId": inst_id,
                "bar": bar,
                "limit": str(_MAX_PER_PAGE),
                "after": after,
            }
            data = None
            for attempt in range(_OKX_MAX_RETRIES + 1):
                try:
                    resp = requests.get(
                        f"{BASE_URL}/market/candles",
                        params=params,
                        timeout=_OKX_TIMEOUT,
                    )
                    data = resp.json()
                    break
                except requests.RequestException as exc:
                    remaining = deadline - time.monotonic()
                    if attempt == _OKX_MAX_RETRIES or remaining <= 0:
                        raise TimeoutError(
                            f"OKX fetch for {inst_id} failed after "
                            f"{attempt + 1} attempt(s): {exc}"
                        ) from exc
                    time.sleep(min(_OKX_BACKOFF[attempt], max(0.0, remaining)))
            if data.get("code") != "0" or not data.get("data"):
                break

            rows = data["data"]
            rows = [r for r in rows if r[8] == "1"]
            all_rows.extend(rows)

            oldest_ts = int(rows[-1][0]) if rows else start_ts
            if oldest_ts <= start_ts or len(data["data"]) < _MAX_PER_PAGE:
                break
            after = str(oldest_ts)

        if not all_rows:
            print(f"[WARN] OKX empty response: {inst_id}")
            return None

        columns = ["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"]
        df = pd.DataFrame(all_rows, columns=columns)
        df["trade_date"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["vol"], errors="coerce").fillna(0)
        df = df.set_index("trade_date").sort_index()

        start_dt = pd.Timestamp(start_ts, unit="ms")
        end_dt = pd.Timestamp(end_ts, unit="ms")
        df = df[(df.index >= start_dt) & (df.index < end_dt)]

        df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["open", "high", "low", "close"])
        return df if not df.empty else None
