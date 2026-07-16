"""Built-in OpenAlgo connector profiles.

OpenAlgo exposes two runtime modes via the Analyze toggle:
- **Paper** (analyzer/sandbox) — simulated fills, no live broker risk
- **Live** — orders route to the connected Indian broker

US market data is read-only (Alpaca paper feed when configured). US order
placement is not exposed on these profiles.
"""

from __future__ import annotations

from src.trading.types import READ_CAPABILITIES, TradingProfile

OPENALGO_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="openalgo-paper-sdk",
        connector="openalgo",
        label="OpenAlgo Paper · Analyzer (India + US data)",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "paper"},
        notes=(
            "Reads funds, positions, orders, and quotes via OpenAlgo while Analyze "
            "mode is ON. US symbols use Alpaca paper data when ALPACA_API_KEY is set."
        ),
    ),
    TradingProfile(
        id="openalgo-live-sdk-readonly",
        connector="openalgo",
        label="OpenAlgo Live · Read-Only (India)",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes=(
            "Reads the live broker book via OpenAlgo (Analyze mode must be OFF). "
            "US quotes still use Alpaca data only — no live US execution."
        ),
    ),
    TradingProfile(
        id="openalgo-paper-trade",
        connector="openalgo",
        label="OpenAlgo Paper · India Trade (Analyzer)",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place",),
        readonly=False,
        config={"profile": "paper"},
        notes=(
            "Places NSE/BSE equity paper orders through OpenAlgo analyzer/sandbox. "
            "Requires Analyze mode ON in OpenAlgo UI."
        ),
    ),
)
