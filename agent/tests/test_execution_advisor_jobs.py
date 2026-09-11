"""The scheduled execution-advisor sweep: the advisory ledger's one writer.

Trade's ``.claude/backlog/items/2026-09-07-advisory-ledger-write-only.md``: advisories used to be
recorded only when a human loaded the panel. The sweep is registered by default, runs per tier
(operational, never release-gated), and fails its run loudly instead of dropping a write.
"""

from __future__ import annotations

import asyncio

import pytest

from src.scheduled_research import execution_advisor_jobs as jobs
from src.scheduled_research.job_dispatch_registry import try_dispatch_pipeline_job
from src.scheduled_research.job_tier_policy import is_collection_job, is_operational_tier_job
from src.scheduled_research.models import ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


def _store(tmp_path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(tmp_path / "jobs.json")


def test_the_sweep_registers_once_by_default(tmp_path) -> None:
    store = _store(tmp_path)

    assert jobs.register_default_execution_advisor_jobs(store) == 1
    assert jobs.register_default_execution_advisor_jobs(store) == 0
    job = store.get(jobs.EXECUTION_ADVISOR_SWEEP_JOB_ID)
    assert job.config["job_type"] == jobs.JOB_TYPE_EXECUTION_ADVISOR_SWEEP
    assert job.schedule == jobs.DEFAULT_EXECUTION_ADVISOR_SWEEP_CRON


def test_the_sweep_is_per_tier_operational_not_release_gated_collection() -> None:
    assert is_operational_tier_job(jobs.JOB_TYPE_EXECUTION_ADVISOR_SWEEP)
    assert not is_collection_job(jobs.JOB_TYPE_EXECUTION_ADVISOR_SWEEP)


def test_the_registry_dispatches_the_sweep_to_advise_positions(monkeypatch) -> None:
    import trade_integrations.dataflows.index_research.execution_advisor as advisor

    calls: list[int] = []
    monkeypatch.setattr(
        advisor,
        "advise_positions",
        lambda: calls.append(1) or [{"symbol": "NIFTY24AUGFUT", "action": "hold"}],
    )
    job = ScheduledResearchJob(
        id=jobs.EXECUTION_ADVISOR_SWEEP_JOB_ID,
        prompt="sweep",
        schedule=jobs.DEFAULT_EXECUTION_ADVISOR_SWEEP_CRON,
        next_run_at=0,
        created_at=0,
        config={"job_type": jobs.JOB_TYPE_EXECUTION_ADVISOR_SWEEP},
    )

    assert asyncio.run(try_dispatch_pipeline_job(job)) is True
    assert calls == [1]


def test_a_failing_sweep_raises_rather_than_reporting_success(monkeypatch) -> None:
    import trade_integrations.dataflows.index_research.execution_advisor as advisor

    def _boom():
        raise OSError("ledger write failed")

    monkeypatch.setattr(advisor, "advise_positions", _boom)
    with pytest.raises(OSError):
        jobs.run_execution_advisor_sweep()


def test_an_unsupported_job_type_raises() -> None:
    job = ScheduledResearchJob(
        id="x", prompt="x", schedule="*/5 * * * *", next_run_at=0, created_at=0,
        config={"job_type": "not_a_sweep"},
    )
    with pytest.raises(ValueError):
        jobs.dispatch_execution_advisor_job_sync(job)
