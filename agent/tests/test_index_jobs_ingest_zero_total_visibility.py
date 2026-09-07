"""A gated, zero-collection hub-news ingest run must not read as ``completed``.

See .claude/backlog/items/2026-09-07-decision-04-sweeps-never-executed.md — NIFTY's daily
``full`` ingest reported four consecutive ``status: completed, consecutive_failures: 0``
runs while its ``last_result_summary`` showed ``pipeline_paused: true`` and every total at
zero, because ``run_hub_news_ingest_job`` returns a normal payload rather than raising when
a gate stops the run. Nothing downstream could tell that apart from a real ingest.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.store import ScheduledResearchJobStore


def _ingest_job(tmp_path):
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)
    job = store.get("nifty-hub-news-ingest-full")
    assert job is not None
    return job


@pytest.mark.unit
@pytest.mark.parametrize(
    "summary",
    [
        # The exact shape observed on release: wiki gate returned before any source ran.
        {
            "mode": "full",
            "pipeline_paused": True,
            "pause_reason": "llm_wiki_unavailable",
            "totals": {"queued": 0, "ingested": 0, "verified": 0, "created": 0, "updated": 0},
        },
        # `blocked` is the other gate flag; errors alone are not progress.
        {"blocked": True, "totals": {"queued": 0, "ingested": 0, "error": 4}},
    ],
)
def test_gated_zero_total_ingest_raises_so_executor_sees_a_failure(tmp_path, monkeypatch, summary):
    monkeypatch.setattr(index_jobs, "run_hub_news_ingest_job", lambda config=None: summary)

    with pytest.raises(RuntimeError, match="collected nothing"):
        index_jobs.dispatch_index_job_sync(_ingest_job(tmp_path))


@pytest.mark.unit
@pytest.mark.parametrize(
    "summary",
    [
        # Genuinely quiet cycle: nothing new to collect, but no gate stopped it.
        {"mode": "tight", "totals": {"queued": 0, "ingested": 0}},
        # Distillation deferred while collection still queued refs -- the normal
        # backpressure case, and the reason `pipeline_paused` alone must not fail.
        {
            "mode": "full",
            "pipeline_paused": True,
            "pause_reason": "llm_wiki_unavailable",
            "totals": {"queued": 126, "ingested": 0},
        },
    ],
)
def test_successful_or_quiet_ingest_still_completes(tmp_path, monkeypatch, summary):
    monkeypatch.setattr(index_jobs, "run_hub_news_ingest_job", lambda config=None: summary)

    index_jobs.dispatch_index_job_sync(_ingest_job(tmp_path))  # must not raise


@pytest.mark.unit
def test_result_summary_is_still_recorded_on_the_failing_path(tmp_path, monkeypatch):
    """The raise must come *after* the summary is attached, or the operator loses
    the only evidence of why the run collected nothing."""
    summary = {
        "pipeline_paused": True,
        "pause_reason": "llm_wiki_unavailable",
        "totals": {"queued": 0},
    }
    monkeypatch.setattr(index_jobs, "run_hub_news_ingest_job", lambda config=None: summary)
    job = _ingest_job(tmp_path)

    with pytest.raises(RuntimeError):
        index_jobs.dispatch_index_job_sync(job)

    recorded = job.config.get(index_jobs.LAST_RESULT_CONFIG_KEY)
    assert recorded, "last_result_summary was not attached before the raise"
    assert recorded.get("pause_reason") == "llm_wiki_unavailable"
