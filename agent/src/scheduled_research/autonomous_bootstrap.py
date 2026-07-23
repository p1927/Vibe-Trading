"""Schedule post-commit autonomous agent bootstrap on the main event loop."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.api.async_bridge import schedule_coroutine

logger = logging.getLogger(__name__)

_PENDING_BOOTSTRAP_MAX_AGE_S = 60.0
_STALE_RUNNING_BOOTSTRAP_MAX_AGE_S = 600.0


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
    """Re-schedule bootstrap for running agents still pending (e.g. after API restart)."""
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


def resume_stale_pending_bootstraps(*, max_age_s: float = _PENDING_BOOTSTRAP_MAX_AGE_S) -> int:
    """Re-schedule bootstrap when commit succeeded but bootstrap never started."""
    _ensure_integrations_on_path()
    from trade_integrations.autonomous_agents.store import get_agent, list_agents, save_agent

    now = datetime.now(timezone.utc)
    count = 0
    for agent in list_agents():
        if str(agent.get("status")) != "running":
            continue
        if str(agent.get("bootstrap_status") or "") != "pending":
            continue
        if str(agent.get("pause_reason") or "") == "infra":
            continue
        created_raw = str(agent.get("created_at") or "")
        if not created_raw:
            continue
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            age_s = (now - created).total_seconds()
        except ValueError:
            continue
        if age_s < max_age_s:
            continue
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            continue
        logger.warning(
            "re-scheduling stale pending bootstrap for %s (pending %.0fs)",
            agent_id,
            age_s,
        )
        if schedule_agent_bootstrap(agent_id):
            count += 1
        else:
            latest = get_agent(agent_id)
            if latest and str(latest.get("bootstrap_status") or "") == "pending":
                latest["bootstrap_error"] = "bootstrap schedule retry failed"
                save_agent(latest)
    return count


def resume_stale_running_bootstraps(*, max_age_s: float = _STALE_RUNNING_BOOTSTRAP_MAX_AGE_S) -> int:
    """Re-schedule bootstrap stuck at running with no decision (hung prefetch or API restart)."""
    _ensure_integrations_on_path()
    from trade_integrations.autonomous_agents.store import get_agent, list_agents, save_agent

    now = datetime.now(timezone.utc)
    count = 0
    for agent in list_agents():
        if str(agent.get("status")) != "running":
            continue
        if str(agent.get("bootstrap_status") or "") != "running":
            continue
        if agent.get("last_decision"):
            continue
        if agent.get("streaming"):
            continue
        if str(agent.get("pause_reason") or "") == "infra":
            continue
        age_anchor = str(agent.get("updated_at") or agent.get("created_at") or "")
        if not age_anchor:
            continue
        try:
            anchor = datetime.fromisoformat(age_anchor.replace("Z", "+00:00"))
            age_s = (now - anchor).total_seconds()
        except ValueError:
            continue
        if age_s < max_age_s:
            continue
        agent_id = str(agent.get("id") or "")
        if not agent_id:
            continue
        logger.warning(
            "re-scheduling stale running bootstrap for %s (running %.0fs, no decision)",
            agent_id,
            age_s,
        )
        agent["bootstrap_status"] = "pending"
        agent["bootstrap_error"] = f"bootstrap timed out after {int(age_s)}s; retrying"
        save_agent(agent)
        if schedule_agent_bootstrap(agent_id):
            count += 1
        else:
            latest = get_agent(agent_id)
            if latest and str(latest.get("bootstrap_status") or "") == "pending":
                latest["bootstrap_status"] = "failed"
                latest["bootstrap_error"] = "bootstrap retry schedule failed"
                save_agent(latest)
    return count
