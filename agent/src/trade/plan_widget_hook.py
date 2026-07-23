"""Notify autonomous plan approval when a trade_plan.widget is emitted."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_agent_id(session_id: str) -> str:
    if not session_id:
        return ""
    try:
        import sys

        host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
        svc = host._get_session_service() if host else None
        if not svc:
            return ""
        session = svc.get_session(session_id)
        if not session:
            return ""
        from src.trade.session_context import is_autonomous_agent_session

        cfg = dict(session.config or {})
        if not is_autonomous_agent_session(cfg):
            return ""
        return str(cfg.get("autonomous_agent_id") or "").strip()
    except Exception:
        logger.debug("resolve agent for session %s failed", session_id, exc_info=True)
        return ""


def notify_trade_plan_widget(session_id: str, widget: dict[str, Any]) -> None:
    widget_id = str(widget.get("widget_id") or "").strip()
    if not widget_id or not session_id:
        return
    agent_id = _resolve_agent_id(session_id)
    if not agent_id:
        return
    try:
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
    except Exception:
        logger.debug("plan widget hook failed session=%s widget=%s", session_id, widget_id, exc_info=True)


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
