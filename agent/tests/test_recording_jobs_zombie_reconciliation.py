"""Regression test for
``.claude/backlog/items/2026-09-11-recording-job-workers-die-before-run.md``:

``reconcile_zombie_job`` used to call ``fail_job(job_id, "worker process
exited unexpectedly")`` unconditionally on any dead worker, with no way to
tell a genuine crash from the process simply having been reaped elsewhere.
Now that ``spawn_worker`` registers its ``Popen`` handle with
``detached_worker``, a reconciled zombie should report the real exit reason
(exit code or killing signal) instead of the generic message — and, more
importantly, a dead-but-unreaped worker must actually be recognized as dead
at all: before the ``detached_worker``-backed reap, a real zombie process
still passes a bare ``os.kill(pid, 0)`` liveness check (the PID slot stays
allocated until reaped), which is exactly what left a job stuck at
``queued``/``running`` forever instead of failing cleanly.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from src.trade import detached_worker, recording_jobs


def _patch_jobs_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(recording_jobs, "_jobs_root", lambda: tmp_path)
    with recording_jobs._JOBS_LOCK:
        recording_jobs.RECORDING_JOBS.clear()
        recording_jobs._ACTIVE_JOB_ID = None


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "condition never became true within timeout"


def test_reconciles_a_real_dead_worker_and_reports_its_exit_reason(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_jobs_root(monkeypatch, tmp_path)

    job_id, _reused = recording_jobs.start_job(underlyings=["NIFTY"])

    # Stand in for spawn_worker's real recorder subprocess with a trivial
    # one so this test stays hermetic (no INDmoney/websocket dependency),
    # but goes through the exact same register-then-track path spawn_worker
    # uses in production.
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(7)"])
    detached_worker.register(proc)

    def _mark_running_with_pid(job: dict) -> bool:
        job["status"] = "running"
        job["worker_pid"] = proc.pid
        return True

    recording_jobs._mutate_job_on_disk(job_id, _mark_running_with_pid)
    with recording_jobs._JOBS_LOCK:
        recording_jobs.RECORDING_JOBS.pop(job_id, None)

    _wait_until(lambda: detached_worker.is_alive(proc.pid) is False)

    reconciled = recording_jobs.reconcile_zombie_job(job_id)
    assert reconciled is True

    job = recording_jobs._get_job_record(job_id)
    assert job is not None
    assert job["status"] == "error"
    assert job["error"] == "worker exited with code 7"
