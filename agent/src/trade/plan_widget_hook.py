"""Notify autonomous plan approval when a trade_plan.widget is emitted."""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_agent_id(session_id: str) -> str:
    """Return the autonomous agent id owning ``session_id``, or ``""`` when there is none.

    A session that is not an autonomous-agent session is the normal case and returns ``""``
    quietly. Every other empty return means a plan widget could not be bound to an agent that
    may own it, so it is logged at WARNING with the reason. Internal errors propagate — this is
    in-process lookup, not a vendor call (2026-09-07-plan-widget-never-bound).
    """
    if not session_id:
        return ""
    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    if host is None:
        logger.warning(
            "plan widget binding skipped for session %s: api_server module not loaded", session_id
        )
        return ""
    svc = host._get_session_service()
    if not svc:
        logger.warning(
            "plan widget binding skipped for session %s: no session service", session_id
        )
        return ""
    session = svc.get_session(session_id)
    if not session:
        logger.warning(
            "plan widget binding skipped for session %s: session not found", session_id
        )
        return ""
    from src.trade.session_context import is_autonomous_agent_session

    cfg = dict(session.config or {})
    if not is_autonomous_agent_session(cfg):
        return ""
    agent_id = str(cfg.get("autonomous_agent_id") or "").strip()
    if not agent_id:
        logger.warning(
            "plan widget binding skipped for session %s: autonomous session has no autonomous_agent_id",
            session_id,
        )
    return agent_id


def notify_trade_plan_widget(session_id: str, widget: dict[str, Any]) -> None:
    widget_id = str(widget.get("widget_id") or "").strip()
    if not session_id:
        return
    if not widget_id:
        logger.warning("plan widget binding skipped for session %s: widget has no widget_id", session_id)
        return
    agent_id = _resolve_agent_id(session_id)
    if not agent_id:
        return
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from trade_integrations.autonomous_agents.plan_approval import on_trade_plan_widget_emitted

    meta = widget.get("meta") if isinstance(widget.get("meta"), dict) else {}
    revision_source = meta.get("revision_source")
    on_trade_plan_widget_emitted(
        agent_id,
        widget_id,
        revision_source=str(revision_source) if revision_source else None,
    )


def mark_user_chat_turn(agent_id: str) -> None:
    if not agent_id:
        return
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.store import get_agent, save_agent

        agent = get_agent(agent_id)
        if not agent:
            return
        agent["active_turn_kind"] = "user_chat"
        save_agent(agent)
    except Exception:
        logger.debug("mark user chat turn failed for %s", agent_id, exc_info=True)
