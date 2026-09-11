"""Scheduled execution-advisor sweep: the advisor's own cadence, not a page view.

Fork-only sidecar (see ``docs/FORK_CONVENTIONS.md``), modelled on ``factor_health_jobs.py``: a new
module plus one loader entry in ``job_dispatch_registry.py``, one registration call in
``scheduled_startup.py``, and one entry in ``job_tier_policy.OPERATIONAL_TIER_JOB_TYPES``.

Why this exists: ``execution_advisor.advise_positions()`` both steps each open position's FSM and
appends the result to the advisory ledger, and its only production caller used to be the
``GET /execution-advisor/positions`` panel route. So a row existed only when a human loaded the
panel, and the FSM's hysteresis (``consecutive_flip_count`` counts *calls*) advanced at the rate
someone happened to look. This job is now the one caller of ``advise_positions`` and the one
ledger writer; the panel reads the ledger (``execution_advisor.latest_advisories``). See Trade's
``.claude/backlog/items/2026-09-07-advisory-ledger-write-only.md``.

**Operational, not collection.** It advises on *this tier's own* OpenAlgo positions, the same
boundary as ``options_position_monitor``: OpenAlgo state is per-tier by design, so it runs in every
tier and is never release-gated. Enabled by default with no flag: an advisor that has to be switched
on is back to recording nothing. A failed run (OpenAlgo unreachable, a ledger write error) raises,
so the scheduler reports it instead of the ledger silently going quiet.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.hub_bridge import ensure_trade_stack_path

logger = logging.getLogger(__name__)

JOB_TYPE_EXECUTION_ADVISOR_SWEEP = "execution_advisor_sweep"
EXECUTION_ADVISOR_JOB_TYPES = frozenset({JOB_TYPE_EXECUTION_ADVISOR_SWEEP})

EXECUTION_ADVISOR_SWEEP_JOB_ID = "execution-advisor-sweep"

#: Every 5 minutes, 09:00-15:55 IST on weekdays: the IN session, when positions move. Evaluated
#: in ``EXECUTION_ADVISOR_SWEEP_TIMEZONE`` — a job's ``timezone=None`` means UTC, which would put
#: this at 14:30-21:25 IST, mostly after the close.
DEFAULT_EXECUTION_ADVISOR_SWEEP_CRON = "*/5 9-15 * * 1-5"
EXECUTION_ADVISOR_SWEEP_TIMEZONE = "Asia/Kolkata"


def run_execution_advisor_sweep() -> dict[str, Any]:
    """One FSM step for every open position, recorded to the ledger. Raises on any failure."""
    ensure_trade_stack_path()
    from trade_integrations.dataflows.index_research.execution_advisor import advise_positions

    advisories = advise_positions()
    actions: dict[str, int] = {}
    for advisory in advisories:
        action = str(advisory.get("action") or "unknown")
        actions[action] = actions.get(action, 0) + 1
    summary = {"positions": len(advisories), "actions": actions}
    logger.info("execution advisor sweep: %s", summary)
    return summary


def dispatch_execution_advisor_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type != JOB_TYPE_EXECUTION_ADVISOR_SWEEP:
        raise ValueError(f"unsupported execution_advisor job_type: {job_type!r}")
    run_execution_advisor_sweep()


async def dispatch_execution_advisor_job(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, dispatch_execution_advisor_job_sync)


def register_default_execution_advisor_jobs(store: ScheduledResearchJobStore) -> int:
    """Register the sweep when missing. Returns count created (0 or 1)."""
    if store.get(EXECUTION_ADVISOR_SWEEP_JOB_ID) is not None:
        return 0
    validate_schedule(DEFAULT_EXECUTION_ADVISOR_SWEEP_CRON)
    now_ms = int(time.time() * 1000)
    store.upsert(
        ScheduledResearchJob(
            id=EXECUTION_ADVISOR_SWEEP_JOB_ID,
            prompt="Execution advisor: one FSM step per open position, recorded to the advisory ledger",
            schedule=DEFAULT_EXECUTION_ADVISOR_SWEEP_CRON,
            timezone=EXECUTION_ADVISOR_SWEEP_TIMEZONE,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_EXECUTION_ADVISOR_SWEEP},
        )
    )
    logger.info(
        "registered execution advisor sweep %s (%s)",
        EXECUTION_ADVISOR_SWEEP_JOB_ID,
        DEFAULT_EXECUTION_ADVISOR_SWEEP_CRON,
    )
    return 1
