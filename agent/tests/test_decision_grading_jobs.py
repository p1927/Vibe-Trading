"""The tier-wide decision-grading job (Trade docs/DECISIONS.md D55).

Grading used to be a per-agent ``<agent_id>-decision-eval`` job that stop/pause/delete removed, so
a stopped or deleted agent was never graded. One job per tier now grades every agent in the ledger
via ``sweep_pending_evaluations(agent_id=None)``; proposals stay per existing agent.
"""

from __future__ import annotations

import asyncio

import pytest

from src.scheduled_research import autonomous_agent_jobs
from src.scheduled_research import decision_grading_jobs as jobs
from src.scheduled_research.job_dispatch_registry import try_dispatch_pipeline_job
from src.scheduled_research.job_tier_policy import is_collection_job, is_operational_tier_job
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.sections import job_section
from src.scheduled_research.store import ScheduledResearchJobStore


def _store(tmp_path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(tmp_path / "jobs.json")


def _job(job_id: str, config: dict) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt=job_id,
        schedule="86400000",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config=config,
    )


def test_the_sweep_registers_once_by_default(tmp_path) -> None:
    store = _store(tmp_path)

    assert jobs.register_default_decision_grading_jobs(store) == 1
    assert jobs.register_default_decision_grading_jobs(store) == 0
    job = store.get(jobs.DECISION_GRADING_SWEEP_JOB_ID)
    assert job.config == {"job_type": jobs.JOB_TYPE_DECISION_GRADING_SWEEP}
    assert "autonomous_agent_id" not in job.config
    assert job.schedule == jobs.DEFAULT_DECISION_GRADING_SWEEP_CRON
    assert job.timezone == jobs.DECISION_GRADING_SWEEP_TIMEZONE


def test_registration_retires_every_per_agent_decision_eval_job(tmp_path) -> None:
    store = _store(tmp_path)
    store.upsert(_job("aa_1-decision-eval", {"job_type": "autonomous_agent_decision_eval", "autonomous_agent_id": "aa_1"}))
    store.upsert(_job("aa_2-decision-eval", {"job_type": "autonomous_agent_decision_eval", "autonomous_agent_id": "aa_2"}))
    store.upsert(_job("aa_1-watch", {"job_type": "autonomous_agent_watch", "autonomous_agent_id": "aa_1"}))

    jobs.register_default_decision_grading_jobs(store)

    remaining = set(store.load())
    assert remaining == {"aa_1-watch", jobs.DECISION_GRADING_SWEEP_JOB_ID}


def test_agents_no_longer_get_a_per_agent_grading_job() -> None:
    assert not any(job_id.endswith("-decision-eval") for job_id in autonomous_agent_jobs.agent_job_ids("aa_x"))
    assert "autonomous_agent_decision_eval" not in autonomous_agent_jobs.AUTONOMOUS_JOB_TYPES


def test_the_sweep_runs_on_every_tier_off_the_operational_loop() -> None:
    assert not is_collection_job(jobs.JOB_TYPE_DECISION_GRADING_SWEEP)
    assert not is_operational_tier_job(jobs.JOB_TYPE_DECISION_GRADING_SWEEP)
    assert job_section(jobs.JOB_TYPE_DECISION_GRADING_SWEEP) == "autonomous_agent"


def _patch_trade(monkeypatch, *, agents, sweep_calls, propose):
    import trade_integrations.autonomous_agents.decision_eval_proposals as proposals
    import trade_integrations.autonomous_agents.decision_evaluation as evaluation
    import trade_integrations.autonomous_agents.store as agent_store

    def _sweep(**kwargs):
        sweep_calls.append(kwargs)
        return {"status": "ok", "scored": 3}

    monkeypatch.setattr(evaluation, "sweep_pending_evaluations", _sweep)
    monkeypatch.setattr(agent_store, "list_agents", lambda: agents)
    monkeypatch.setattr(proposals, "propose_from_decision_evaluations", propose)


def test_the_registry_grades_every_agent_then_proposes_per_existing_agent(monkeypatch) -> None:
    sweep_calls: list[dict] = []
    proposed: list[str] = []

    def _propose(*, agent_id):
        proposed.append(agent_id)
        return {"status": "insufficient_samples"}

    # A stopped agent keeps its record, so it still gets a proposal; a deleted one has no record
    # and gets graded (agent_id=None) but no proposal.
    _patch_trade(
        monkeypatch,
        agents=[{"id": "aa_live", "status": "running"}, {"id": "aa_done", "status": "stopped"}],
        sweep_calls=sweep_calls,
        propose=_propose,
    )
    job = _job(jobs.DECISION_GRADING_SWEEP_JOB_ID, {"job_type": jobs.JOB_TYPE_DECISION_GRADING_SWEEP})

    assert asyncio.run(try_dispatch_pipeline_job(job)) is True
    assert sweep_calls == [{"agent_id": None}]
    assert proposed == ["aa_live", "aa_done"]


def test_one_failing_proposal_does_not_starve_the_others_but_fails_the_run(monkeypatch) -> None:
    proposed: list[str] = []

    def _propose(*, agent_id):
        proposed.append(agent_id)
        if agent_id == "aa_bad":
            raise OSError("weights store unreadable")
        return {"status": "no_pattern"}

    _patch_trade(
        monkeypatch,
        agents=[{"id": "aa_bad"}, {"id": "aa_ok"}],
        sweep_calls=[],
        propose=_propose,
    )
    with pytest.raises(RuntimeError, match="aa_bad"):
        jobs.run_decision_grading_sweep()
    assert proposed == ["aa_bad", "aa_ok"]


def test_a_failing_sweep_raises_rather_than_reporting_success(monkeypatch) -> None:
    import trade_integrations.autonomous_agents.decision_evaluation as evaluation

    def _boom(**_kwargs):
        raise OSError("evaluation parquet write failed")

    monkeypatch.setattr(evaluation, "sweep_pending_evaluations", _boom)
    with pytest.raises(OSError):
        jobs.run_decision_grading_sweep()


def test_an_unsupported_job_type_raises() -> None:
    with pytest.raises(ValueError):
        jobs.dispatch_decision_grading_job_sync(_job("x", {"job_type": "not_a_sweep"}))
