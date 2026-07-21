"""Safety net: retry bootstrap turns that recorded a decision but lack options structure."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DECISION_TOOL = "record_autonomous_decision"
_WIDGET_TOOLS = frozenset(
    {
        "get_options_trade_widget",
        "mcp_openalgo_get_options_trade_widget",
        "get_stock_trade_widget",
        "mcp_openalgo_get_stock_trade_widget",
        "get_index_trade_widget",
        "mcp_openalgo_get_index_trade_widget",
        "get_options_trade_plan",
        "mcp_openalgo_get_options_trade_plan",
    }
)


def _guard_enabled() -> bool:
    raw = os.getenv("BOOTSTRAP_FINALIZE_GUARD_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _is_bootstrap_turn(user_message: str) -> bool:
    from src.trade.autonomous_decision_guard import is_autonomous_scheduler_turn

    if not is_autonomous_scheduler_turn(user_message):
        return False
    return "bootstrap" in (user_message or "").lower()


def needs_bootstrap_finalize_guard(
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
    if not _is_bootstrap_turn(user_message):
        return False

    agent_id = str(cfg.get("autonomous_agent_id") or "").strip()
    if not agent_id:
        return False

    called = {str(t).strip() for t in tools_called if t}
    if _DECISION_TOOL not in called:
        return False
    if called & _WIDGET_TOOLS:
        return False

    try:
        import sys
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in sys.path:
            sys.path.insert(0, str(integrations))
        from trade_integrations.autonomous_agents.bootstrap import _bootstrap_structured_plan_ready
        from trade_integrations.autonomous_agents.store import get_agent
        from trade_integrations.execution.profile import resolve_profile
    except Exception:
        logger.debug("bootstrap finalize guard import failed", exc_info=True)
        return False

    agent = get_agent(agent_id)
    if not agent or str(agent.get("bootstrap_status") or "") != "running":
        return False
    profile = resolve_profile(agent=agent)
    if "options" not in profile.allowed_instruments:
        return False
    if _bootstrap_structured_plan_ready(agent):
        return False
    return True


def build_bootstrap_widget_retry_message(*, agent_id: str, focus: str) -> str:
    return (
        "## Bootstrap turn incomplete\n"
        f"Agent `{agent_id}` recorded a decision but the options plan is not structured yet.\n"
        f"1. Call `get_options_trade_plan(ticker=\"{focus}\")` or `get_options_trade_widget` once.\n"
        "2. Call `set_agent_watch_spec` with the chosen strategy.\n"
        f"3. Update `record_autonomous_decision` if needed — then stop.\n"
    )


async def maybe_retry_bootstrap_widget(
    session_service: Any,
    session_id: str,
    *,
    user_message: str,
    tools_called: set[str] | list[str],
    session_config: dict | None,
) -> bool:
    if not needs_bootstrap_finalize_guard(user_message, tools_called, session_config):
        return False

    agent_id = str((session_config or {}).get("autonomous_agent_id") or "").strip()
    try:
        import sys
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in sys.path:
            sys.path.insert(0, str(integrations))
        from trade_integrations.autonomous_agents.store import get_agent
    except Exception:
        return False

    agent = get_agent(agent_id) or {}
    symbols = list(agent.get("symbols") or ["NIFTY"])
    focus = str(symbols[0] if symbols else "NIFTY")

    try:
        await session_service.send_message(
            session_id,
            build_bootstrap_widget_retry_message(agent_id=agent_id, focus=focus),
        )
        logger.info(
            "Bootstrap finalize guard enqueued widget retry for agent %s session=%s",
            agent_id,
            session_id,
        )
        return True
    except Exception:
        logger.exception("Bootstrap finalize guard failed for session=%s", session_id)
        return False
