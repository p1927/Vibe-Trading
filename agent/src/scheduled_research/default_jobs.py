"""The one place every code-registered default job is registered and brought up to date.

Each ``register_default_*`` function creates a job only when its id is missing, so a record stored
before a default changed keeps its old value forever. ``register_all_default_jobs`` registers the
families, then reconciles every stored default against what the code registers today
(``store.reconcile_default_job``: schedule, timezone, prompt and config; runtime state untouched). A default
cron is authored in IST (``DEFAULT_JOB_TIMEZONE``), and ``timezone=None`` means UTC, which put "evening
maintenance" (18:35) at 00:05 IST. Used by API startup and by the test that every default has a timezone.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from src.scheduled_research.store import ScheduledResearchJobStore, reconcile_default_job

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
    _register_families(store)
    with tempfile.TemporaryDirectory() as tmp:  # what the code registers today: the definitions to carry onto the store
        fresh = ScheduledResearchJobStore(path=Path(tmp) / "jobs.json")
        _register_families(fresh)
        code_jobs = list(fresh.load().values())
    for code_job in code_jobs:
        reconcile_default_job(store, code_job)
