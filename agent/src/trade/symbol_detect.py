"""Detect India market tickers mentioned in chat messages."""

from __future__ import annotations

import re

_IN_INDICES = frozenset(
    {
        "NIFTY",
        "NIFTY50",
        "BANKNIFTY",
        "FINNIFTY",
        "MIDCPNIFTY",
        "SENSEX",
        "^NSEI",
        "^BSESN",
    }
)

_STOPWORDS = frozenset(
    {
        "AI",
        "API",
        "ATM",
        "BUY",
        "CE",
        "CEO",
        "CFO",
        "ETF",
        "FNO",
        "FO",
        "GST",
        "HOLD",
        "IV",
        "LTP",
        "MIS",
        "OI",
        "PE",
        "PCR",
        "POT",
        "ROI",
        "SELL",
        "STT",
        "USD",
        "THE",
        "AND",
        "FOR",
        "NOT",
        "YOU",
        "ALL",
        "CAN",
        "HOW",
        "WHY",
        "WHAT",
        "WHEN",
        "WHO",
        "ARE",
        "WAS",
        "HAS",
        "HAD",
        "ITS",
        "OUR",
        "OUT",
        "NEW",
        "OLD",
        "TOP",
        "LOW",
        "MAX",
        "MIN",
        "NET",
        "GROSS",
        "PLAN",
        "TRADE",
        "STOCK",
        "CALL",
        "PUT",
        "SHOULD",
        "WOULD",
        "COULD",
        "HELLO",
        "WORLD",
        "PLEASE",
        "THANK",
        "THANKS",
        "HELP",
        "SHOW",
        "TELL",
        "GIVE",
        "WANT",
        "NEED",
        "LIKE",
        "JUST",
        "ALSO",
        "THAT",
        "THIS",
        "WITH",
        "FROM",
        "INTO",
        "YOUR",
        "MY",
        "ME",
        "WE",
        "THEY",
        "THEM",
        "THEN",
        "THAN",
        "BUT",
        "OR",
        "IF",
        "SO",
        "DO",
        "DOES",
        "DID",
        "WILL",
        "BE",
        "IS",
        "AM",
        "AN",
        "AS",
        "AT",
        "BY",
        "ON",
        "IN",
        "IT",
        "TO",
        "OF",
        "CURRENT",
        "PREVIOUS",
        "LATEST",
        "TODAY",
        "PRIOR",
    }
)

_TICKER_RE = re.compile(r"\b([A-Z][A-Z0-9&.-]{1,14})\b")
_OPTIONS_HINT = re.compile(
    r"\b(option|options|call|put|strike|expiry|expir|iron\s+condor|straddle|strangle|"
    r"butterfly|spread|F&O|FNO|chain|IV|theta|delta|gamma|vega|OI|open\s+interest)\b",
    re.I,
)
_FINALIZE_HINT = re.compile(
    r"\b(finali[sz]e|confirm|ready\s+to\s+trade|execute|send\s+to\s+agents?|"
    r"agent\s+debate|agent\s+analysis|run\s+debate|second\s+opinion)\b",
    re.I,
)


def detect_options_intent(text: str) -> bool:
    return bool(_OPTIONS_HINT.search(text or ""))


def detect_finalize_intent(text: str) -> bool:
    return bool(_FINALIZE_HINT.search(text or ""))


def extract_primary_ticker(text: str) -> str | None:
    """Return the first plausible India index or equity ticker in the message."""
    if not text:
        return None
    upper = text.upper()
    for index in sorted(_IN_INDICES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(index)}\b", upper):
            return index.replace("^", "") if index.startswith("^") else index

    candidates: list[str] = []
    for match in _TICKER_RE.finditer(upper):
        token = match.group(1).rstrip(".")
        if token.endswith(".NS") or token.endswith(".BO"):
            candidates.append(token.split(".")[0])
            continue
        if token in _STOPWORDS or token in _IN_INDICES:
            continue
        if len(token) < 2 or len(token) > 12 or token.isdigit():
            continue
        candidates.append(token)

    if not candidates:
        return None

    listed = _filter_india_listed(candidates)
    return listed[0] if listed else None


def _filter_india_listed(candidates: list[str]) -> list[str]:
    """Keep tickers that match the India symbol universe when trade stack is available."""
    try:
        from trade_integrations.dataflows.company_research.india_symbols import (
            is_india_listed_symbol,
        )

        return [c for c in candidates if is_india_listed_symbol(c)]
    except Exception:
        return candidates


def infer_asset_type(text: str, ticker: str | None) -> str:
    if detect_options_intent(text):
        return "options"
    if ticker and ticker.upper() in _IN_INDICES:
        return "options"
    return "stock"
