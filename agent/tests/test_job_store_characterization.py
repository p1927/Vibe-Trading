"""Behaviour pins for the file-backed job stores (external_predictions, forecast_engine), written
before they were unified onto ``job_store.JobStore``: they must hold for both and survive the refactor."""

from __future__ import annotations

import json
import threading

import pytest

from src.trade import external_predictions_run_jobs as ext
from src.trade import forecast_engine_run_jobs as fe

DEAD_PID = 2_999_999
MODS = [ext, fe]
IDS = ["external_predictions", "forecast_engine"]


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


def _active(mod):
    if mod is ext:
        return mod.get_active_job("NIFTY", horizon_days=14)
    return mod.get_active_job(ticker="NIFTY", recipe="r")


def _cache(mod):
    return ext.EXTERNAL_PREDICTIONS_RUN_JOBS if mod is ext else fe.FORECAST_ENGINE_RUN_JOBS


def _complete(mod, job_id):
    if mod is ext:
        mod.complete_job(job_id, ticker="NIFTY", snapshot={"ok": 1})
    else:
        mod.complete_job(job_id, result={"ok": 1})


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_lifecycle_and_restart_persistence(mod):
    job_id = _start(mod)[0]
    assert mod.get_job(job_id)["status"] == "queued"
    mod.mark_running(job_id)
    assert mod.get_job(job_id)["status"] == "running"
    _complete(mod, job_id)
    _cache(mod).clear()  # API hot-reload: memory gone, disk is the truth
    assert mod.get_job(job_id)["status"] == "done"
    assert _active(mod) is None


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_fail_job_records_error_and_frees_scope(mod):
    first = _start(mod)[0]
    mod.fail_job(first, "boom")
    snap = mod.get_job(first)
    assert (snap["status"], snap["error"]) == ("error", "boom")
    assert _start(mod)[0] != first


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_corrupt_job_file_raises_on_read_but_does_not_stop_hydration(mod):
    """An unreadable job file is not an absent job (4528e8e8, D479: reading it as absent hid a live recording from
    the one-session scan); the read raises, while the boot-time scan of the whole directory skips it."""
    job_id = _start(mod)[0]
    mod._job_file(job_id).write_text("{not json", encoding="utf-8")
    _cache(mod).clear()
    with pytest.raises(ValueError):
        mod.get_job(job_id)
    mod.hydrate_jobs_from_disk()  # must not raise on the corrupt file


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_one_corrupt_job_file_does_not_stop_the_others_being_hydrated(mod):
    bad = _start(mod)[0]
    # a second, healthy job on another scope
    if mod is ext:
        good = mod.start_job(ticker="BANKNIFTY", horizon_days=14)[0]
    else:
        good = mod.start_job(ticker="BANKNIFTY", recipe="r2", start="2026-01-01", end="2026-02-01", horizon=5)[0]
    mod._job_file(bad).write_text("{not json", encoding="utf-8")
    _cache(mod).clear()
    mod._ACTIVE_BY_SCOPE.clear()
    mod.hydrate_jobs_from_disk()
    assert good in _cache(mod)


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_hydrate_restores_active_job_so_start_reuses_it(mod):
    job_id = _start(mod)[0]
    _cache(mod).clear()
    mod._ACTIVE_BY_SCOPE.clear()
    mod.hydrate_jobs_from_disk()
    assert _start(mod)[0] == job_id


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_concurrent_starts_same_scope_yield_one_job(mod):
    out = []
    threads = [threading.Thread(target=lambda: out.append(_start(mod)[0]), daemon=True) for _ in range(6)]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert len(out) == 6 and len(set(out)) == 1


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_reconcile_all_terminalizes_dead_worker_without_polling(mod):
    job_id = _start(mod)[0]
    job = mod._get_job_record(job_id)
    job["status"], job["worker_pid"] = "running", DEAD_PID
    mod._write_job_to_disk(job)
    assert mod.reconcile_all_active_jobs() == 1
    assert mod.get_job(job_id)["status"] == "error"


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_job_file_is_plain_json_with_core_fields(mod):
    job_id = _start(mod)[0]
    payload = json.loads(mod._job_file(job_id).read_text(encoding="utf-8"))
    assert {"job_id", "status", "created_at", "logs", "error", "worker_pid", "_finished_at"} <= set(payload)


# --- behaviours of the unified JobStore (disk-first) -------------------------------------------


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_late_complete_does_not_resurrect_a_failed_job(mod):
    job_id = _start(mod)[0]
    mod.fail_job(job_id, "watchdog said so")
    _complete(mod, job_id)
    assert mod.get_job(job_id)["status"] == "error"


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_worker_pid_write_cannot_clobber_a_finished_job(mod):
    """spawn_worker records the pid with a disk read-modify-write, not a stale full-record write."""
    job_id = _start(mod)[0]
    mod._get_job_record(job_id)  # the API process now holds the queued record in memory
    _complete(mod, job_id)  # the worker finishes before the pid lands
    mod._STORE.mutate(job_id, lambda job: job.update(worker_pid=1234))
    assert mod.get_job(job_id)["status"] == "done"


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_job_file_carries_schema_version_and_old_files_still_load(mod):
    job_id = _start(mod)[0]
    path = mod._job_file(job_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    del payload["schema_version"]  # a file written before the version existed
    path.write_text(json.dumps(payload), encoding="utf-8")
    _cache(mod).clear()
    assert mod.get_job(job_id)["status"] == "queued"


@pytest.mark.parametrize("mod", MODS, ids=IDS)
def test_start_racing_reconcile_on_dead_worker_does_not_deadlock(mod):
    first = _start(mod)[0]
    job = mod._get_job_record(first)
    job["worker_pid"] = DEAD_PID
    mod._write_job_to_disk(job)
    done = []
    workers = [lambda: _start(mod), lambda: mod.reconcile_zombie_job(first), lambda: _start(mod), lambda: mod.reconcile_job(first)]
    threads = [threading.Thread(target=lambda f=f: done.append(f()), daemon=True) for f in workers]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert len(done) == len(workers), "start_job / reconcile deadlocked on the same job"


# --- index_prediction: the same JobStore with its own budgets / terminal statuses -------------


@pytest.fixture
def idx(tmp_path, monkeypatch):
    from src.trade import index_prediction_run_jobs as mod

    monkeypatch.setattr(mod, "_jobs_root", lambda: tmp_path / "idx")
    mod.INDEX_PREDICTION_RUN_JOBS.clear()
    mod._ACTIVE_BY_TICKER.clear()
    return mod


def _idx_start(mod, refresh=False):
    return mod.start_job(ticker="NIFTY", horizon_days=14, refresh_constituents=refresh)


def test_index_warnings_survive_a_reload_and_status_is_done_with_warnings(idx):
    job_id = _idx_start(idx)[0]
    idx.complete_job(job_id, ticker="NIFTY", artifact={"spot": 1.0}, warnings=["w1"])
    idx.INDEX_PREDICTION_RUN_JOBS.clear()
    snap = idx.get_job(job_id)
    assert (snap["status"], snap["warnings"]) == ("done_with_warnings", ["w1"])
    assert _idx_start(idx)[0] != job_id  # terminal job frees the ticker


def test_index_running_job_with_no_logs_goes_stale_but_one_with_logs_does_not(idx, monkeypatch):
    job_id = _idx_start(idx)[0]
    idx.mark_running(job_id)
    job = idx._get_job_record(job_id)
    job["worker_pid"] = __import__("os").getpid()
    idx._write_job_to_disk(job)
    monkeypatch.setattr(idx, "_QUEUED_NO_PID_SECONDS", -1)  # any age exceeds the no-log grace
    assert idx.reconcile_stale_job(job_id) is True
    assert "no logs" in idx.get_job(job_id)["error"]
    job2 = _idx_start(idx)[0]
    idx.append_log(job2, {"stage": "x", "at": "2026-01-01T00:00:00+00:00"})
    job = idx._get_job_record(job2)
    job["worker_pid"] = __import__("os").getpid()
    idx._write_job_to_disk(job)
    assert idx.reconcile_stale_job(job2) is False


def test_index_refresh_jobs_get_the_refresh_wall_clock_budget(idx, monkeypatch):
    monkeypatch.setattr(idx, "_WALL_CLOCK_SECONDS", -1)
    monkeypatch.setattr(idx, "_REFRESH_WALL_CLOCK_SECONDS", 10**9)
    plain = _idx_start(idx)[0]
    idx.append_log(plain, {"stage": "x"})
    refresh_job = idx.start_job(ticker="BANKNIFTY", horizon_days=14, refresh_constituents=True)[0]
    idx.append_log(refresh_job, {"stage": "x"})
    for jid in (plain, refresh_job):
        j = idx._get_job_record(jid)
        j["worker_pid"] = __import__("os").getpid()
        idx._write_job_to_disk(j)
    assert idx.reconcile_stale_job(plain) is True
    assert idx.reconcile_stale_job(refresh_job) is False
