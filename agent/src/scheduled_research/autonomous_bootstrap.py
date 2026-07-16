"""Schedule post-commit autonomous agent bootstrap on the main event loop."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.api.async_bridge import schedule_coroutine

logger = logging.getLogger(__name__)


def _ensure_integrations_on_path() -> None:
    trade_root = Path(__file__).resolve().parents[3]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def schedule_agent_bootstrap(agent_id: str) -> bool:
    """Fire-and-forget bootstrap on the API main loop. Returns False if not scheduled."""
    _ensure_integrations_on_path()
    from trade_integrations.autonomous_agents.bootstrap import bootstrap_agent

    handle = schedule_coroutine(bootstrap_agent(agent_id), label=f"bootstrap-{agent_id[:12]}")
    if handle is None:
        logger.error("failed to schedule bootstrap for %s", agent_id)
        try:
            from trade_integrations.autonomous_agents.store import get_agent, save_agent

            agent = get_agent(agent_id)
            if agent:
                agent["bootstrap_status"] = "failed"
                agent["bootstrap_error"] = "main event loop unavailable"
                save_agent(agent)
        except Exception:
            logger.debug("could not mark bootstrap failed for %s", agent_id, exc_info=True)
        return False
    return True


def resume_pending_bootstraps() -> int:
    """Re-schedule bootstrap for running agents still pending/failed (e.g. after API restart)."""
    _ensure_integrations_on_path()
    from trade_integrations.autonomous_agents.store import list_agents

    count = 0
    for agent in list_agents():
        if str(agent.get("status")) != "running":
            continue
        bootstrap = str(agent.get("bootstrap_status") or "")
        if bootstrap != "pending":
            continue
        if schedule_agent_bootstrap(str(agent["id"])):
            count += 1
    return count
