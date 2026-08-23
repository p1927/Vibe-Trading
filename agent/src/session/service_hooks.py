"""Fork-only session-lifecycle hooks, split out of `service.py` per the sidecar
pattern in the parent repo's `docs/FORK_CONVENTIONS.md`.

Every function here is a thin, self-contained dispatch: a session-state
check (usually "is this an orchestrator session?" or "does this session
have an autonomous-agent id?"), a try/except around a call into one of the
`src/trade/*` hook modules, and a warning log on failure. None of them own
state of their own — the `SessionService`/`EventBus` they act on is always
passed in explicitly — so moving them out of the `SessionService` class into
plain functions changes nothing about behavior, only where the code lives.

`SessionService` itself stays the caller: each hook is invoked as
`service_hooks.<name>(self, ...)` or `service_hooks.<name>(self.event_bus, ...)`
from the corresponding point in `service.py`'s attempt lifecycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.session.models import Session

if TYPE_CHECKING:
    from src.session.events import EventBus
    from src.session.service import SessionService

logger = logging.getLogger(__name__)


def maybe_refresh_agent_intent(
    service: "SessionService",
    session: Session,
    content: str,
    message_id: str,
) -> None:
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.intent_capabilities import summarize_intent_change
        from trade_integrations.autonomous_agents.intent_hooks import maybe_refresh_intent_on_user_message
        from trade_integrations.autonomous_agents.intent_store import load_intent_from_session_config

        prior = load_intent_from_session_config(dict(session.config or {}))
        updated = maybe_refresh_intent_on_user_message(
            dict(session.config or {}),
            content,
            source_message_id=message_id,
        )
        if updated:
            session.config = updated
            current = load_intent_from_session_config(updated)
            if current is not None:
                change = summarize_intent_change(prior, current)
                if change:
                    service.event_bus.emit(
                        session.session_id,
                        "autonomous_agent.intent_updated",
                        change,
                    )
    except Exception:
        logger.warning(
            "refresh agent intent failed for %s",
            session.session_id,
            exc_info=True,
        )


def maybe_mark_autonomous_user_turn(session: Session, content: str) -> None:
    try:
        from src.trade.autonomous_decision_guard import is_autonomous_scheduler_turn
        from src.trade.session_context import is_autonomous_agent_session

        cfg = dict(session.config or {})
        if not is_autonomous_agent_session(cfg) or is_autonomous_scheduler_turn(content):
            return
        agent_id = str(cfg.get("autonomous_agent_id") or "").strip()
        if not agent_id:
            return
        from src.trade.plan_widget_hook import mark_user_chat_turn

        mark_user_chat_turn(agent_id)
    except Exception:
        logger.debug(
            "mark autonomous user turn failed for %s",
            session.session_id,
            exc_info=True,
        )


def prefetch_research_for_message(
    event_bus: "EventBus",
    session_id: str,
    content: str,
    session_config: Optional[Dict[str, Any]] = None,
) -> str:
    from src.session.orchestrator_profile import is_orchestrator_session

    if is_orchestrator_session(session_config):
        return ""
    from src.trade.autonomous_decision_guard import is_autonomous_scheduler_turn
    from src.trade.session_context import is_autonomous_agent_session

    cfg = dict(session_config or {})
    blocks: list[str] = []
    try:
        from src.trade.hub_bridge import prefetch_autonomous_context, prefetch_research_for_message

        agent_ctx = prefetch_autonomous_context(session_id, content, session_config)
        if agent_ctx.strip():
            blocks.append(agent_ctx.strip())

        if is_autonomous_agent_session(cfg) and is_autonomous_scheduler_turn(content):
            return "\n\n".join(blocks)

        research_ctx = prefetch_research_for_message(
            session_id,
            content,
            event_bus,
            session_config,
        )
        if research_ctx.strip():
            blocks.append(research_ctx.strip())
    except Exception:
        logger.exception("Research prefetch hook failed")
        return "\n\n".join(blocks)
    return "\n\n".join(blocks)


def emit_provenance_if_needed(
    event_bus: "EventBus",
    session_id: str,
    attempt_id: str,
    event_type: str,
    data: dict,
) -> None:
    """Record non-tool provenance and emit a dedicated SSE frame."""
    if event_type in {"tool_result", "provenance.source"}:
        return
    try:
        from src.provenance.hook import record_from_event

        source = record_from_event(
            session_id,
            event_type,
            data,
            attempt_id=attempt_id,
        )
        if source:
            event_bus.emit(
                session_id,
                "provenance.source",
                {"source": source.to_dict(), "attempt_id": attempt_id},
            )
    except Exception:
        logger.exception("Provenance recording failed")


def clear_agent_streaming(agent_id: str) -> None:
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.bootstrap import safe_finalize_bootstrap_if_ready
        from trade_integrations.autonomous_agents.store import get_agent, save_agent

        agent = get_agent(agent_id)
        if not agent:
            return
        if agent.get("streaming"):
            agent["streaming"] = False
            save_agent(agent)
        safe_finalize_bootstrap_if_ready(agent_id)
    except Exception:
        logger.debug("clear agent streaming failed for %s", agent_id, exc_info=True)


async def maybe_orchestrator_propose_guard(
    service: "SessionService",
    session_id: str,
    user_message: str,
    assistant_text: str,
    tools_called: set[str] | list[str],
    session_config: Optional[Dict[str, Any]] = None,
) -> None:
    from src.session.orchestrator_profile import is_orchestrator_session

    if not is_orchestrator_session(session_config):
        return
    try:
        from src.trade.orchestrator_propose_guard import maybe_enforce_orchestrator_propose

        await maybe_enforce_orchestrator_propose(
            service,
            session_id,
            user_message=user_message,
            assistant_text=assistant_text,
            tools_called=tools_called,
            session_config=session_config,
        )
    except Exception:
        logger.exception("Orchestrator propose guard failed")


def maybe_widget_guard(
    event_bus: "EventBus",
    session_id: str,
    user_message: str,
    assistant_text: str,
    tools_called: set[str] | list[str],
    session_config: Optional[Dict[str, Any]] = None,
) -> None:
    from src.session.orchestrator_profile import is_orchestrator_session

    if is_orchestrator_session(session_config):
        return
    try:
        from src.trade.widget_guard import maybe_inject_widget

        maybe_inject_widget(
            session_id,
            event_bus,
            user_message=user_message,
            assistant_text=assistant_text,
            tools_called=tools_called,
            session_config=session_config,
        )
    except Exception:
        logger.exception("Widget guard hook failed")


async def maybe_autonomous_decision_guard(
    service: "SessionService",
    session: Session,
    user_message: str,
    tools_called: set[str] | list[str],
) -> None:
    from src.session.orchestrator_profile import is_orchestrator_session

    if is_orchestrator_session(session.config):
        return
    try:
        from src.trade.autonomous_decision_guard import maybe_retry_autonomous_decision

        await maybe_retry_autonomous_decision(
            service,
            session.session_id,
            user_message=user_message,
            tools_called=tools_called,
            session_config=dict(session.config),
        )
    except Exception:
        logger.exception("Autonomous decision guard hook failed")


async def maybe_bootstrap_finalize_guard(
    service: "SessionService",
    session: Session,
    user_message: str,
    tools_called: set[str] | list[str],
) -> None:
    from src.session.orchestrator_profile import is_orchestrator_session

    if is_orchestrator_session(session.config):
        return
    try:
        from src.trade.bootstrap_finalize_guard import maybe_retry_bootstrap_widget

        await maybe_retry_bootstrap_widget(
            service,
            session.session_id,
            user_message=user_message,
            tools_called=tools_called,
            session_config=dict(session.config),
        )
    except Exception:
        logger.exception("Bootstrap finalize guard hook failed")
