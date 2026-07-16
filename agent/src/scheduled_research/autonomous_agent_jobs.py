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
AUTONOMOUS_JOB_TYPES = frozenset({JOB_TYPE_WATCH, JOB_TYPE_RESEARCH})


def is_autonomous_scheduler_enabled() -> bool:
    raw = os.getenv("AUTONOMOUS_AGENTS_ENABLE_SCHEDULER", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _ensure_trade_integrations_on_path() -> None:
    from pathlib import Path

    trade_root = Path(__file__).resolve().parents[4]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))


def _job_ids(agent_id: str) -> tuple[str, str]:
    return f"{agent_id}-watch", f"{agent_id}-research"


def register_agent_jobs(agent: dict[str, Any]) -> None:
    if not is_autonomous_scheduler_enabled():
        return

    from src.scheduled_research.store import ScheduledResearchJobStore

    agent_id = str(agent.get("id") or "")
    if not agent_id:
        return

    schedules = dict(agent.get("schedules") or {})
    watch_ms = str(int(schedules.get("watch_ms") or 420_000))
    research_ms = str(int(schedules.get("research_ms") or 5_400_000))
    validate_schedule(watch_ms)
    validate_schedule(research_ms)

    store = ScheduledResearchJobStore()
    now_ms = int(time.time() * 1000)
    watch_id, research_id = _job_ids(agent_id)
    # Post-commit bootstrap runs an immediate watch tick; defer the first scheduled one.
    bootstrap = str(agent.get("bootstrap_status") or "")
    watch_next_run = now_ms + int(watch_ms)
    research_next_run = (
        now_ms + int(research_ms)
        if bootstrap in {"pending", "running"}
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
    logger.info("registered autonomous jobs for %s", agent_id)


def schedule_first_research_after_bootstrap(agent_id: str, *, delay_ms: int = 30_000) -> None:
    """Queue the first scheduled research turn shortly after bootstrap completes."""
    if not is_autonomous_scheduler_enabled():
        return
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore()
    research_id = f"{agent_id}-research"
    job = store.get(research_id)
    if job is None:
        return
    now_ms = int(time.time() * 1000)
    target = now_ms + int(delay_ms)
    if job.next_run_at is None or job.next_run_at < target:
        job.next_run_at = target
        store.upsert(job)


def unregister_agent_jobs(agent_id: str) -> dict[str, bool]:
    from src.scheduled_research.store import ScheduledResearchJobStore

    store = ScheduledResearchJobStore()
    watch_id, research_id = _job_ids(agent_id)
    return {watch_id: store.delete(watch_id), research_id: store.delete(research_id)}


async def dispatch_autonomous_job(job: ScheduledResearchJob) -> None:
    _ensure_trade_integrations_on_path()
    from trade_integrations.autonomous_agents.watch import dispatch_full_reasoning, run_watch_tick

    agent_id = str((job.config or {}).get("autonomous_agent_id") or "")
    if not agent_id:
        logger.warning("autonomous job missing agent id: %s", job.id)
        return

    job_type = str((job.config or {}).get("job_type") or "")
    if job_type == JOB_TYPE_WATCH:
        await run_watch_tick(agent_id)
        return
    if job_type == JOB_TYPE_RESEARCH:
        await dispatch_full_reasoning(agent_id, turn_kind="research")
        return

    logger.warning("unknown autonomous job type %s", job_type)


def dispatch_autonomous_job_sync(job: ScheduledResearchJob) -> None:
    asyncio.run(dispatch_autonomous_job(job))
