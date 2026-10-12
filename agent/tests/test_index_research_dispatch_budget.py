"""Regression for 2026-10-12-forecast-replay-s8-recording-coverage: the weekly `nifty-index-research`
dispatch has a 30-minute timeout, but its constituent refresh was bounded only by the 88-minute
manual-run wall clock, so 4 of its last 5 runs hit the timeout before the ledger/fan stages and
wrote no row. The dispatch must bind its own budget and the run must bound the batch by it."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.models import ScheduledResearchJob
from src.trade import index_prediction_run_jobs as run_jobs

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_job_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(run_jobs, "INDEX_PREDICTION_RUN_JOBS", {})
    monkeypatch.setattr(run_jobs, "_ACTIVE_BY_TICKER", {})
    monkeypatch.setattr("src.trade.hub_bridge.trade_repo_root", lambda: tmp_path)
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))


def test_dispatch_binds_budget_inside_dispatch_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.job_deadline import remaining_seconds

    seen: list[float | None] = []
    monkeypatch.setattr(
        index_jobs, "run_index_research_job", lambda cfg: seen.append(remaining_seconds()) or {}
    )
    job = ScheduledResearchJob(
        id="nifty-index-research",
        prompt="x",
        schedule="0 8 * * 1",
        config={"job_type": index_jobs.JOB_TYPE_INDEX_RESEARCH, "ticker": "NIFTY"},
    )
    index_jobs._dispatch_index_job_body(job)

    timeout_s = index_jobs.dispatch_timeout_ms_for(job) / 1000.0
    assert seen[0] is not None and 0.7 * timeout_s < seen[0] <= 0.75 * timeout_s


def test_refresh_batch_budget_is_bounded_by_the_dispatch_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations.job_deadline import job_deadline

    captured: dict[str, float | None] = {}

    def _fake_run(*args, **kwargs):
        captured["budget"] = kwargs["refresh_time_budget_seconds"]
        raise RuntimeError("stop after capturing the budget")

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.run_index_research", _fake_run
    )
    job_id, _ = run_jobs.start_job(ticker="NIFTY", horizon_days=14, refresh_constituents=True)
    with job_deadline(1350.0):
        run_jobs.run_worker(job_id)

    assert captured["budget"] is not None and captured["budget"] <= 1350.0
    # Unbound (a manual run), the batch keeps its own wall-clock budget.
    job_id2, _ = run_jobs.start_job(ticker="BANKNIFTY", horizon_days=14, refresh_constituents=True)
    run_jobs.run_worker(job_id2)
    assert captured["budget"] == max(60.0, run_jobs._REFRESH_WALL_CLOCK_SECONDS - 120.0)
