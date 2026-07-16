"""FTShare SDK-backed A-share and Hong Kong equity OHLCV loader.

FTShare is an optional dependency distributed from its upstream GitHub
repository.  Importing this module never imports the SDK, so installations
that do not opt into FTShare keep the normal loader fallback behavior.

SDK: https://github.com/FTShare-Lab/FTShare-python-sdk (MIT)
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

import pandas as pd

from backtest.loaders.base import (
    NoAvailableSourceError,
    cached_loader_fetch,
    validate_date_range,
)
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

_OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
_REQUEST_TIMEOUT_S = 20
_INSTALL_COMMAND = (
    'pip install "ftshare @ '
    'git+https://github.com/FTShare-Lab/FTShare-python-sdk.git@v0.1.1"'
)

_A_SHARE_RE = re.compile(
    r"^(?P<code>\d{6})\.(?P<exchange>SH|XSHG|SZ|XSHE|BJ|BJSE)$",
    re.IGNORECASE,
)
_HK_RE = re.compile(r"^(?P<code>\d{1,5})\.HK$", re.IGNORECASE)

_A_SHARE_INTERVALS = {
    "1D": "Day",
    "1W": "Week",
    "1M": "Month",
}
_HK_INTERVALS = {
    "1D": "day",
    "1M": "month",
}


class FTShareDependencyError(RuntimeError):
    """Raised when the optional FTShare SDK is not installed."""


def _require_ftshare() -> Any:
    """Import and return the optional ``ftshare`` SDK module.

    Returns:
        Imported ``ftshare`` module.

    Raises:
        FTShareDependencyError: If the SDK is not installed.
    """
    try:
        import ftshare  # noqa: PLC0415
    except ImportError as exc:
        raise FTShareDependencyError(
            f"The 'ftshare' SDK is not installed. Run: {_INSTALL_COMMAND}"
        ) from exc
    return ftshare


def _normalize_symbol(code: str) -> tuple[str, str] | None:
    """Convert a project symbol to the FTShare SDK format.

    Args:
        code: Project symbol such as ``600000.SH`` or ``700.HK``.

    Returns:
        ``(market, sdk_symbol)`` or ``None`` for an unsupported symbol.
    """
    value = code.strip().upper()
    a_share = _A_SHARE_RE.fullmatch(value)
    if a_share:
        exchange = a_share.group("exchange")
        suffix = {
            "SH": "XSHG",
            "XSHG": "XSHG",
            "SZ": "XSHE",
            "XSHE": "XSHE",
            "BJ": "BJSE",
            "BJSE": "BJSE",
        }[exchange]
        return "a_share", f"{a_share.group('code')}.{suffix}"

    hk = _HK_RE.fullmatch(value)
    if hk:
        return "hk_equity", f"{int(hk.group('code')):05d}.HK"
    return None


def _normalize_frame(frame: pd.DataFrame, *, market: str) -> pd.DataFrame:
    """Normalize an FTShare response to the repository OHLCV contract.

    Args:
        frame: DataFrame returned by the FTShare SDK.
        market: ``a_share`` or ``hk_equity``.

    Returns:
        Timezone-naive, ascending OHLCV frame indexed by ``trade_date``.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)

    result = frame.copy()
    if market == "a_share":
        timestamp_column = next(
            (name for name in ("open_ts_ms", "close_ts_ms") if name in result),
            None,
        )
        if timestamp_column is None:
            return pd.DataFrame(columns=_OHLCV_COLUMNS)
        trade_dates = (
            pd.to_datetime(
                result[timestamp_column], unit="ms", errors="coerce", utc=True
            )
            .dt.tz_convert("Asia/Shanghai")
            .dt.tz_localize(None)
            .dt.normalize()
        )
    else:
        if "date" not in result:
            return pd.DataFrame(columns=_OHLCV_COLUMNS)
        trade_dates = pd.to_datetime(result["date"], errors="coerce")

    missing = [column for column in _OHLCV_COLUMNS if column not in result]
    if missing:
        logger.warning("FTShare response missing OHLCV columns: %s", missing)
        return pd.DataFrame(columns=_OHLCV_COLUMNS)

    normalized = result[_OHLCV_COLUMNS].copy()
    for column in _OHLCV_COLUMNS:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized.index = pd.DatetimeIndex(trade_dates, name="trade_date")
    normalized = normalized[~normalized.index.isna()]
    normalized = normalized.dropna(subset=["open", "high", "low", "close"])
    normalized["volume"] = normalized["volume"].fillna(0.0)
    return normalized[~normalized.index.duplicated(keep="last")].sort_index()


def _a_share_interval(interval: str) -> str:
    token = interval.strip()
    if token not in _A_SHARE_INTERVALS:
        raise NoAvailableSourceError(
            f"unsupported FTShare A-share interval: {interval!r}; "
            f"supported intervals: {sorted(_A_SHARE_INTERVALS)}"
        )
    return _A_SHARE_INTERVALS[token]


def _hk_interval(interval: str) -> str:
    token = interval.strip()
    if token not in _HK_INTERVALS:
        raise NoAvailableSourceError(
            f"unsupported FTShare HK interval: {interval!r}; "
            f"supported intervals: {sorted(_HK_INTERVALS)}"
        )
    return _HK_INTERVALS[token]


@register
class DataLoader:
    """Fetch A-share and Hong Kong equity bars through the FTShare SDK."""

    name = "feitu"
    markets = {"a_share", "hk_equity"}
    requires_auth = False

    def is_available(self) -> bool:
        """Return whether the optional SDK is importable without network I/O."""
        try:
            _require_ftshare()
            return True
        except FTShareDependencyError:
            return False

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch normalized OHLCV history through FTShare.

        Args:
            codes: A-share or HK symbols in project format.
            start_date: Inclusive start date in ``YYYY-MM-DD`` format.
            end_date: Inclusive end date in ``YYYY-MM-DD`` format.
            interval: ``1D``, ``1W`` or ``1M`` for A-shares; ``1D`` or
                ``1M`` for Hong Kong equities.
            fields: Ignored; included for loader protocol compatibility.

        Returns:
            Mapping of the original input symbol to normalized OHLCV data.
        """
        del fields
        if not codes:
            return {}
        validate_date_range(start_date, end_date)

        supported = [(code, _normalize_symbol(code)) for code in codes]
        pending = [(code, symbol) for code, symbol in supported if symbol]
        for code, symbol in supported:
            if symbol is None:
                logger.warning("FTShare does not support symbol %s", code)
        if not pending:
            return {}

        client: Any | None = None

        def get_client() -> Any:
            nonlocal client
            if client is None:
                ftshare = _require_ftshare()
                try:
                    client = ftshare.market_api(timeout=_REQUEST_TIMEOUT_S)
                except Exception as exc:
                    raise NoAvailableSourceError(
                        f"Cannot initialise FTShare SDK client: {exc}"
                    ) from exc
            return client

        results: dict[str, pd.DataFrame] = {}
        try:
            for code, normalized_symbol in pending:
                assert normalized_symbol is not None
                market, sdk_symbol = normalized_symbol
                try:
                    frame = cached_loader_fetch(
                        source=self.name,
                        symbol=code,
                        timeframe=interval,
                        start_date=start_date,
                        end_date=end_date,
                        fields=None,
                        fetch=lambda market=market, sdk_symbol=sdk_symbol: self._fetch_one(
                            get_client(),
                            market=market,
                            symbol=sdk_symbol,
                            start_date=start_date,
                            end_date=end_date,
                            interval=interval,
                        ),
                    )
                except Exception as exc:
                    logger.warning("FTShare failed for %s: %s", code, exc)
                    continue
                if frame is not None and not frame.empty:
                    results[code] = frame
        finally:
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    close()
        return results

    @staticmethod
    def _fetch_one(
        client: Any,
        *,
        market: str,
        symbol: str,
        start_date: str,
        end_date: str,
        interval: str,
    ) -> pd.DataFrame:
        """Fetch and normalize one FTShare symbol."""
        if market == "a_share":
            frame = client.stock_ohlcs(
                symbol=symbol,
                since=pd.Timestamp(start_date).strftime("%Y%m%d"),
                until=pd.Timestamp(end_date).strftime("%Y%m%d"),
                interval=_a_share_interval(interval),
                adjust="Forward",
            )
        else:
            frame = client.hk_candlesticks(
                trade_code=symbol,
                interval_unit=_hk_interval(interval),
                since_date=pd.Timestamp(start_date).strftime("%Y-%m-%d"),
                until_date=pd.Timestamp(end_date).strftime("%Y-%m-%d"),
                interval_value=1,
                adjust_kind="forward",
            )
        return _normalize_frame(frame, market=market)
