"""Per-market twins of one scheduled job must not share a display label (Prediction tab 'Run now' / 'Cancel')."""

from __future__ import annotations

from pathlib import Path

from src.scheduled_research.index_jobs import register_default_index_jobs
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade.index_prediction_jobs import _job_display_label


def test_per_market_jobs_get_distinct_labels_and_india_stays_unsuffixed(tmp_path: Path) -> None:
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    register_default_index_jobs(store)
    jobs = list(store.load().values())
    labels = {job.id: _job_display_label(job) for job in jobs}
    assert labels["nifty-hub-news-ingest-full"] == "Hub news ingest (full)"
    assert labels["us-hub-news-ingest-full"] == "Hub news ingest (full) [US]"
    assert labels["us-hub-news-ingest-light"] == "Hub news ingest (light) [US]"
    # no two jobs of the same job type, mode and market share a label
    seen: dict[str, str] = {}
    for job in jobs:
        label = labels[job.id]
        assert label not in seen, f"{job.id} and {seen[label]} share the label {label!r}"
        seen[label] = job.id
