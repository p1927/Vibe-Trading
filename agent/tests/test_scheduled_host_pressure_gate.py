"""Host-pressure gate at scheduler dispatch (2026-10-13-release-host-swap-and-disk-exhaustion-stall-
scheduler-runs): heavy jobs are DEFERRED (stay pending, reason ``host_pressure:<what>``) while the host is
starved; light jobs keep dispatching; a deferral is never a failure and clears when the host recovers.
The probe is injected; thresholds are covered in Trade's tests/test_host_pressure.py."""

from __future__ import annotations

import asyncio
import time
from functools import partial
from pathlib import Path

import pytest
from src.scheduled_research.dispatch_admission import DispatchPool
from src.scheduled_research.executor import ScheduledResearchExecutor, next_due
from src.scheduled_research.job_tier_policy import HEAVY_JOB_TYPES, COLLECTION_JOB_TYPES, host_pressure_defer_reason
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore
from trade_integrations.host_pressure import HostPressure

STARVED = HostPressure(True, ("disk 5.1 GiB free < 8", "swap 12.6/13.3 GiB (95% > 90%)"), {})
HEALTHY = HostPressure(False)


async def _noop_dispatch(job: ScheduledResearchJob) -> None:
    return None


def _job(job_id: str, job_type: str) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id, prompt="p", schedule="3600000", next_run_at=10, status=JobStatus.PENDING,
        created_at=0, config={"job_type": job_type},
    )


def _pool(state: dict) -> DispatchPool:
    return DispatchPool(
        global_cap=lambda: 4, stopping=lambda: False, next_due_fn=next_due,
        defer_reason=lambda t: host_pressure_defer_reason(t, probe=lambda: state["hp"]),
    )


async def _submit(pool: DispatchPool, jobs: list[ScheduledResearchJob], ran: list[str]) -> None:
    async def run(job: ScheduledResearchJob, _now: int) -> None:
        ran.append(job.id)

    await pool.submit(jobs, 1_000, run=run, population=jobs, wait=False)
    await asyncio.sleep(0.02)


def test_heavy_deferred_light_runs_then_heavy_runs_on_recovery() -> None:
    async def scenario() -> None:
        state = {"hp": STARVED}
        pool, ran = _pool(state), []
        heavy, light = _job("heavy", "index_research"), _job("light", "hub_capture_intraday")
        await _submit(pool, [heavy, light], ran)
        assert ran == ["light"]
        assert pool.deferred == {"heavy": "host_pressure:disk+swap"}
        assert pool.waiting_ids == ["heavy"]  # still pending in the pool, not dropped
        await _submit(pool, [heavy], ran)  # next tick, still starved
        assert ran == ["light"]
        state["hp"] = HEALTHY
        await _submit(pool, [heavy], ran)
        assert ran == ["light", "heavy"]
        assert pool.deferred == {}

    asyncio.run(scenario())


def test_deferral_never_touches_the_stored_job(tmp_path: Path) -> None:
    """No failure counted, no pause, no status change: deferral lives only in the pool + status."""
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    job = _job("heavy", "forecast_engine_refresh")
    store.upsert(job)

    async def scenario() -> None:
        await _submit(_pool({"hp": STARVED}), [job], [])

    asyncio.run(scenario())
    stored = store.get("heavy")
    assert (stored.status, stored.paused, stored.consecutive_failures, stored.last_error) == (
        JobStatus.PENDING, False, 0, None,
    )


@pytest.mark.parametrize("job_type", ["autonomous_agent_watch", "recording_wake", "hub_capture_intraday", "", "unknown_type"])
def test_light_and_operational_types_are_never_deferred(job_type: str) -> None:
    assert host_pressure_defer_reason(job_type, probe=lambda: STARVED) is None


def test_heavy_set_is_a_subset_of_collection_jobs_and_names_the_incident_types() -> None:
    assert HEAVY_JOB_TYPES <= COLLECTION_JOB_TYPES
    assert {"forecast_engine_refresh", "index_research"} <= HEAVY_JOB_TYPES


def test_every_heavy_type_is_deferred() -> None:
    for job_type in HEAVY_JOB_TYPES:
        assert host_pressure_defer_reason(job_type, probe=lambda: STARVED), job_type


def test_pool_without_gate_is_unchanged() -> None:
    async def scenario() -> None:
        pool = DispatchPool(global_cap=lambda: 2, stopping=lambda: False, next_due_fn=next_due)
        ran: list[str] = []
        await _submit(pool, [_job("heavy", "index_research")], ran)
        assert ran == ["heavy"] and pool.deferred == {}

    asyncio.run(scenario())


def test_status_and_run_record_surface_the_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`liveness()` (the body of /scheduled-runs/scheduler/status) carries the verdict, and a deferred job
    is not reported as an overdue stall; the run record's dispatch_blocked_reason names the reason."""
    from trade_integrations import host_pressure

    from src.api.scheduled_routes import _dispatch_blocked_reason

    monkeypatch.setenv("STACK_PROFILE", "release")
    monkeypatch.setattr(host_pressure, "measure", lambda: STARVED)
    monkeypatch.setattr(host_pressure, "_cache", None)
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    heavy = _job("heavy", "index_research")
    heavy.next_run_at = int(time.time() * 1000) - 3_600_000
    store.upsert(heavy)
    executor = ScheduledResearchExecutor(store=store, dispatch=_noop_dispatch)

    async def scenario() -> dict:
        await executor._dispatch_pool.submit(
            [heavy], int(time.time() * 1000), run=lambda j, n: asyncio.sleep(0), population=[heavy], wait=False
        )
        await asyncio.sleep(0.02)
        return executor.liveness()

    status = asyncio.run(scenario())
    hp = status["host_pressure"]
    assert hp["deferred"] is True and hp["reason"] == "host_pressure:disk+swap"
    assert hp["deferred_jobs"] == {"heavy": "host_pressure:disk+swap"}
    assert status["max_overdue_seconds"] == 0  # a deliberate deferral is not a stalled scheduler
    assert _dispatch_blocked_reason(heavy) == "host_pressure:disk+swap"
    assert _dispatch_blocked_reason(_job("light", "hub_capture_intraday")) is None


def test_healthy_host_reports_not_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trade_integrations import host_pressure

    monkeypatch.setattr(host_pressure, "measure", lambda: HEALTHY)
    monkeypatch.setattr(host_pressure, "_cache", None)
    executor = ScheduledResearchExecutor(
        store=ScheduledResearchJobStore(path=tmp_path / "jobs.json"), dispatch=_noop_dispatch
    )
    hp = executor.liveness()["host_pressure"]
    assert hp["deferred"] is False and hp["reason"] == "" and hp["deferred_jobs"] == {}


def test_wait_true_caller_returns_while_a_job_is_deferred() -> None:
    async def scenario() -> None:
        pool = _pool({"hp": STARVED})
        job = _job("heavy", "index_research")

        async def run(j: ScheduledResearchJob, _n: int) -> None:
            raise AssertionError("must not run")

        await asyncio.wait_for(pool.submit([job], 1_000, run=run, population=[job], wait=True), timeout=2)
        assert pool.deferred and pool.waiting_ids == ["heavy"]

    asyncio.run(scenario())
