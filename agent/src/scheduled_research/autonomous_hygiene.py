"""Periodic autonomous-agent hygiene, independent of the UI and of the scheduler's resume gate.

Fork-only sidecar (docs/FORK_CONVENTIONS.md). These passes used to run inside
``GET /autonomous-agents``, so recovery happened only while someone had the UI open and every
15 s poll paid for it ([[2026-09-23-agents-list-get-side-effects]]). The GET is now read-only
and this loop runs them instead, started at boot like the recording-wake poller.

Per docs/add/autonomous_agents.md § Lifecycle, pure hygiene (clearing stale flags, position
reconciliation, bootstrap finalization) must run even while the scheduler dispatch loop is
paused (D98) and for restart-paused agents, so this loop is not gated on the executor. Each pass
keeps its own gate for scheduling new work (the bootstrap resumes and infra-heal finalize act
only on ``status == "running"`` agents).
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_INTERVAL_S = 60.0
_task: "asyncio.Task | None" = None


def run_autonomous_hygiene_pass() -> None:
    """One synchronous pass. Each step is isolated so one failing step does not skip the rest;
    failures are logged at WARNING with the traceback (they were DEBUG in the GET)."""
    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from src.scheduled_research.autonomous_bootstrap import (
        resume_stale_pending_bootstraps,
        resume_stale_running_bootstraps,
    )
    from trade_integrations.autonomous_agents.recovery import run_autonomous_agent_recovery
    from trade_integrations.autonomous_agents.store import list_agents

    results: dict[str, object] = {}
    for name, step in (
        ("stale pending bootstrap resume", resume_stale_pending_bootstraps),
        ("stale running bootstrap resume", resume_stale_running_bootstraps),
        # The only periodic pass that clears a stale agent.streaming=True and reconciles
        # positions; a silent failure leaves an agent skipping every alert as turn_in_flight.
        ("autonomous agent recovery", run_autonomous_agent_recovery),
        ("infra heal", _heal_infra_paused_agents),
    ):
        try:
            results[name] = step()
        except Exception:
            logger.warning("autonomous hygiene: %s failed", name, exc_info=True)
            results[name] = "failed"
    # One line per pass (~1/min) so a live check can see the loop is alive and what it did.
    logger.info("autonomous hygiene pass: agents=%d %s", len(list_agents()), results)


def _heal_infra_paused_agents() -> int:
    from src.scheduled_research.autonomous_agent_jobs import finalize_infra_heal
    from trade_integrations.autonomous_agents.infra_startup import maybe_heal_infra_paused_agents
    from trade_integrations.autonomous_agents.store import get_agent, list_agents

    paused = [
        str(a.get("id") or "")
        for a in list_agents()
        if str(a.get("pause_reason") or "") == "infra" and str(a.get("status") or "") == "paused"
    ]
    healed = maybe_heal_infra_paused_agents()
    for agent_id in paused:
        updated = get_agent(agent_id) if agent_id else None
        if updated and str(updated.get("status") or "") == "running":
            finalize_infra_heal(agent_id)
    return healed


async def _loop() -> None:
    while True:
        # The passes block (file locks, OpenAlgo calls): keep them off the event loop.
        try:
            await asyncio.to_thread(run_autonomous_hygiene_pass)
        except Exception:
            logger.exception("autonomous hygiene pass failed")  # keep the loop alive
        await asyncio.sleep(_INTERVAL_S)


def start_autonomous_hygiene_loop() -> None:
    """Start the loop if it is not already running (safe on every boot and --reload respawn)."""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.get_running_loop().create_task(_loop())


async def stop_autonomous_hygiene_loop() -> None:
    global _task
    task, _task = _task, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
