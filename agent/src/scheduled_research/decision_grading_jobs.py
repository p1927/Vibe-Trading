"""One tier-wide decision-grading job: every agent in the ledger, live, stopped or deleted.

Fork-only sidecar (see ``docs/FORK_CONVENTIONS.md``), modelled on ``execution_advisor_jobs.py``:
a new module plus one loader entry in ``job_dispatch_registry.py`` and one registration call in
``scheduled_startup.py``.

Why this exists (Trade ``docs/DECISIONS.md`` D55): grading used to run only as a per-agent
``<agent_id>-decision-eval`` job, first due 24h after the agent was created. Stop, pause, delete
and clear-all all remove an agent's jobs, so an agent that stopped or was deleted was never
graded again, and one that lived under 24h was never graded at all. Release's only simulation
agent (33 decisions) and the three agents deleted on 2026-09-07 (48) were never graded, against
D21, which keeps a deleted agent's evidence as history for grading. The sweep itself,
``sweep_pending_evaluations(agent_id=None)``, already grades every agent it finds in the decision
ledger and needs no live agent record; only its scheduling was bound to the agent's lifetime.

**Every tier grades** (D2). Agent stores are per-tier, so two tiers' sweeps never write the same
row; which evidence may back the calibration artifact is decided by each row's ``stack_tier``
stamp, not by where grading runs. Registered by default with no enable flag: a grader that has to
be switched on is back to grading nothing. Not operational-tier: it walks market data for every
matured horizon and must not hold the agents' own short-cadence ticks behind it.

**Proposals stay per-agent** (D55): weight-change proposals are emitted only for agents whose
record still exists. Proposing a change to an agent that is gone is meaningless.

A failed sweep or proposal raises, so the scheduler records a failed run instead of the evaluation
ledger silently going quiet.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.scheduled_research.models import JobStatus, ScheduledResearchJob, validate_schedule
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.hub_bridge import ensure_trade_stack_path

logger = logging.getLogger(__name__)

JOB_TYPE_DECISION_GRADING_SWEEP = "decision_grading_sweep"
DECISION_GRADING_JOB_TYPES = frozenset({JOB_TYPE_DECISION_GRADING_SWEEP})

DECISION_GRADING_SWEEP_JOB_ID = "decision-grading-sweep"

#: Daily at 17:00 IST, after the IN close, so each run grades the day's newly matured horizons.
#: Grading looks at horizons measured in days (T+1d, T+3d, expiry); a tighter cadence would re-walk
#: the same not-yet-matured decisions without producing a new verdict.
DEFAULT_DECISION_GRADING_SWEEP_CRON = "0 17 * * *"
DECISION_GRADING_SWEEP_TIMEZONE = "Asia/Kolkata"

#: The retired per-agent job type. Jobs of this type still sitting in a store from before D55 are
#: removed at registration: no pipeline claims the type any more, so a surviving one would fall
#: through ``try_dispatch_pipeline_job`` to the legacy agent-prompt path.
RETIRED_JOB_TYPE_PER_AGENT_DECISION_EVAL = "autonomous_agent_decision_eval"


def run_decision_grading_sweep() -> dict[str, Any]:
    """Grade every matured decision on this tier, then propose per existing agent. Raises on failure."""
    ensure_trade_stack_path()
    from trade_integrations.autonomous_agents.decision_eval_proposals import (
        propose_from_decision_evaluations,
    )
    from trade_integrations.autonomous_agents.decision_evaluation import sweep_pending_evaluations
    from trade_integrations.autonomous_agents.store import list_agents

    sweep = sweep_pending_evaluations(agent_id=None)
    logger.info("decision grading sweep: %s", sweep)

    # After grading, never before: a proposal reads the rows the sweep just wrote. Pending-only;
    # a human promotes. Every agent is attempted even if one fails, then the run fails loudly.
    proposals: dict[str, str] = {}
    failures: dict[str, str] = {}
    for agent in list_agents():
        agent_id = str(agent.get("id") or "")
        try:
            result = propose_from_decision_evaluations(agent_id=agent_id)
        except Exception as exc:  # re-raised below, after the other agents had their turn
            logger.exception("decision eval proposal failed for %s", agent_id)
            failures[agent_id] = f"{type(exc).__name__}: {exc}"
            continue
        proposals[agent_id] = str(result.get("status"))
    summary = {"sweep": sweep, "proposals": proposals}
    logger.info("decision grading proposals: %s", proposals)
    if failures:
        raise RuntimeError(f"decision eval proposal failed for {len(failures)} agent(s): {failures}")
    return summary


def dispatch_decision_grading_job_sync(job: ScheduledResearchJob) -> None:
    job_type = str(job.config.get("job_type") or "")
    if job_type != JOB_TYPE_DECISION_GRADING_SWEEP:
        raise ValueError(f"unsupported decision_grading job_type: {job_type!r}")
    run_decision_grading_sweep()


async def dispatch_decision_grading_job(job: ScheduledResearchJob) -> None:
    from src.scheduled_research.run_log_buffer import run_logged

    await run_logged(job, dispatch_decision_grading_job_sync)


def remove_retired_per_agent_decision_eval_jobs(store: ScheduledResearchJobStore) -> list[str]:
    """Delete every pre-D55 ``<agent_id>-decision-eval`` job. Returns the removed ids."""
    retired = [
        job.id
        for job in store.load().values()
        if str((job.config or {}).get("job_type") or "") == RETIRED_JOB_TYPE_PER_AGENT_DECISION_EVAL
    ]
    for job_id in retired:
        store.delete(job_id)
    if retired:
        logger.info("removed retired per-agent decision-eval jobs: %s", retired)
    return retired


def register_default_decision_grading_jobs(store: ScheduledResearchJobStore) -> int:
    """Register the tier-wide sweep when missing and retire per-agent jobs. Returns count created."""
    remove_retired_per_agent_decision_eval_jobs(store)
    if store.get(DECISION_GRADING_SWEEP_JOB_ID) is not None:
        return 0
    validate_schedule(DEFAULT_DECISION_GRADING_SWEEP_CRON)
    now_ms = int(time.time() * 1000)
    store.upsert(
        ScheduledResearchJob(
            id=DECISION_GRADING_SWEEP_JOB_ID,
            prompt="Decision grading: grade every agent's matured decisions, then propose per existing agent",
            schedule=DEFAULT_DECISION_GRADING_SWEEP_CRON,
            timezone=DECISION_GRADING_SWEEP_TIMEZONE,
            next_run_at=now_ms,
            status=JobStatus.PENDING,
            created_at=now_ms,
            config={"job_type": JOB_TYPE_DECISION_GRADING_SWEEP},
        )
    )
    logger.info(
        "registered decision grading sweep %s (%s)",
        DECISION_GRADING_SWEEP_JOB_ID,
        DEFAULT_DECISION_GRADING_SWEEP_CRON,
    )
    return 1
