"""Safety net: ensure orchestrator turns produce proposal cards."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_PROPOSE_TOOL = "propose_autonomous_agent"
_CARD_STATUSES = frozenset({"ready", "incomplete"})


def _guard_enabled() -> bool:
    raw = os.getenv("ORCHESTRATOR_PROPOSE_GUARD_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _propose_tool_called(tools_called: set[str] | list[str]) -> bool:
    called = {str(t).strip() for t in tools_called if t}
    return any(_PROPOSE_TOOL in name or name == _PROPOSE_TOOL for name in called)


def emit_autonomous_proposal(event_bus: Any, session_id: str, result: dict[str, Any] | None) -> bool:
    """Emit autonomous_agent.proposal SSE when auto-propose saved a card."""
    proposal = (result or {}).get("proposal")
    if not isinstance(proposal, dict):
        return False
    if str(proposal.get("status") or "") not in _CARD_STATUSES:
        return False
    if not proposal.get("proposal_id"):
        return False
    event_bus.emit(session_id, "autonomous_agent.proposal", proposal)
    return True


def needs_propose_guard(
    user_message: str,
    assistant_text: str,
    tools_called: set[str] | list[str],
    session_config: dict | None,
    *,
    orchestrator_session_id: str = "",
) -> bool:
    if not _guard_enabled():
        return False
    from src.session.orchestrator_profile import is_orchestrator_session

    if not is_orchestrator_session(session_config):
        return False
    if _propose_tool_called(tools_called):
        return False

    try:
        import sys
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in sys.path:
            sys.path.insert(0, str(integrations))
        from trade_integrations.autonomous_agents.orchestrator_intent import (
            assistant_claims_proposal_ready,
            build_auto_propose_kwargs,
            orchestrator_has_propose_intent,
        )
    except Exception:
        logger.debug("orchestrator intent import failed for propose guard", exc_info=True)
        return False

    if assistant_claims_proposal_ready(assistant_text):
        return True
    if orchestrator_has_propose_intent(user_message, assistant_text):
        return True
    session_id = str(orchestrator_session_id or (session_config or {}).get("session_id") or "").strip()
    if build_auto_propose_kwargs(
        user_message=user_message,
        assistant_text=assistant_text,
        orchestrator_session_id=session_id,
    ) is not None:
        return True
    return False


def build_propose_retry_message(*, user_message: str) -> str:
    snippet = (user_message or "").strip()[:240]
    context = f"\n\nUser request: {snippet}" if snippet else ""
    return (
        "## Orchestrator turn incomplete\n"
        "You described a proposal in chat but did **not** call `propose_autonomous_agent`. "
        "Prose alone does not create the confirmation card in the UI.\n"
        f"Call `propose_autonomous_agent` now with symbols, mandate, and schedules from this turn.{context}\n"
        "Then tell the user to **Confirm the proposal card** — do not commit yourself."
    )


async def maybe_enforce_orchestrator_propose(
    session_service: Any,
    session_id: str,
    *,
    user_message: str,
    assistant_text: str,
    tools_called: set[str] | list[str],
    session_config: dict | None,
) -> bool:
    """Auto-propose or enqueue a retry when an orchestrator turn skipped the propose tool."""
    if not needs_propose_guard(
        user_message,
        assistant_text,
        tools_called,
        session_config,
        orchestrator_session_id=session_id,
    ):
        return False

    try:
        import sys
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in sys.path:
            sys.path.insert(0, str(integrations))
        from trade_integrations.autonomous_agents.orchestrator_intent import (
            maybe_auto_propose_after_orchestrator_turn,
        )
    except Exception:
        logger.exception("orchestrator auto-propose import failed")
        return False

    result = maybe_auto_propose_after_orchestrator_turn(
        orchestrator_session_id=session_id,
        user_message=user_message,
        assistant_text=assistant_text,
        tools_called=tools_called,
    )
    if emit_autonomous_proposal(session_service.event_bus, session_id, result):
        logger.info("Orchestrator propose guard auto-proposed for session=%s", session_id)
        return True

    try:
        await session_service.send_message(
            session_id,
            build_propose_retry_message(user_message=user_message),
        )
        logger.info("Orchestrator propose guard enqueued retry for session=%s", session_id)
        return True
    except Exception:
        logger.exception("Orchestrator propose guard failed for session=%s", session_id)
        return False
