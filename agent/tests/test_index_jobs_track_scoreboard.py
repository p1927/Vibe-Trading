"""index_track_scoreboard scheduled job: the post-close recompute that shares the POST route's single-flight
path (`src.trade.scoreboard_refresh`); a failed or concurrent run is never recorded as a success."""
from __future__ import annotations

import threading

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from src.trade import scoreboard_refresh

TYPE = index_jobs.JOB_TYPE_INDEX_TRACK_SCOREBOARD


def _patch_walk_forward(monkeypatch, *, fail=False, gate=None):
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator import (
        scoreboard as scoreboard_mod,
        walk_forward as wf,
    )

    calls = []

    def _track(*, ticker, days, horizon_days, eval_step):
        if gate is not None:
            gate.wait(5)
        if fail:
            raise ValueError("boom")
        calls.append(("track", ticker, days))

    monkeypatch.setattr(scoreboard_mod, "load_scoreboard", lambda ticker: None)
    monkeypatch.setattr(wf, "run_track_walk_forward", _track)
    monkeypatch.setattr(wf, "run_strategy_walk_forward", lambda **kw: calls.append(("strategy", kw["ticker"], kw["days"])))
    return calls


@pytest.mark.unit
def test_default_job_is_post_close_ist_weekdays(tmp_path):
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)
    job = store.get("nifty-track-scoreboard-refresh")
    assert job is not None and job.status == JobStatus.PENDING
    assert job.config["job_type"] == TYPE and TYPE in index_jobs.INDEX_JOB_TYPES
    assert job.timezone == "Asia/Kolkata" and job.schedule == "45 20 * * 1-5"


@pytest.mark.unit
def test_job_runs_the_shared_walk_forward_and_records_done(monkeypatch):
    calls = _patch_walk_forward(monkeypatch)
    summary = index_jobs.run_index_track_scoreboard_job({"ticker": "NIFTY", "days": 365})
    assert summary["status"] == "ok" and summary["job"]["state"] == "done"
    assert calls == [("track", "NIFTY", 730), ("strategy", "NIFTY", 730)]


@pytest.mark.unit
def test_a_failed_recompute_raises_so_the_run_is_recorded_failed(monkeypatch):
    _patch_walk_forward(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match="failed"):
        index_jobs.run_index_track_scoreboard_job({})


@pytest.mark.unit
def test_job_is_single_flight_with_the_route_path(monkeypatch):
    gate = threading.Event()
    _patch_walk_forward(monkeypatch, gate=gate)
    started, _ = scoreboard_refresh.start_refresh("NIFTY")  # e.g. the POST route
    assert started
    summary = index_jobs.run_index_track_scoreboard_job({})
    assert summary == {"status": "skipped", "reason": "refresh_in_flight", "job": summary["job"]}
    gate.set()
    scoreboard_refresh._THREAD.join(5)
    assert scoreboard_refresh.current_job()["state"] == "done"


@pytest.mark.unit
def test_dispatch_attaches_the_summary(monkeypatch):
    monkeypatch.setattr(index_jobs, "run_index_track_scoreboard_job", lambda cfg: {"status": "ok", "job": {}})
    job = ScheduledResearchJob(
        id="x", prompt="p", schedule="1000", next_run_at=0, status=JobStatus.PENDING, created_at=0,
        config={"job_type": TYPE},
    )
    index_jobs.dispatch_index_job_sync(job)
    assert job.config["_last_result_summary"]["status"] == "ok"
