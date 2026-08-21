"""Regression coverage for
.claude/backlog/items/2026-08-21-refresh-cron-no-collision-lock.md:
the weekly scheduled full-refresh (``run_index_research_job``) must not race
a concurrent manual "Run analysis" for the same ticker.

Two independent guards are exercised here:
1. ``run_index_research_job`` checks ``get_active_job`` up front and skips
   instead of starting a second run.
2. ``start_job`` itself is lock-protected and reuse-safe per ticker, so even
   two calls that both pass the first check (a check-then-act race) cannot
   create two independent jobs for the same ticker.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scheduled_research import index_jobs
from src.trade import index_prediction_run_jobs as run_jobs

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_job_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Give each test a private in-memory + on-disk job store."""
    monkeypatch.setattr(run_jobs, "INDEX_PREDICTION_RUN_JOBS", {})
    monkeypatch.setattr(run_jobs, "_ACTIVE_BY_TICKER", {})
    monkeypatch.setattr("src.trade.hub_bridge.trade_repo_root", lambda: tmp_path)
    yield


def test_run_index_research_job_skips_when_manual_run_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manual run already active for the ticker must short-circuit the
    scheduled full-refresh before it ever calls start_job/run_worker."""
    active_job = {"job_id": "manual-job-1", "status": "running"}
    monkeypatch.setattr(run_jobs, "get_active_job", lambda ticker: active_job)

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("start_job must not be called while a manual run is active")

    monkeypatch.setattr(run_jobs, "start_job", _must_not_be_called)

    result = index_jobs.run_index_research_job({"ticker": "NIFTY"})

    assert result == {
        "skipped": True,
        "reason": "run_active",
        "ticker": "NIFTY",
        "active_job_id": "manual-job-1",
    }


def test_start_job_dedups_concurrent_calls_for_same_ticker() -> None:
    """Even without the early get_active_job check (e.g. a check-then-act
    race between the cron tick and a manual click), start_job's own
    per-ticker lock must prevent two independent jobs for the same ticker."""
    job_id_1, reused_1 = run_jobs.start_job(
        ticker="NIFTY", horizon_days=14, refresh_constituents=False
    )
    job_id_2, reused_2 = run_jobs.start_job(
        ticker="NIFTY", horizon_days=14, refresh_constituents=False
    )

    assert reused_1 is False
    assert reused_2 is True
    assert job_id_2 == job_id_1


def test_start_job_does_not_dedup_across_different_tickers() -> None:
    """Sanity check: the per-ticker lock must not over-collide unrelated tickers."""
    nifty_id, _ = run_jobs.start_job(ticker="NIFTY", horizon_days=14, refresh_constituents=False)
    banknifty_id, reused = run_jobs.start_job(
        ticker="BANKNIFTY", horizon_days=14, refresh_constituents=False
    )

    assert reused is False
    assert banknifty_id != nifty_id
