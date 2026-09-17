"""Regression test for
``.claude/backlog/items/2026-09-11-recording-job-workers-die-before-run.md``:

``spawn_worker()`` in ``recording_jobs.py`` (and the identical pattern in
``index_prediction_run_jobs.py``/``external_predictions_run_jobs.py``) never
retained or reaped its ``Popen`` handle, so a dead child sat as a
``<defunct>`` zombie for as long as the parent process stayed up. A zombie
still passes a bare ``os.kill(pid, 0)`` liveness check (the PID slot is still
allocated until reaped), so the job stores' own zombie reconciler never
fired and the job got stuck instead of failing cleanly.

``detached_worker`` fixes this by giving every spawner a place to register
its ``Popen`` handle so liveness checks can reap opportunistically and
report the real exit reason.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from src.trade import detached_worker


def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "condition never became true within timeout"


def test_is_alive_reaps_and_reports_false_after_normal_exit() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(3)"])
    detached_worker.register(proc)

    assert detached_worker.is_alive(proc.pid) is True

    _wait_until(lambda: detached_worker.is_alive(proc.pid) is False)

    # Reaped, not a zombie: a second waitpid on the same pid must now raise
    # ECHILD (already reaped) rather than returning a zombie's status.
    try:
        os.waitpid(proc.pid, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise AssertionError("expected the child to already be reaped")

    assert detached_worker.describe_exit(proc.pid) == "worker exited with code 3"


def test_describe_exit_reports_signal_name_for_killed_child() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    detached_worker.register(proc)

    assert detached_worker.is_alive(proc.pid) is True
    os.kill(proc.pid, signal.SIGKILL)

    _wait_until(lambda: detached_worker.is_alive(proc.pid) is False)

    assert detached_worker.describe_exit(proc.pid) == "worker killed by signal 9 (SIGKILL)"


def test_forget_drops_the_handle() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    detached_worker.register(proc)
    _wait_until(lambda: detached_worker.is_alive(proc.pid) is False)

    detached_worker.forget(proc.pid)

    # No handle left to explain the exit -- caller falls back to its own
    # generic message.
    assert detached_worker.describe_exit(proc.pid) is None


def test_is_alive_false_for_none_or_nonpositive_pid() -> None:
    assert detached_worker.is_alive(None) is False
    assert detached_worker.is_alive(0) is False
    assert detached_worker.is_alive(-1) is False
