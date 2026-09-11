"""Golden-eval jobs: dispatch budget, cancel eligibility, and job-scoped cancel for dst-eval.

Regression for .claude/backlog/items/2026-09-11-eval-jobs-exceed-dispatch-timeout.md:
`news_quality_eval` and `index_research_eval` hit their 30-minute dispatch timeout two runs in a
row. `news_quality_eval` was never even asked to stop, because it was neither `index_`-prefixed
nor listed in `_JOB_DISPATCH_TIMEOUT_MS`. `dispatch_dst_eval_job_sync` never bound the job id, so
a cancel aimed at `dst-eval-index-research` was invisible to its sub-evals' checkpoints.
"""

from __future__ import annotations

import pytest
from src.scheduled_research import dst_eval_jobs, index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.staleness import (
    EVAL_JOB_DISPATCH_TIMEOUT_MS,
    _request_pipeline_cancel_on_dispatch_timeout,
    dispatch_timeout_ms_for,
    stale_running_ms_for,
)
from trade_integrations.dataflows.index_research import pipeline_cancel


@pytest.fixture(autouse=True)
def _isolated_cancel_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    pipeline_cancel.set_pipeline_job_id(None)
    yield
    pipeline_cancel.set_pipeline_job_id(None)


def _job(job_id: str, job_type: str, **config) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id,
        prompt="p",
        schedule="0 2 * * *",
        next_run_at=0,
        status=JobStatus.PENDING,
        created_at=0,
        config={"job_type": job_type, **config},
    )


def test_eval_budget_is_sixty_minutes() -> None:
    assert EVAL_JOB_DISPATCH_TIMEOUT_MS == 60 * 60 * 1000


@pytest.mark.parametrize("job_type", ["news_quality_eval", "index_research_eval"])
def test_eval_job_types_get_the_eval_budget(job_type: str) -> None:
    assert dispatch_timeout_ms_for(_job("j", job_type)) == EVAL_JOB_DISPATCH_TIMEOUT_MS
    # The watchdog must stay above the dispatch budget, or it recovers the job mid-run and the
    # timeout's own failure write is discarded.
    assert stale_running_ms_for(_job("j", job_type)) > EVAL_JOB_DISPATCH_TIMEOUT_MS


def test_news_quality_eval_definition_pins_the_eval_budget() -> None:
    """The registered definition pins dispatch_timeout_ms in config, which overrides the per-type
    table and is reconciled into the existing live record on boot, so it must carry the new value."""
    from src.scheduled_research.store import ScheduledResearchJobStore  # noqa: F401

    captured: list[ScheduledResearchJob] = []

    class _Store:
        def get(self, job_id):
            return None

        def upsert(self, job, **kw):
            captured.append(job)

    index_jobs.register_default_index_jobs(_Store())  # type: ignore[arg-type]
    job = next((j for j in captured if j.id == "nifty-news-quality-eval"), None)
    if job is None:
        pytest.skip("index job registration is disabled in this environment")
    assert job.config["dispatch_timeout_ms"] == EVAL_JOB_DISPATCH_TIMEOUT_MS
    assert dispatch_timeout_ms_for(job) == EVAL_JOB_DISPATCH_TIMEOUT_MS


@pytest.mark.parametrize("job_type", ["news_quality_eval", "index_research_eval"])
def test_dispatch_timeout_asks_eval_jobs_to_stop(job_type: str) -> None:
    _request_pipeline_cancel_on_dispatch_timeout("job-a", job_type)
    pipeline_cancel.set_pipeline_job_id("job-a")
    with pytest.raises(pipeline_cancel.PipelineCancelledError) as excinfo:
        pipeline_cancel.check_pipeline_cancel()
    assert excinfo.value.reason == "dispatch_timeout:job-a"


def test_dst_eval_dispatch_sees_its_own_timeout_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sub-eval's checkpoint inside dispatch_dst_eval_job_sync sees the cancel aimed at its job."""
    checkpoints: list[int] = []

    def body(job: ScheduledResearchJob) -> None:
        for i in range(5):
            if i == 2:
                _request_pipeline_cancel_on_dispatch_timeout(job.id, "index_research_eval")
            pipeline_cancel.check_pipeline_cancel()
            checkpoints.append(i)

    monkeypatch.setattr(dst_eval_jobs, "_dispatch_dst_eval_job_body", body)
    with pytest.raises(pipeline_cancel.PipelineCancelledError):
        dst_eval_jobs.dispatch_dst_eval_job_sync(_job("dst-eval-index-research", "index_research_eval"))
    assert checkpoints == [0, 1]
    # Unbound and own flag cleared afterwards, so the job's next run is not cancelled by it.
    assert pipeline_cancel._current_job_id.get() is None
    ran: list[str] = []
    monkeypatch.setattr(dst_eval_jobs, "_dispatch_dst_eval_job_body", lambda job: ran.append(job.id))
    dst_eval_jobs.dispatch_dst_eval_job_sync(_job("dst-eval-index-research", "index_research_eval"))
    assert ran == ["dst-eval-index-research"]


def test_dst_eval_dispatch_does_not_see_another_jobs_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _request_pipeline_cancel_on_dispatch_timeout("some-other-job", "index_research_eval")
    monkeypatch.setattr(
        dst_eval_jobs, "_dispatch_dst_eval_job_body", lambda job: pipeline_cancel.check_pipeline_cancel()
    )
    dst_eval_jobs.dispatch_dst_eval_job_sync(_job("dst-eval-index-research", "index_research_eval"))


def test_dst_eval_dispatch_still_routes_unknown_types_to_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="unsupported dst_eval job_type"):
        dst_eval_jobs.dispatch_dst_eval_job_sync(_job("x", "nope"))
