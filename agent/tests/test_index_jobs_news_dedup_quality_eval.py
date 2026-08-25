"""news_dedup_quality_eval scheduled job.

Before this job existed, `run_news_dedup_golden_eval()` (the MLflow-scored semantic-dedup
golden-pair eval) had zero callers outside its own test file — a future regression in
`cluster_threshold()`/`events_are_merge_candidates()` had no automatic signal. See
.claude/backlog/items/2026-08-26-dedup-golden-eval-never-scheduled-dataset-too-small.md.
"""
from __future__ import annotations

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
