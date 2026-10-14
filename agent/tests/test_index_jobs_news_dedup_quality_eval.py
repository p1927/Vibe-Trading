"""news_dedup_quality_eval scheduled job.

Before this job existed, `run_news_dedup_golden_eval()` (the MLflow-scored semantic-dedup
golden-pair eval) had zero callers outside its own test file — a future regression in
`cluster_threshold()`/`events_are_merge_candidates()` had no automatic signal. See
.claude/backlog/items/2026-08-26-dedup-golden-eval-never-scheduled-dataset-too-small.md.
"""
from __future__ import annotations

import inspect
import re

import pytest

from src.scheduled_research import index_jobs


@pytest.mark.unit
def test_run_news_dedup_quality_eval_job_returns_eval_summary(monkeypatch):
    fake_summary = {"status": "ok", "pair_count": 12, "classification_metrics": {"f1": 0.857}}
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_news_dedup_golden_eval",
        lambda: fake_summary,
    )

    result = index_jobs.run_news_dedup_quality_eval_job()

    assert result == fake_summary


@pytest.mark.unit
def test_run_news_dedup_quality_eval_job_never_raises_on_eval_error(monkeypatch):
    def _raise():
        raise RuntimeError("mlflow tracking uri unreachable")

    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.run_news_dedup_golden_eval",
        _raise,
    )

    result = index_jobs.run_news_dedup_quality_eval_job()

    assert result["status"] == "error"
    assert result["had_errors"] is True
    assert "mlflow tracking uri unreachable" in result["error"]


@pytest.mark.unit
def test_dispatch_index_job_sync_routes_news_dedup_quality_eval(monkeypatch):
    from src.scheduled_research.models import JobStatus, ScheduledResearchJob

    fake_summary = {"status": "ok", "pair_count": 12}
    monkeypatch.setattr(
        index_jobs, "run_news_dedup_quality_eval_job", lambda config: fake_summary
    )

    job = ScheduledResearchJob(
        id="nifty-news-dedup-quality-eval",
        prompt="test",
        schedule="30 2 * * *",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config={"job_type": index_jobs.JOB_TYPE_NEWS_DEDUP_QUALITY_EVAL},
    )

    index_jobs.dispatch_index_job_sync(job)

    assert job.config[index_jobs.LAST_RESULT_CONFIG_KEY]["status"] == "ok"


@pytest.mark.unit
def test_every_dispatched_job_type_is_in_index_job_types():
    """Regression guard for the exact bug in
    .claude/backlog/items/2026-09-16-dedup-eval-runs-as-agent.md: a `JOB_TYPE_*` constant with a
    real ``if job_type == JOB_TYPE_...:`` branch inside `dispatch_index_job_sync` but missing
    from `INDEX_JOB_TYPES` is dead code — `job_dispatch_registry.try_dispatch_pipeline_job` only
    ever calls `dispatch_index_job` when the job_type is a member of `INDEX_JOB_TYPES`, so an
    omitted job type silently falls through to the generic LLM agent-session path instead of its
    real handler. `news_dedup_quality_eval` had exactly this gap: the constant and dispatch
    branch existed, but the frozenset entry was missing.
    """
    # dispatch_index_job_sync itself just wraps pipeline-cancel scoping; the actual
    # `if job_type == JOB_TYPE_...:` cascade lives in _dispatch_index_job_body.
    source = inspect.getsource(index_jobs._dispatch_index_job_body)
    dispatched_job_types = set(re.findall(r"job_type == (JOB_TYPE_\w+)", source))

    assert dispatched_job_types, "expected to find at least one dispatched JOB_TYPE_* constant"

    missing = {
        name
        for name in dispatched_job_types
        if getattr(index_jobs, name) not in index_jobs.INDEX_JOB_TYPES
    }
    assert not missing, (
        f"{missing} have a dispatch_index_job_sync branch but are missing from "
        "INDEX_JOB_TYPES, so try_dispatch_pipeline_job never routes them to their handler"
    )


def _dedup_eval_job():
    from src.scheduled_research.models import JobStatus, ScheduledResearchJob

    return ScheduledResearchJob(
        id="nifty-news-dedup-quality-eval",
        prompt="test",
        schedule="30 2 * * *",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config={"job_type": index_jobs.JOB_TYPE_NEWS_DEDUP_QUALITY_EVAL},
    )


@pytest.mark.unit
def test_try_dispatch_pipeline_job_routes_news_dedup_quality_eval(monkeypatch):
    """The registry entry point the scheduler executor calls claims this job_type (True) and hands it
    to the index dispatcher instead of falling through to the generic agent-session path. Before the
    job was wired, `try_dispatch_pipeline_job` returned False and an LLM agent session was enqueued.

    The dispatcher is stubbed at the registry seam: `news_dedup_quality_eval` is a
    CHILD_PROCESS_JOB_TYPES job (D244), so the real dispatch runs the handler in a child process that
    a monkeypatch here cannot reach (it ran the real golden eval until pytest-timeout)."""
    import asyncio

    from src.scheduled_research import job_dispatch_registry

    routed = []

    async def spy(job):
        routed.append(job.id)

    monkeypatch.setattr(index_jobs, "dispatch_index_job", spy)
    job = _dedup_eval_job()

    assert asyncio.run(job_dispatch_registry.try_dispatch_pipeline_job(job)) is True
    assert routed == [job.id]


@pytest.mark.unit
def test_dispatch_index_job_sync_runs_the_handler_and_records_its_summary(monkeypatch):
    """The child process's own entry point: the handler result lands on the job config."""
    monkeypatch.setattr(
        index_jobs, "run_news_dedup_quality_eval_job", lambda config: {"status": "ok", "pair_count": 12}
    )
    job = _dedup_eval_job()

    index_jobs.dispatch_index_job_sync(job)

    assert job.config[index_jobs.LAST_RESULT_CONFIG_KEY]["status"] == "ok"


@pytest.mark.unit
def test_dispatch_index_job_sync_fails_the_run_when_the_eval_reports_errors(monkeypatch):
    """The handler returns had_errors instead of raising; the dispatcher must still fail the run."""
    monkeypatch.setattr(
        index_jobs, "run_news_dedup_quality_eval_job", lambda config: {"status": "error", "had_errors": True}
    )
    with pytest.raises(Exception):
        index_jobs.dispatch_index_job_sync(_dedup_eval_job())
