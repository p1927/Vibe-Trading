"""Scheduled-research stack boot/shutdown orchestration.

Fork-only sidecar for ``agent/src/api/scheduled_routes.py`` (an upstream
file) per docs/FORK_CONVENTIONS.md — this is pure fork orchestration (job
registration defaults, autonomous-agent recovery, Nautilus watch) with no
upstream logic left once the store/executor are passed in explicitly rather
than reached via module-private globals. ``scheduled_routes.py`` keeps
ownership of its own ``_scheduled_research_executor`` global; these functions
take the store and executor as plain parameters instead of reading module
state, so they carry no coupling back to that file beyond the call site.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.accessor import get_env_config

logger = logging.getLogger(__name__)


def register_persisted_autonomous_agent_jobs() -> None:
    """Re-register scheduler jobs for running autonomous agents after API restart."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.store import list_agents
        from src.scheduled_research.autonomous_agent_jobs import register_agent_jobs

        for agent in list_agents():
            if str(agent.get("status")) == "running":
                register_agent_jobs(agent)
    except Exception:
        logger.exception("failed to register persisted autonomous agent jobs")


def boot_scheduled_research_stack(get_store) -> None:
    """Start scheduled research execution when explicitly enabled.

    Args:
        get_store: Callable returning the shared scheduled-research store
            singleton (``scheduled_routes._get_scheduled_research_store``).
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.proposals import pause_running_agents_on_boot

        # A process start/restart (crash, deploy, or dev uvicorn --reload
        # respawn) is indistinguishable on disk from an agent that was
        # legitimately left running. Force every "running" agent to
        # "paused" before any resume/bootstrap sweep below can see it, so
        # nothing auto-fires LLM work until a user explicitly resumes it.
        pause_running_agents_on_boot()
    except Exception:
        logger.exception("failed to boot-pause running autonomous agents")
    try:
        from src.scheduled_research.lifecycle import recover_scheduler_jobs_on_stack_boot

        recover_scheduler_jobs_on_stack_boot(get_store())
    except Exception:
        logger.exception("failed to recover stale scheduler jobs on API startup")
    from src.scheduled_research.index_jobs import (
        is_index_scheduler_enabled,
        register_default_index_jobs,
    )
    from src.scheduled_research.options_jobs import (
        is_options_scheduler_enabled,
        register_default_options_jobs,
    )
    from src.scheduled_research.trade_data_jobs import (
        is_trade_data_scheduler_enabled,
        register_default_trade_data_jobs,
    )
    from src.scheduled_research.hub_calibration_jobs import (
        is_hub_calibration_scheduler_enabled,
        register_default_hub_calibration_jobs,
    )
    from src.scheduled_research.capture_jobs import (
        is_hub_capture_scheduler_enabled,
        register_default_hub_capture_jobs,
    )
    from src.scheduled_research.financial_knowledge_jobs import (
        is_financial_knowledge_scheduler_enabled,
        register_default_financial_knowledge_jobs,
    )
    from src.scheduled_research.dst_eval_jobs import (
        is_dst_eval_scheduler_enabled,
        register_default_dst_eval_jobs,
    )

    if is_index_scheduler_enabled():
        register_default_index_jobs(get_store())
    if is_options_scheduler_enabled():
        register_default_options_jobs(get_store())
    if is_hub_calibration_scheduler_enabled():
        register_default_hub_calibration_jobs(get_store())
    elif is_trade_data_scheduler_enabled():
        register_default_trade_data_jobs(get_store())
    if is_hub_capture_scheduler_enabled():
        register_default_hub_capture_jobs(get_store())
    if is_financial_knowledge_scheduler_enabled():
        register_default_financial_knowledge_jobs(get_store())
    if is_dst_eval_scheduler_enabled():
        register_default_dst_eval_jobs(get_store())
    register_persisted_autonomous_agent_jobs()
    # Recording-wake poller: unlike the general executor below, this starts
    # unconditionally (not gated by the scheduler enable/resume flag) — see
    # ``src.trade.recording_wait_scheduler`` module docstring for why a
    # ``wait_for_open`` recording must not depend on that LLM-cost gate.
    try:
        from src.trade.recording_wait_scheduler import start_recording_wake_poller

        start_recording_wake_poller(get_store())
    except Exception:
        logger.exception("failed to start recording-wake poller on startup")
    # Hot-reload safety: every ``register_default_*`` helper stamps
    # ``next_run_at=now_ms`` so a fresh job fires immediately. On uvicorn
    # --reload this means every code save re-stamps every default job and the
    # first executor tick dispatches them all, cascading LLM calls and IO
    # before the user types anything. Push never-executed PENDING jobs forward
    # by ``SCHEDULED_RESEARCH_FRESH_DEFER_MS`` (default 30 min) so the
    # scheduler only fires on the persisted cron schedule, not on every reload.
    try:
        from src.scheduled_research.executor import defer_fresh_registrations

        deferred = defer_fresh_registrations(get_store())
        if deferred:
            logger.info(
                "deferred %d fresh scheduled job(s) on startup to avoid hot-reload cascade",
                deferred,
            )
    except Exception:
        logger.exception("defer_fresh_registrations failed on startup")
    # Note: no bootstrap-resume sweep runs here anymore. Every agent that
    # was "running" was just boot-paused above, so resuming bootstrap work
    # is now exclusively a user action via POST /autonomous-agents/{id}/resume
    # (see autonomous_routes.resume_agent, which re-fires a stuck bootstrap
    # when appropriate).
    try:
        from trade_integrations.autonomous_agents.recovery import run_autonomous_agent_recovery

        recovery = run_autonomous_agent_recovery()
        if any(recovery.values()):
            logger.info("autonomous agent recovery: %s", recovery)
    except Exception:
        logger.debug("autonomous agent recovery on startup failed", exc_info=True)
    try:
        if get_env_config().trade.stack_dev.strip().lower() in {"1", "true", "yes", "on"}:
            logger.debug("skipping Nautilus watch ensure in dev mode (use: trade reload nautilus)")
        else:
            from src.trade.hub_bridge import ensure_trade_stack_path

            ensure_trade_stack_path()
            from trade_integrations.autonomous_agents.nautilus_watch import (
                ensure_nautilus_watch_for_running_agents,
                get_watch_process_status,
            )

            watch_status = get_watch_process_status(reconcile=True)
            if watch_status.get("alive") and watch_status.get("registry_agent_ids"):
                logger.debug("Nautilus watch already alive with registry — skipping startup ensure")
            elif ensure_nautilus_watch_for_running_agents():
                logger.info("ensured Nautilus watch for running India bridge agent(s)")
    except Exception:
        logger.exception("failed to ensure Nautilus watch on startup")
    # Global scheduled-research jobs (index/options/hub-calibration/trade-data/
    # hub-capture) default to paused on every boot, the same "always ephemeral,
    # resume via UI" model used for autonomous agents. Jobs were registered
    # above so they're visible in the Scheduled UI with their real cadence,
    # but the executor's dispatch loop is intentionally NOT started here —
    # only POST /scheduled-runs/scheduler/resume starts it, and every fresh
    # process start (clean shutdown, crash, or dev --reload) requires that
    # click again rather than remembering the prior running state.


async def shutdown_scheduled_research_stack(get_store, executor: Any) -> None:
    """Stop scheduled research execution if it was started.

    Args:
        get_store: Callable returning the shared scheduled-research store
            singleton.
        executor: The current executor instance (or ``None``) — the caller
            keeps ownership of clearing its own module-level reference after
            this returns.
    """
    logger.info("scheduled research shutdown: recovering jobs and stopping executor")
    try:
        from src.scheduled_research.lifecycle import recover_scheduler_jobs_on_stack_shutdown

        recover_scheduler_jobs_on_stack_shutdown(get_store())
    except Exception:
        logger.exception("failed to recover scheduler jobs on API shutdown")
    if executor is not None:
        # Process-restart interruption, not a deliberate user pause: mark any
        # job this shutdown catches mid-RUNNING as auto-paused with a clear
        # reason so `GET /scheduled-runs` can distinguish it from a real hang
        # without cross-referencing log/vibe-api.log (see
        # 2026-09-02-vibetrading-dev-reload-thrashing.md).
        await executor.stop(
            auto_pause_reason="auto-paused: process restart (executor shutdown)"
        )
    try:
        from src.trade.recording_wait_scheduler import stop_recording_wake_poller

        await stop_recording_wake_poller()
    except Exception:
        logger.exception("failed to stop recording-wake poller on shutdown")
