"""Safety net: retry autonomous scheduler turns missing record_autonomous_decision."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_DECISION_TOOL = "record_autonomous_decision"
_STATUS_TOOL = "get_autonomous_agent_status"

_AUTONOMOUS_TURN_RE = re.compile(
    r"#\s*Autonomous agent turn|##\s*Bootstrap checklist|Autonomous strategy revision",
    re.IGNORECASE,
)


def _guard_enabled() -> bool:
    raw = os.getenv("AUTONOMOUS_DECISION_GUARD_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def is_autonomous_scheduler_turn(user_message: str) -> bool:
    return bool(_AUTONOMOUS_TURN_RE.search(user_message or ""))


def infer_scheduler_turn_kind(user_message: str) -> str:
    """Best-effort turn kind from scheduler prompt text (bootstrap/research/revision)."""
    lower = (user_message or "").lower()
    if "bootstrap" in lower:
        return "bootstrap"
    if "revision" in lower:
        return "revision"
    if "research" in lower:
        return "research"
    if "decision retry" in lower:
        return "decision_retry"
    return "scheduler"


def needs_decision_guard(
    user_message: str,
    tools_called: set[str] | list[str],
    session_config: dict | None,
) -> bool:
    if not _guard_enabled():
        return False
    from src.trade.session_context import is_autonomous_agent_session

    cfg = session_config or {}
    if not is_autonomous_agent_session(cfg) or cfg.get("e2e_integration_test"):
        return False
    if not is_autonomous_scheduler_turn(user_message):
        return False
    called = {str(t).strip() for t in tools_called if t}
    return _DECISION_TOOL not in called


def build_decision_retry_message(*, agent_id: str, turn_kind: str = "scheduler") -> str:
    kind = str(turn_kind or "scheduler").strip() or "scheduler"
    return (
        f"# Autonomous agent turn (decision retry — {kind})\n\n"
        "## Autonomous turn incomplete\n"
        f"This scheduler turn for agent `{agent_id}` must end with structured output.\n"
        f"1. Call `{_STATUS_TOOL}(agent_id=\"{agent_id}\")` if not already done this turn.\n"
        f"2. Call `{_DECISION_TOOL}` with decision, rationale, confidence (0–100), direction, strategy.\n"
        "3. Reply using the mandatory Decision template only — then stop.\n"
    )


async def maybe_retry_autonomous_decision(
    session_service: Any,
    session_id: str,
    *,
    user_message: str,
    tools_called: set[str] | list[str],
    session_config: dict | None,
) -> bool:
    """Enqueue a follow-up user turn when a scheduler turn skipped record_autonomous_decision."""
    if not needs_decision_guard(user_message, tools_called, session_config):
        return False
    agent_id = str((session_config or {}).get("autonomous_agent_id") or "").strip()
    if not agent_id:
        return False
    try:
        await session_service.send_message(
            session_id,
            build_decision_retry_message(
                agent_id=agent_id,
                turn_kind=infer_scheduler_turn_kind(user_message),
            ),
        )
        logger.info("Autonomous decision guard enqueued retry for agent %s session=%s", agent_id, session_id)
        return True
    except Exception:
        logger.exception("Autonomous decision guard failed for session=%s", session_id)
        return False
