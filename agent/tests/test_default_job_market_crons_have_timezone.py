"""Every default job whose cron is Mon-Fri (`1-5`) (market-day intent) must carry a timezone.

A null timezone means UTC: `*/15 9-16 * * 1-5` then runs 14:30-22:30 IST, market closed.
Run: python -m pytest tests/test_default_job_market_crons_have_timezone.py
"""
import tempfile
from pathlib import Path

from src.scheduled_research import (
    capture_jobs, decision_grading_jobs, dst_eval_jobs, execution_advisor_jobs,
    factor_health_jobs, financial_knowledge_jobs, hub_calibration_jobs, index_jobs,
    options_jobs, trade_data_jobs,
)
from src.scheduled_research.store import ScheduledResearchJobStore

MODULES = (
    capture_jobs, decision_grading_jobs, dst_eval_jobs, execution_advisor_jobs,
    factor_health_jobs, financial_knowledge_jobs, hub_calibration_jobs, index_jobs,
    options_jobs, trade_data_jobs,
)


def test_weekday_restricted_default_crons_have_timezone():
    store = ScheduledResearchJobStore(Path(tempfile.mkdtemp()) / "jobs.json")
    for mod in MODULES:
        for name in dir(mod):
            if name.startswith("register_default_"):
                getattr(mod, name)(store)
    jobs = store.list_jobs()
    assert jobs, "no default jobs registered; the check would be vacuous"
    bad = [j.id for j in jobs if j.schedule.split()[4] == "1-5" and not j.timezone]
    assert not bad, f"market-day crons with null timezone (run in UTC): {bad}"
