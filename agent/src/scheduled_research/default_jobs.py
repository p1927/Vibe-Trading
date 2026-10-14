"""The one place every code-registered default job is registered and brought up to date.

Each ``register_default_*`` function creates a job only when its id is missing, so a record stored
before a default changed keeps its old value forever. ``register_all_default_jobs`` registers the
families, then carries the code's timezone onto stored defaults that have none: a default cron is
authored in IST (``DEFAULT_JOB_TIMEZONE``), and ``timezone=None`` means UTC, which put "evening
maintenance" (18:35) at 00:05 IST. Used by API startup and by the test that every default has a timezone.
"""

from __future__ import annotations

import dataclasses
import logging
import tempfile
import time
from pathlib import Path

from src.scheduled_research.store import ScheduledResearchJobStore

logger = logging.getLogger(__name__)


def _register_families(store: ScheduledResearchJobStore) -> None:
    # D80: every family is always registered; each reads its own `*_ENABLE_SCHEDULER` env var only to
    # decide whether a newly created job starts paused (docs/DECISIONS.md D80).
    from src.scheduled_research.capture_jobs import register_default_hub_capture_jobs
    from src.scheduled_research.decision_grading_jobs import register_default_decision_grading_jobs
    from src.scheduled_research.dst_eval_jobs import register_default_dst_eval_jobs
    from src.scheduled_research.execution_advisor_jobs import register_default_execution_advisor_jobs
    from src.scheduled_research.factor_health_jobs import register_default_factor_health_jobs
    from src.scheduled_research.financial_knowledge_jobs import register_default_financial_knowledge_jobs
    from src.scheduled_research.hub_calibration_jobs import register_default_hub_calibration_jobs
    from src.scheduled_research.index_jobs import register_default_index_jobs
    from src.scheduled_research.options_jobs import register_default_options_jobs
    from src.scheduled_research.trade_data_jobs import register_default_trade_data_jobs

    register_default_index_jobs(store)
    register_default_options_jobs(store)
    register_default_hub_calibration_jobs(store)
    # Not an ``elif``: the NSE browser jobs are never subsumed by the unified hub calibration jobs;
    # register_default_trade_data_jobs itself skips only the fills/archive jobs those do subsume.
    register_default_trade_data_jobs(store)
    register_default_hub_capture_jobs(store)
    register_default_financial_knowledge_jobs(store)
    register_default_dst_eval_jobs(store)
    register_default_factor_health_jobs(store)
    register_default_execution_advisor_jobs(store)  # no enable flag: the advisory ledger's only writer
    register_default_decision_grading_jobs(store)  # no enable flag: the one grader for every agent (D55)


def register_all_default_jobs(store: ScheduledResearchJobStore) -> None:
    from src.scheduled_research.executor import next_due

    _register_families(store)
    with tempfile.TemporaryDirectory() as tmp:  # what the code registers today, to read the intended timezones from
        fresh = ScheduledResearchJobStore(path=Path(tmp) / "jobs.json")
        _register_families(fresh)
        intended = {job.id: job.timezone for job in fresh.load().values()}
    now_ms = int(time.time() * 1000)
    for job in store.load().values():
        tz = intended.get(job.id)
        if tz is None or job.timezone is not None:
            continue
        # A not-yet-due run was timed in UTC: retime it on the wall clock it was authored in. An overdue one
        # still fires at once and then advances in its timezone.
        next_run_at = next_due(job.schedule, now_ms, tz) if job.next_run_at > now_ms else job.next_run_at
        store.upsert(dataclasses.replace(job, timezone=tz, next_run_at=next_run_at))
        logger.info("default job %s: timezone None -> %s, next run %s", job.id, tz, next_run_at)
