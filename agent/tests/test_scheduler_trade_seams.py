"""Seam checks for the Vibe scheduler / trade bridge (Trade review R2, seams-vibe-fork-internals).

One file, one check per defect found at the boundary:

* every recovery path advances a job's ``next_run_at`` in the job's OWN timezone (IST jobs were
  advanced in UTC by shutdown / stale / boot recovery and by the startup backlog deferral);
* the recording store (the last store moved onto ``JobStore``) keeps its recording-only rules:
  ``waiting_for_open`` has no pid and is not a dead worker, a log append must not promote it to
  ``running`` (no worker exists yet), a failed ``wait_for_open`` scheduling fails the new job and
  raises instead of spawning a worker against a closed market;
* ``persist_trade_widget`` returns ``None`` for an id its store refuses (a ticker like ``M&M``): the
  advisory approve route must say so instead of returning a widget id execute-basket cannot load.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.scheduled_research import lifecycle
from src.scheduled_research.executor import ScheduledResearchExecutor, first_fire_ms, next_due
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

IST = "Asia/Kolkata"
# 2026-10-14 05:00 UTC = 10:30 IST: the next "30 9 * * 1-5" slot is tomorrow 09:30 IST (04:00 UTC)
# in IST but today 09:30 UTC (15:00 IST) in UTC, so the two readings cannot coincide.
NOW_MS = int(datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
CRON = "30 9 * * 1-5"


def _job(job_id: str, *, status: JobStatus, job_type: str = "index_plan_refresh") -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id, prompt="p", schedule=CRON, timezone=IST, next_run_at=NOW_MS - 60_000, status=status,
        created_at=1, last_run_at=NOW_MS - 3_600_000, config={"job_type": job_type},
    )


def _expect_ist(job: ScheduledResearchJob) -> None:
    assert job.next_run_at == next_due(CRON, NOW_MS, IST)
    assert job.next_run_at != next_due(CRON, NOW_MS, None), "advanced in UTC, not the job's own timezone"
    local = datetime.fromtimestamp(job.next_run_at / 1000, ZoneInfo(IST))
    assert (local.hour, local.minute) == (9, 30)


@pytest.fixture()
def store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


async def _noop(job: ScheduledResearchJob) -> None:
    return None


def test_executor_recovery_paths_advance_in_the_jobs_timezone(store, monkeypatch) -> None:
    monkeypatch.setenv("STACK_PROFILE", "dev")
    store.upsert(_job("stale", status=JobStatus.RUNNING))
    executor = ScheduledResearchExecutor(store, _noop)
    assert executor.recover_stale_running(NOW_MS, startup=True) == 1
    _expect_ist(store.get("stale"))

    store.upsert(_job("shutdown", status=JobStatus.RUNNING))
    assert executor.recover_all_running_on_shutdown(NOW_MS) == 1
    _expect_ist(store.get("shutdown"))

    # a collection job on the dev tier cannot dispatch: its overdue slot is deferred, in its own timezone
    store.upsert(_job("deferred", status=JobStatus.PENDING, job_type="hub_news_ingest"))
    assert executor.defer_startup_backlog(NOW_MS) >= 1
    _expect_ist(store.get("deferred"))


def test_stack_recovery_advances_in_the_jobs_timezone(store, monkeypatch) -> None:
    store.upsert(_job("boot", status=JobStatus.RUNNING))
    monkeypatch.setattr(lifecycle.time, "time", lambda: NOW_MS / 1000)
    assert lifecycle.recover_persisted_scheduler_jobs(store, mode="all_running", reason="r") == 1
    _expect_ist(store.get("boot"))


@pytest.fixture()
def rec(tmp_path, monkeypatch):
    from src.trade import recording_jobs

    monkeypatch.setattr(recording_jobs, "_jobs_root", lambda: tmp_path / "recording")
    recording_jobs.RECORDING_JOBS.clear()
    recording_jobs._ACTIVE_BY_SCOPE.clear()
    return recording_jobs


def test_waiting_for_open_is_not_a_dead_worker_and_logs_do_not_wake_it(rec) -> None:
    job_id, _ = rec.start_job(underlyings=["NIFTY"], wait_for_open=True)
    assert rec.set_waiting_for_open_at(job_id, next_open_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert rec.reconcile_job(job_id) is False  # no pid is normal while waiting
    rec.append_log(job_id, {"stage": "control", "message": "stop requested"})
    snap = rec.get_job(job_id)
    assert snap["status"] == "waiting_for_open"  # a log must not promote it: no worker exists yet
    assert snap["run_started_at"] is None
    rec.mark_running(job_id)
    assert rec.get_job(job_id)["status"] == "running"
    assert rec.get_job(job_id)["run_started_at"]  # the wall-clock budget runs from here, not creation


def test_one_live_recording_job_is_reused_by_a_second_start(rec) -> None:
    first, reused = rec.start_job(underlyings=["NIFTY"])
    assert reused is False
    rec._mutate_job_on_disk(first, lambda job: job.update(worker_pid=os.getpid()) or True)
    # a process that never saw the job (empty memory) still finds it on disk and reuses it
    rec.RECORDING_JOBS.clear()
    rec._ACTIVE_BY_SCOPE.clear()
    second, reused = rec.start_job(underlyings=["BANKNIFTY"])
    assert (second, reused) == (first, True)


def test_failed_wait_for_open_scheduling_fails_the_new_job_and_raises(rec, monkeypatch) -> None:
    import nautilus_openalgo_bridge.market_hours as market_hours
    from src.trade import recording_wait_scheduler

    monkeypatch.setattr(market_hours, "is_real_nse_market_open", lambda *a, **k: False)
    monkeypatch.setattr(market_hours, "next_real_nse_open_at", lambda **k: datetime(2099, 1, 1, tzinfo=timezone.utc))

    def _boom(**_):
        raise RuntimeError("scheduler store unreadable")

    monkeypatch.setattr(recording_wait_scheduler, "schedule_recording_wake", _boom)
    spawned: list[str] = []
    monkeypatch.setattr(rec, "spawn_worker", spawned.append)
    with pytest.raises(RuntimeError, match="scheduler store unreadable"):
        rec.kick_recording(underlyings=["NIFTY"], wait_for_open=True)
    assert spawned == []  # no worker against a closed market
    jobs = list(rec.RECORDING_JOBS.values())
    assert [j["status"] for j in jobs] == ["error"]
    assert "wait_for_open scheduling failed" in jobs[0]["error"]


def test_approve_route_refuses_a_widget_the_store_cannot_persist(monkeypatch) -> None:
    from fastapi import HTTPException

    from src.api import advisory_routes
    from trade_integrations.dataflows.options_research import widget_payload
    from trade_integrations.trade_widgets import store as widget_store

    monkeypatch.setattr(widget_payload, "build_options_trade_widget", lambda ticker, refresh=False: {"widget_id": "tp_M&M_abcdef123456"})
    monkeypatch.setattr(widget_store, "persist_trade_widget", lambda widget: None)
    body = advisory_routes.ApproveCandidateRequest(ticker="M&M", strategy_name="x")
    with pytest.raises(HTTPException) as exc:
        advisory_routes.prepare_advisory_widget(body)
    assert exc.value.status_code == 400


def test_first_fire_rule_is_one_function_for_every_create_surface() -> None:
    from src.scheduled_research.playbooks import get_playbook

    # interval and timezone-less jobs fire at creation; a timezone-carrying cron fires at its authored slot
    assert first_fire_ms("60000", NOW_MS, IST) == NOW_MS
    assert first_fire_ms(CRON, NOW_MS, None) == NOW_MS
    assert first_fire_ms(CRON, NOW_MS, IST) == next_due(CRON, NOW_MS, IST)
    # the playbook surface agrees (it used to carry its own copy of the rule)
    job = get_playbook("premarket-brief").to_job(schedule=CRON, timezone=IST, now_ms=NOW_MS)
    assert job.next_run_at == next_due(CRON, NOW_MS, IST)
