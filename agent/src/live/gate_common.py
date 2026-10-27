"""Helpers both live order gates (`order_guard` for MCP brokers, `sdk_order_gate` for direct-SDK brokers) share."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.live.mandate.model import Mandate

# ponytail: substring heuristic over the error text; the ceiling is a transport failure worded in a way not
# listed here, which would be treated as a definite rejection. Upgrade: a typed `ambiguous` flag on the
# adapter / connector error envelope.
AMBIGUOUS_MARKERS = (
    "timeout", "timed out", "connection", "network", "reset by peer", "unreachable", "502", "503", "504",
)


def mandate_expired(mandate: Mandate) -> bool:
    """Whether the mandate is past its ``expires_at``. An unparseable expiry counts as expired (fail-closed): a
    live mandate with a malformed expiry must not keep trading."""
    try:
        expires = datetime.fromisoformat(mandate.consent.expires_at)
    except (TypeError, ValueError):
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires


def error_message(result: object, default: str = "broker order returned an error") -> str:
    """A human-readable error from a broker error envelope."""
    if isinstance(result, dict):
        for key in ("error", "message", "detail"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
    return default


def is_ambiguous_failure(result: Any) -> bool:
    """Whether an error result leaves it unknown if the broker placed the order (timeout, dropped connection,
    unparseable reply); a broker that answered with a refusal is a definite rejection."""
    if not isinstance(result, dict):
        return True
    text = error_message(result, "").lower()
    return any(marker in text for marker in AMBIGUOUS_MARKERS)
