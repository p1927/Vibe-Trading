"""A news-ingest source that fetches nothing run after run must be flagged, not read as quiet.

See .claude/backlog/items/2026-09-08-ingest-per-source-consecutive-zero-health-signal.md. Trade's
``run_hub_news_ingest`` reports ``sources_empty`` per run. ``ingest_source_streaks`` counts
consecutive runs per job and names a source that reaches the threshold. That is a signal only: the
job still completes.
"""

from __future__ import annotations

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.ingest_source_streaks import (
    SOURCE_ZERO_STREAKS_CONFIG_KEY,
    SOURCES_STALE_CONFIG_KEY,
    STALE_AFTER_CONSECUTIVE_EMPTY_RUNS,
    record_source_zero_streaks,
)
from src.scheduled_research.store import ScheduledResearchJobStore

SUMMARY_KEY = index_jobs.LAST_RESULT_CONFIG_KEY
JOB_ID = "nifty-hub-news-ingest-full"
FETCHED = {"entries": 40, "queued": 0, "ingested": 0}
DEAD = {"results": 0, "queued": 0, "ingested": 0}


def _ingest_job(tmp_path):
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)
    job = store.get(JOB_ID)
    assert job is not None
    return job


def _result(empty: set[str], sources: dict) -> dict:
    return {"mode": "full", "sources": sources, "sources_empty": sorted(empty), "totals": {"queued": 1}}


def _record(job, result):
    return record_source_zero_streaks(job, result, summary_config_key=SUMMARY_KEY)


@pytest.mark.unit
def test_streak_counts_consecutive_empty_runs_and_resets_once_the_source_fetches(tmp_path):
    job = _ingest_job(tmp_path)
    for _ in range(2):
        _record(job, _result({"web_search_global"}, {"rss": FETCHED, "web_search_global": DEAD}))
    assert job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] == {"rss": 0, "web_search_global": 2}

    _record(job, _result(set(), {"rss": FETCHED, "web_search_global": {"results": 3}}))
    assert job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] == {"rss": 0, "web_search_global": 0}


@pytest.mark.unit
def test_a_source_is_flagged_at_the_threshold_and_unflagged_when_it_recovers(tmp_path):
    job = _ingest_job(tmp_path)
    dead_run = _result({"web_search_global"}, {"rss": FETCHED, "web_search_global": DEAD})
    for _ in range(STALE_AFTER_CONSECUTIVE_EMPTY_RUNS - 1):
        job.config[SUMMARY_KEY] = {"mode": "full"}
        assert _record(job, dead_run) == {}
    assert SOURCES_STALE_CONFIG_KEY not in job.config
    assert "sources_stale" not in job.config[SUMMARY_KEY]

    job.config[SUMMARY_KEY] = {"mode": "full"}
    stale = _record(job, dead_run)
    assert stale == {"web_search_global": STALE_AFTER_CONSECUTIVE_EMPTY_RUNS}
    assert job.config[SOURCES_STALE_CONFIG_KEY] == stale
    assert job.config[SUMMARY_KEY] == {"mode": "full", "sources_stale": stale}

    _record(job, _result(set(), {"rss": FETCHED, "web_search_global": {"results": 3}}))
    assert SOURCES_STALE_CONFIG_KEY not in job.config


@pytest.mark.unit
def test_a_skipped_source_keeps_its_streak_and_a_dropped_source_is_forgotten(tmp_path):
    job = _ingest_job(tmp_path)
    job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] = {"web_search_sector": 2, "moneycontrol": 5}

    _record(job, _result(set(), {"rss": FETCHED, "web_search_sector": {"skipped": "light mode"}}))

    assert job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] == {"rss": 0, "web_search_sector": 2}


@pytest.mark.unit
@pytest.mark.parametrize(
    "result",
    [
        None,
        # run_hub_news_ingest_job's own error payload: nothing was observed.
        {"status": "error", "error": "boom", "mode": "full", "had_errors": True},
        # an older Trade build that predates `sources_empty`.
        {"mode": "full", "sources": {"rss": DEAD}, "totals": {"queued": 0}},
    ],
)
def test_a_result_without_sources_empty_leaves_the_streaks_alone(tmp_path, result):
    job = _ingest_job(tmp_path)
    job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] = {"rss": 2}

    assert _record(job, result) == {}
    assert job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] == {"rss": 2}


@pytest.mark.unit
def test_dispatch_flags_a_dead_source_without_failing_the_job(tmp_path, monkeypatch):
    """Through the real dispatch branch: a source dead for N runs is reported, and the job itself
    still completes, because zero rows from one source is not a failed run."""
    dead_run = {
        "mode": "full",
        "sources": {"rss": FETCHED, "web_search_macro": DEAD},
        "sources_empty": ["web_search_macro"],
        "totals": {"queued": 4, "ingested": 0},
    }
    monkeypatch.setattr(index_jobs, "run_hub_news_ingest_job", lambda config=None: dead_run)
    job = _ingest_job(tmp_path)

    for _ in range(STALE_AFTER_CONSECUTIVE_EMPTY_RUNS):
        index_jobs.dispatch_index_job_sync(job)  # must not raise

    expected = {"web_search_macro": STALE_AFTER_CONSECUTIVE_EMPTY_RUNS}
    assert job.config[SOURCES_STALE_CONFIG_KEY] == expected
    assert job.config[SUMMARY_KEY]["sources_stale"] == expected


@pytest.mark.unit
def test_streaks_survive_default_job_re_registration(tmp_path):
    """The count has to outlive a restart, or a source dead across boots never reaches N."""
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)
    job = store.get(JOB_ID)
    job.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] = {"rss": 2}
    job.config[SOURCES_STALE_CONFIG_KEY] = {"web_search_global": 3}
    store.upsert(job)

    index_jobs.register_default_index_jobs(store)

    reloaded = store.get(JOB_ID)
    assert reloaded.config[SOURCE_ZERO_STREAKS_CONFIG_KEY] == {"rss": 2}
    assert reloaded.config[SOURCES_STALE_CONFIG_KEY] == {"web_search_global": 3}
