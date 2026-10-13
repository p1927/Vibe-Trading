"""start_job over an active job whose worker died must replace it, not deadlock.

external_predictions / forecast_engine ``start_job`` called ``fail_job`` while holding a plain
``Lock`` that ``fail_job`` re-takes: the first start after a worker crash hung forever (and with
it every other call on the store). The watchdog must also cover every store (forecast_engine was
missing, so its crashed unpolled jobs read "running" forever).
"""

from __future__ import annotations

import os
import threading

import pytest

from src.trade import external_predictions_run_jobs as ext
from src.trade import forecast_engine_run_jobs as fe
from src.trade import job_watchdog

DEAD_PID = 2_999_999


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    monkeypatch.setattr(ext, "_jobs_root", lambda: tmp_path / "ext")
    monkeypatch.setattr(fe, "_jobs_root", lambda: tmp_path / "fe")
    monkeypatch.setattr("trade_integrations.forecast_engine.artifact.make_run_id", lambda **_: "run")
    for store, scopes in (
        (ext.EXTERNAL_PREDICTIONS_RUN_JOBS, ext._ACTIVE_BY_SCOPE),
        (fe.FORECAST_ENGINE_RUN_JOBS, fe._ACTIVE_BY_SCOPE),
    ):
        store.clear()
        scopes.clear()


def _start(mod):
    if mod is ext:
        return mod.start_job(ticker="NIFTY", horizon_days=14)
    return mod.start_job(ticker="NIFTY", recipe="r", start="2026-01-01", end="2026-02-01", horizon=5)


def _within(seconds, fn):
    out = []
    t = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    t.start()
    t.join(seconds)
    return out


@pytest.mark.parametrize("mod", [ext, fe], ids=["external_predictions", "forecast_engine"])
@pytest.mark.parametrize("status", ["queued", "running"])
def test_start_job_replaces_job_whose_worker_died(mod, status):
    first = _start(mod)[0]
    job = mod._get_job_record(first)
    job["status"], job["worker_pid"] = status, DEAD_PID
    mod._write_job_to_disk(job)

    out = _within(5, lambda: _start(mod))

    assert out, "start_job deadlocked on a dead worker"
    assert out[0][0] != first
    assert mod.get_job(first)["status"] == "error"


@pytest.mark.parametrize("mod", [ext, fe], ids=["external_predictions", "forecast_engine"])
def test_concurrent_starts_over_dead_worker_all_return(mod):
    first = _start(mod)[0]
    job = mod._get_job_record(first)
    job["worker_pid"] = DEAD_PID
    mod._write_job_to_disk(job)

    results = []
    threads = [threading.Thread(target=lambda: results.append(_start(mod)[0]), daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert len(results) == 4
    assert first not in results


def test_start_job_still_reuses_a_live_worker():
    first = _start(ext)[0]
    job = ext._get_job_record(first)
    job["worker_pid"] = os.getpid()
    ext._write_job_to_disk(job)

    assert _start(ext) == (first, True)


def test_watchdog_covers_every_job_store():
    names = {m.__name__.rsplit(".", 1)[-1] for m in job_watchdog._job_stores()}
    assert names == {
        "index_prediction_run_jobs",
        "recording_jobs",
        "external_predictions_run_jobs",
        "forecast_engine_run_jobs",
    }
