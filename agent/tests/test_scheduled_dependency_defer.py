"""A job that needs the stock_simulator service is DEFERRED (stays pending, re-offered each tick) while it does
not answer, instead of dispatching and failing (R6 live: nifty-index-prediction-post-close failed when the
simulator was unreachable and a weekly job then waits a week)."""

from __future__ import annotations

import asyncio

from src.scheduled_research import job_tier_policy as policy
from src.scheduled_research.dispatch_admission import DispatchPool
from src.scheduled_research.executor import next_due
from src.scheduled_research.models import JobStatus, ScheduledResearchJob


def _job(job_id: str, job_type: str) -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id, prompt="p", schedule="3600000", next_run_at=10, status=JobStatus.PENDING,
        created_at=0, config={"job_type": job_type},
    )


def test_dependency_reason_only_for_simulator_jobs() -> None:
    down = lambda: False  # noqa: E731
    assert policy.dependency_defer_reason("index_prediction_post_close", probe=down) == "dependency:stock_simulator_unreachable"
    assert policy.dependency_defer_reason("index_prediction_post_close", probe=lambda: True) is None
    assert policy.dependency_defer_reason("hub_capture_intraday", probe=down) is None
    assert policy.dependency_defer_reason("", probe=down) is None


def test_post_close_is_deferred_while_the_simulator_is_down_then_runs(monkeypatch) -> None:
    state = {"up": False}
    monkeypatch.setattr(policy, "host_pressure_defer_reason", lambda t, probe=None: None)
    monkeypatch.setattr(policy, "_simulator_reachable", lambda: state["up"])

    async def scenario() -> None:
        pool = DispatchPool(global_cap=lambda: 4, stopping=lambda: False, next_due_fn=next_due,
                            defer_reason=policy.defer_reason)
        ran: list[str] = []
        job = _job("post-close", "index_prediction_post_close")

        async def run(j: ScheduledResearchJob, _now: int) -> None:
            ran.append(j.id)

        await pool.submit([job], 1_000, run=run, population=[job], wait=False)
        await asyncio.sleep(0.02)
        assert ran == [] and pool.deferred == {"post-close": "dependency:stock_simulator_unreachable"}
        state["up"] = True
        await pool.submit([job], 1_000, run=run, population=[job], wait=False)
        await asyncio.sleep(0.02)
        assert ran == ["post-close"]

    asyncio.run(scenario())


def test_probe_is_cached_and_unreachable_means_false(monkeypatch) -> None:
    calls = []

    class _Client:
        def health_check(self):
            calls.append(1)
            from trade_integrations.stock_simulator.client import StockSimulatorClientError

            raise StockSimulatorClientError("down")

    monkeypatch.setattr("trade_integrations.stock_simulator.client.StockSimulatorClient", _Client)
    monkeypatch.setattr(policy, "_simulator_probe_cache", None)
    assert policy._simulator_reachable() is False
    assert policy._simulator_reachable() is False
    assert len(calls) == 1
