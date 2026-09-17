"""Shared liveness/reaping for detached subprocess workers.

``index_prediction_run_jobs.py``, ``recording_jobs.py``, and
``external_predictions_run_jobs.py`` each spawn their worker via
``subprocess.Popen(..., start_new_session=True)`` (survives API hot-reload) and
then track it purely by PID. Nothing ever called ``Popen.poll()``/``.wait()``
again, so a dead child sat as a ``<defunct>`` zombie for as long as this
process stayed up — and a bare ``os.kill(pid, 0)`` liveness check succeeds
against a zombie (the PID slot is still allocated until reaped), so the
job-store's own zombie reconciler never fired. This module gives each spawner
a place to register its ``Popen`` handle so liveness checks can reap
opportunistically and report *why* a worker died instead of a generic message.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading

_HANDLES: dict[int, subprocess.Popen] = {}
_LOCK = threading.Lock()


def register(proc: subprocess.Popen) -> None:
    """Record a just-spawned worker's ``Popen`` handle so it can be reaped."""
    with _LOCK:
        _HANDLES[proc.pid] = proc


def forget(pid: int | None) -> None:
    """Drop a handle once its job record has been pruned, so a long-running
    process doesn't accumulate one ``Popen`` object per job forever."""
    if pid is None:
        return
    with _LOCK:
        _HANDLES.pop(int(pid), None)


def is_alive(pid: int | None) -> bool:
    """Reap-aware liveness check: a zombie we hold the handle for reports
    dead, not alive."""
    if pid is None or pid <= 0:
        return False
    with _LOCK:
        proc = _HANDLES.get(pid)
    if proc is not None and proc.poll() is not None:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def describe_exit(pid: int | None) -> str | None:
    """Human-readable reason ``pid`` is no longer running, if this process
    spawned and reaped it. ``None`` if we hold no handle for it (fall back to
    the caller's own generic message) — e.g. after an API restart the
    orphaned child is reparented to init, which reaps it, not us."""
    if pid is None:
        return None
    with _LOCK:
        proc = _HANDLES.get(pid)
    if proc is None or proc.returncode is None:
        return None
    code = proc.returncode
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = str(-code)
        return f"worker killed by signal {-code} ({name})"
    return f"worker exited with code {code}"
