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


@pytest.mark.parametrize(
    "module",
    ["trade_integrations.dataflows.index_research.aggregator", "trade_integrations.context.hub"],
)
def test_early_import_failure_marks_the_job_failed(monkeypatch, module):
    """These imports precede PipelineCancelledError's: a failure used to raise UnboundLocalError from the
    `except PipelineCancelledError` clause itself, skipping fail_job and leaving the job running."""
    import sys

    # the cleanup in `finally` needs pipeline_cancel (which imports context.hub): load it before poisoning
    import trade_integrations.dataflows.index_research.pipeline_cancel  # noqa: F401

    monkeypatch.setitem(sys.modules, module, None)
    job_id, _ = run_jobs.start_job(ticker="NIFTY", horizon_days=14, refresh_constituents=False)
    run_jobs.run_worker(job_id)  # must not raise UnboundLocalError
    rec = run_jobs._get_job_record(job_id)
    assert rec["status"] != "running" and rec.get("error")


def test_a_failure_after_the_import_still_fails_the_job(monkeypatch):
    import trade_integrations.dataflows.index_research.aggregator as agg

    def boom(*a, **k):
        raise RuntimeError("pipeline blew up")

    monkeypatch.setattr(agg, "run_index_research", boom)
    job_id, _ = run_jobs.start_job(ticker="NIFTY", horizon_days=14, refresh_constituents=False)
    run_jobs.run_worker(job_id)
    rec = run_jobs._get_job_record(job_id)
    assert rec["status"] != "running" and "pipeline blew up" in str(rec.get("error"))
