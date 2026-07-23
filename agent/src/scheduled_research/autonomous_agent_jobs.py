"""Scheduled jobs for multi-instance autonomous agents."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule

logger = logging.getLogger(__name__)

JOB_TYPE_WATCH = "autonomous_agent_watch"
JOB_TYPE_RESEARCH = "autonomous_agent_research"
JOB_TYPE_QUANT = "autonomous_agent_quant"
JOB_TYPE_INFRA_HEAL = "autonomous_agent_infra_heal"
AUTONOMOUS_JOB_TYPES = frozenset({JOB_TYPE_WATCH, JOB_TYPE_RESEARCH, JOB_TYPE_QUANT, JOB_TYPE_INFRA_HEAL})

_INFRA_HEAL_MS = 60_000


def is_autonomous_scheduler_enabled() -> bool:
    raw = os.getenv("AUTONOMOUS_AGENTS_ENABLE_SCHEDULER", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _ensure_trade_integrations_on_path() -> None:
    from pathlib import Path

    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def _job_ids(agent_id: str) -> tuple[str, str, str]:
    return f"{agent_id}-watch", f"{agent_id}-research", f"{agent_id}-quant"


def _is_index_agent(agent: dict[str, Any]) -> bool:
    symbols = [str(s).upper() for s in (agent.get("symbols") or [])]
    return any(s in {"NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "^NSEI"} for s in symbols)


def _infra_heal_job_id(agent_id: str) -> str:
    return f"{agent_id}-infra-heal"


def register_infra_heal_job(agent_id: str) -> None:
    if not is_autonomous_scheduler_enabled():
        return
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore()
    now_ms = int(time.time() * 1000)
    job_id = _infra_heal_job_id(agent_id)
    store.upsert(
        ScheduledResearchJob(
            id=job_id,
            prompt=f"Infra heal for autonomous agent {agent_id}",
            schedule=str(_INFRA_HEAL_MS),
            next_run_at=now_ms + _INFRA_HEAL_MS,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_INFRA_HEAL, "autonomous_agent_id": agent_id},
        )
    )
    logger.info("registered infra heal job for %s", agent_id)


def unregister_infra_heal_job(agent_id: str) -> bool:
    from src.scheduled_research.store import ScheduledResearchJobStore

    return ScheduledResearchJobStore().delete(_infra_heal_job_id(agent_id))


def register_agent_jobs(agent: dict[str, Any]) -> None:
    if not is_autonomous_scheduler_enabled():
        return

    agent_id = str(agent.get("id") or "")
    if not agent_id:
        return

    if str(agent.get("status") or "") == "draft":
        return

    if str(agent.get("pause_reason") or "") == "infra":
        register_infra_heal_job(agent_id)
        return

    from src.scheduled_research.store import ScheduledResearchJobStore

    unregister_infra_heal_job(agent_id)

    schedules = dict(agent.get("schedules") or {})
    watch_ms = str(int(schedules.get("watch_ms") or 420_000))
    research_ms = str(int(schedules.get("research_ms") or 5_400_000))
    validate_schedule(watch_ms)
    validate_schedule(research_ms)

    store = ScheduledResearchJobStore()
    now_ms = int(time.time() * 1000)
    watch_id, research_id, quant_id = _job_ids(agent_id)
    # Post-commit bootstrap runs an immediate watch tick; defer the first scheduled one.
    bootstrap = str(agent.get("bootstrap_status") or "")
    watch_next_run = now_ms + int(watch_ms)
    research_next_run = (
        now_ms + int(research_ms)
        if bootstrap in {"pending", "running", "awaiting_plan_approval"}
        else now_ms + 60_000
    )

    store.upsert(
        ScheduledResearchJob(
            id=watch_id,
            prompt=f"Autonomous watch tick for {agent.get('name') or agent_id}",
            schedule=watch_ms,
            next_run_at=watch_next_run,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_WATCH, "autonomous_agent_id": agent_id},
        )
    )
    store.upsert(
        ScheduledResearchJob(
            id=research_id,
            prompt=f"Autonomous research turn for {agent.get('name') or agent_id}",
            schedule=research_ms,
            next_run_at=research_next_run,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_RESEARCH, "autonomous_agent_id": agent_id},
        )
    )
    if _is_index_agent(agent):
        quant_ms = str(int(schedules.get("quant_ms") or watch_ms))
        store.upsert(
            ScheduledResearchJob(
                id=quant_id,
                prompt=f"Quant monitor tick for {agent.get('name') or agent_id}",
                schedule=quant_ms,
                next_run_at=watch_next_run,
                status=JobStatus.PENDING,
                created_at=now_ms,
                config={"job_type": JOB_TYPE_QUANT, "autonomous_agent_id": agent_id},
            )
        )
    logger.info("registered autonomous jobs for %s", agent_id)


def unregister_agent_jobs(agent_id: str) -> dict[str, bool]:
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore()
    watch_id, research_id, quant_id = _job_ids(agent_id)
    return {
        watch_id: store.delete(watch_id),
        research_id: store.delete(research_id),
        quant_id: store.delete(quant_id),
        _infra_heal_job_id(agent_id): store.delete(_infra_heal_job_id(agent_id)),
    }


def finalize_infra_heal(agent_id: str) -> None:
    """After infra heal succeeds: swap heal job for watch/research/bootstrap."""
    _ensure_trade_integrations_on_path()
    from trade_integrations.autonomous_agents.store import get_agent

    agent = get_agent(agent_id)
    if not agent or str(agent.get("status") or "") != "running":
        return
    unregister_infra_heal_job(agent_id)
    register_agent_jobs(agent)
    from src.scheduled_research.autonomous_bootstrap import schedule_agent_bootstrap

    schedule_agent_bootstrap(agent_id)


async def dispatch_autonomous_job(job: ScheduledResearchJob) -> None:
    _ensure_trade_integrations_on_path()
    from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning, run_watch_tick

    agent_id = str((job.config or {}).get("autonomous_agent_id") or "")
    if not agent_id:
        logger.warning("autonomous job missing agent id: %s", job.id)
        return

    job_type = str((job.config or {}).get("job_type") or "")
    if job_type == JOB_TYPE_INFRA_HEAL:
        from trade_integrations.autonomous_agents.infra_startup import attempt_infra_heal

        updated = await asyncio.to_thread(attempt_infra_heal, agent_id)
        if updated and str(updated.get("status") or "") == "running":
            await asyncio.to_thread(finalize_infra_heal, agent_id)
        return
    if job_type == JOB_TYPE_WATCH:
        await run_watch_tick(agent_id)
        return
    if job_type == JOB_TYPE_QUANT:
        from trade_integrations.monitor.quant_monitor import run_quant_monitor_tick

        await asyncio.to_thread(run_quant_monitor_tick, agent_id)
        return
    if job_type == JOB_TYPE_RESEARCH:
        import os

        if os.getenv("AUTONOMOUS_RESEARCH_ON_SCHEDULE", "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            logger.info("skip scheduled research for %s (alert-only policy)", agent_id)
            return
        await dispatch_full_reasoning(agent_id, turn_kind="research")
        return

    logger.warning("unknown autonomous job type %s", job_type)


def dispatch_autonomous_job_sync(job: ScheduledResearchJob) -> None:
    asyncio.run(dispatch_autonomous_job(job))
