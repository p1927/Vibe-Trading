"""Tests for the Auto Record rearm circuit breaker.

Covers the bug filed at
``.claude/backlog/items/2026-08-25-recording-auto-rearm-no-backoff-duplicate-supplement.md``:
``_maybe_rearm_auto_record`` used to re-kick a fresh recording job every
poll tick with no regard for whether the previous job did anything, so a
consistently fast-failing recording (bad broker token, 0 cycles every
time) turned into an unbounded flood of jobs. These tests exercise the
streak-tracking + cooldown state machine directly via ``asyncio.run``,
since this repo's pytest config has no ``asyncio_mode`` set.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.trade import recording_wait_scheduler as rws


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _fast_fail_job(created_at: float) -> dict:
    """A job that finished almost instantly with nothing recorded."""
    return {
        "status": "done",
        "result": {"cycles": 0},
        "created_at": _iso(created_at),
        "_finished_at": created_at + 1.0,
    }


def _real_job(created_at: float) -> dict:
    """A job that actually recorded something."""
    return {
        "status": "done",
        "result": {"cycles": 42},
        "created_at": _iso(created_at),
        "_finished_at": created_at + 600.0,
    }


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    rws._auto_rearm_state["pending_job_id"] = None
    rws._auto_rearm_state["consecutive_fast_failures"] = 0
    rws._auto_rearm_state["circuit_open_until"] = None
    yield
    rws._auto_rearm_state["pending_job_id"] = None
    rws._auto_rearm_state["consecutive_fast_failures"] = 0
    rws._auto_rearm_state["circuit_open_until"] = None


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, jobs: dict, kicked: list) -> None:
    import asyncio

    def fake_load_auto_record() -> dict:
        return {"enabled": True, "config": {"underlyings": ["NIFTY"]}}

    def fake_get_active_job():
        return None

    def fake_get_job(job_id: str):
        return jobs.get(job_id)

    def fake_kick_recording(**kwargs):
        job_id = f"job-{len(kicked) + 1}"
        kicked.append(job_id)
        return job_id, "queued", False

    monkeypatch.setattr(
        "src.trade.recording_auto.load_auto_record", fake_load_auto_record
    )
    monkeypatch.setattr("src.trade.recording_jobs.get_active_job", fake_get_active_job)
    monkeypatch.setattr("src.trade.recording_jobs.get_job", fake_get_job)
    monkeypatch.setattr("src.trade.recording_jobs.kick_recording", fake_kick_recording)


def test_rearm_kicks_a_job_when_enabled_and_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    kicked: list = []
    _patch_common(monkeypatch, jobs={}, kicked=kicked)

    asyncio.run(rws._maybe_rearm_auto_record())

    assert kicked == ["job-1"]
    assert rws._auto_rearm_state["pending_job_id"] == "job-1"


def test_circuit_opens_after_streak_of_fast_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import time

    now = time.time()
    jobs: dict = {}
    kicked: list = []
    _patch_common(monkeypatch, jobs=jobs, kicked=kicked)

    # Tick 1: nothing pending yet -> kicks job-1.
    asyncio.run(rws._maybe_rearm_auto_record())
    assert kicked == ["job-1"]

    # Ticks 2-4: each prior job is discovered to be a fast failure, so the
    # streak climbs 1 -> 2 -> 3. The 3rd fast failure (streak hits the
    # limit) must open the circuit and skip kicking a 4th job.
    jobs["job-1"] = _fast_fail_job(now)
    asyncio.run(rws._maybe_rearm_auto_record())
    assert kicked == ["job-1", "job-2"]
    assert rws._auto_rearm_state["consecutive_fast_failures"] == 1

    jobs["job-2"] = _fast_fail_job(now)
    asyncio.run(rws._maybe_rearm_auto_record())
    assert kicked == ["job-1", "job-2", "job-3"]
    assert rws._auto_rearm_state["consecutive_fast_failures"] == 2

    jobs["job-3"] = _fast_fail_job(now)
    asyncio.run(rws._maybe_rearm_auto_record())
    # Streak hit the limit (3) on this tick -> circuit opens, no 4th kick.
    assert kicked == ["job-1", "job-2", "job-3"]
    assert rws._auto_rearm_state["consecutive_fast_failures"] == 3
    assert rws._auto_rearm_state["circuit_open_until"] is not None

    # While the circuit is open, further ticks must not kick anything,
    # even though get_active_job() still reports idle.
    asyncio.run(rws._maybe_rearm_auto_record())
    asyncio.run(rws._maybe_rearm_auto_record())
    assert kicked == ["job-1", "job-2", "job-3"]


def test_real_recording_resets_the_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import time

    now = time.time()
    jobs: dict = {}
    kicked: list = []
    _patch_common(monkeypatch, jobs=jobs, kicked=kicked)

    asyncio.run(rws._maybe_rearm_auto_record())  # kicks job-1
    jobs["job-1"] = _fast_fail_job(now)
    asyncio.run(rws._maybe_rearm_auto_record())  # streak=1, kicks job-2
    assert rws._auto_rearm_state["consecutive_fast_failures"] == 1

    jobs["job-2"] = _real_job(now)
    asyncio.run(rws._maybe_rearm_auto_record())  # streak resets, kicks job-3
    assert rws._auto_rearm_state["consecutive_fast_failures"] == 0
    assert kicked == ["job-1", "job-2", "job-3"]


def test_circuit_recovers_after_cooldown_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    monkeypatch.setattr(rws, "_FAST_FAIL_STREAK_LIMIT", 1)
    monkeypatch.setattr(rws, "_FAST_FAIL_COOLDOWN_S", 0.0)

    now_holder = {"t": 1_000_000.0}
    monkeypatch.setattr(rws.time, "time", lambda: now_holder["t"])

    jobs: dict = {}
    kicked: list = []
    _patch_common(monkeypatch, jobs=jobs, kicked=kicked)

    asyncio.run(rws._maybe_rearm_auto_record())  # kicks job-1
    jobs["job-1"] = _fast_fail_job(now_holder["t"])
    asyncio.run(rws._maybe_rearm_auto_record())  # streak hits limit(1) -> circuit opens
    assert kicked == ["job-1"]
    assert rws._auto_rearm_state["circuit_open_until"] is not None

    # Cooldown is 0s and time hasn't moved, but circuit_open_until == now
    # means "now < circuit_open_until" is False, so the very next tick
    # should already be allowed to retry once.
    asyncio.run(rws._maybe_rearm_auto_record())
    assert kicked == ["job-1", "job-2"]
    assert rws._auto_rearm_state["circuit_open_until"] is None
    assert rws._auto_rearm_state["consecutive_fast_failures"] == 0
