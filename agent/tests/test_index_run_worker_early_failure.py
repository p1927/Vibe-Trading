"""A failure before PipelineCancelledError is imported must fail the job, not leave it running."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.trade import index_prediction_run_jobs as run_jobs

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(run_jobs, "INDEX_PREDICTION_RUN_JOBS", {})
    monkeypatch.setattr(run_jobs, "_ACTIVE_BY_TICKER", {})
    monkeypatch.setattr("src.trade.hub_bridge.trade_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))


def test_early_import_failure_marks_the_job_failed(monkeypatch):
    """The aggregator import precedes PipelineCancelledError's: its failure used to raise UnboundLocalError from the
    `except PipelineCancelledError` clause itself, skipping fail_job and leaving the job running."""
    import sys

    monkeypatch.setitem(sys.modules, "trade_integrations.dataflows.index_research.aggregator", None)
    job_id, _ = run_jobs.start_job(ticker="NIFTY", horizon_days=14, refresh_constituents=False)
    run_jobs.run_worker(job_id)  # must not raise UnboundLocalError
    rec = run_jobs._get_job_record(job_id)
    assert rec["status"] != "running" and rec.get("error")
