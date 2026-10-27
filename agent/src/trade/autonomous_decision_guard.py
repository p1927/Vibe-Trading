"""Safety net: retry autonomous scheduler turns missing record_autonomous_decision."""

from __future__ import annotations

import logging
from typing import Any

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)

_DECISION_TOOL = "record_autonomous_decision"
_STATUS_TOOL = "get_autonomous_agent_status"

def _guard_enabled() -> bool:
    raw = get_env_config().trade.autonomous_decision_guard_enabled.strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _tool_matches(called: set[str], bare_name: str) -> bool:
    """Match bare MCP tool names against local wrappers (e.g. mcp_openalgo_record_autonomous_decision)."""
    if bare_name in called:
        return True
    suffix = f"_{bare_name}"
    return any(t.endswith(suffix) for t in called)


def needs_decision_guard(
    turn_kind: str | None,
    tools_called: set[str] | list[str],
    session_config: dict | None,
) -> bool:
    if not _guard_enabled():
        return False
    from src.trade.session_context import is_autonomous_agent_session

    cfg = session_config or {}
    if not is_autonomous_agent_session(cfg) or cfg.get("e2e_integration_test"):
        return False
    # D220: a scheduler turn is the attempt's structured turn_kind, never parsed from the prompt text.
    if not turn_kind:
        return False
    called = {str(t).strip() for t in tools_called if t}
    return not _tool_matches(called, _DECISION_TOOL)


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
    tools_called: set[str] | list[str],
    session_config: dict | None,
    turn_kind: str | None = None,
) -> bool:
    """Enqueue a follow-up user turn when a scheduler turn skipped record_autonomous_decision."""
    if not needs_decision_guard(turn_kind, tools_called, session_config):
        return False
    agent_id = str((session_config or {}).get("autonomous_agent_id") or "").strip()
    if not agent_id:
        return False
    try:
        await session_service.send_message(
            session_id,
            build_decision_retry_message(
                agent_id=agent_id,
                turn_kind=turn_kind or "scheduler",
            ),
            turn_kind=turn_kind,  # the retry keeps the original turn's tool list (D220)
        )
        logger.info("Autonomous decision guard enqueued retry for agent %s session=%s", agent_id, session_id)
        return True
    except Exception:
        logger.exception("Autonomous decision guard failed for session=%s", session_id)
        return False
