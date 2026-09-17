"""Shared plumbing for the file-backed detached-worker job stores.

``index_prediction_run_jobs.py``, ``external_predictions_run_jobs.py``, and
``recording_jobs.py`` each independently implement: a ``job.json`` atomic
read/write (some under an ``fcntl`` cross-process lock, some not), a
``spawn_worker`` that ``Popen``s ``python -m src.trade.<name>_worker
<job_id>`` detached (``start_new_session=True``) and registers it with
:mod:`src.trade.detached_worker`, a best-effort SIGTERM ``_terminate_worker``,
and a ``job_id`` uuid4-hex validity check.

This module factors out exactly that — and only that — as small,
**stateless** functions parameterized by path/callable, not a stateful
class. That's deliberate: each of the three modules' own tests monkeypatch
a module-level ``_jobs_root()`` function (sometimes on a freshly-imported
module, sometimes via a subprocess that reassigns
``recording_jobs._jobs_root`` directly) and expect every disk operation to
immediately respect the new root. A shared object that captured a jobs-root
callable (or resolved path) at construction time would silently keep using
the pre-monkeypatch value — the classic "bound the wrong closure" bug. Every
function here instead takes the already-resolved ``Path`` for the call it's
making, computed by the caller from *its own* ``_jobs_root()`` each time.

Each of the three ``*_jobs.py`` modules keeps its own ``_jobs_root``,
``_job_dir``, ``_job_file``, ``_job_lock_file``, ``_serialize_job``,
in-memory dict, active-job index, and status-set/reconcile logic exactly as
before (those genuinely differ per system, or are load-bearing monkeypatch
points for existing tests) — they just delegate the repeated disk-I/O,
locking, and subprocess-spawn mechanics to the functions below.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl

    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows / minimal builds
    fcntl = None  # type: ignore[assignment]
    HAS_FCNTL = False

from src.trade import detached_worker

JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def job_id_valid(job_id: str | None) -> bool:
    return bool(job_id and JOB_ID_RE.fullmatch(job_id))


@contextmanager
def job_file_lock(lock_path: Path, *, valid: bool, exclusive: bool = True):
    """Cross-process lock for a job.json read-modify-write cycle.

    ``valid`` is the caller's own ``job_id_valid(job_id)`` result — an
    invalid job_id skips locking entirely (matches every existing
    ``_job_file_lock`` implementation, which never even computes a lock
    path for a bad id). No-ops as a lock (still runs the body) when
    ``fcntl`` isn't available, same as before.
    """
    if not valid:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not HAS_FCNTL:
        yield
        return
    with open(lock_path, "a+", encoding="utf-8") as lockf:
        flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lockf.fileno(), flag)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def write_job_json_unsafe(path: Path, payload: dict[str, Any]) -> None:
    """Atomic (tmp + rename) write. Caller must already hold the lock (or
    know no concurrent writer exists) — this performs no locking itself."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def read_job_json_unsafe(path: Path) -> dict[str, Any] | None:
    """Caller must already hold the lock (or accept a racy read)."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def mutate_job_on_disk(
    *,
    job_file: Path,
    lock_file: Path,
    valid: bool,
    job_id: str,
    serialize: Callable[[dict[str, Any]], dict[str, Any]],
    mutator: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    """Atomically read, mutate, and write job.json under an exclusive lock.

    ``mutator`` receives the in-memory dict (already ``dict(...)``-copied,
    with ``logs`` defaulted to ``[]``) and returns ``True`` to persist the
    mutation or ``False`` to skip the write (e.g. the job is already
    terminal). Returns the mutated job dict, or ``None`` if the job wasn't
    found on disk or the mutator declined.
    """
    with job_file_lock(lock_file, valid=valid, exclusive=True):
        job = read_job_json_unsafe(job_file)
        if job is None:
            return None
        job = dict(job)
        job.setdefault("logs", [])
        job.setdefault("job_id", job_id)
        if not mutator(job):
            return None
        write_job_json_unsafe(job_file, serialize(job))
        return job


def terminate_worker(pid: Any) -> None:
    """Best-effort SIGTERM for a detached worker subprocess by pid."""
    if pid is None:
        return
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return
    if not detached_worker.is_alive(pid_int):
        return
    if pid_int == os.getpid():
        return
    try:
        os.kill(pid_int, signal.SIGTERM)
    except OSError:
        pass


def spawn_worker_process(
    *,
    job_id: str,
    worker_module: str,
    agent_dir: Path,
    worker_log: Path,
    env_mutator: Callable[[dict[str, str]], None] | None = None,
) -> subprocess.Popen:
    """Launch ``python -m <worker_module> <job_id>`` detached (survives API
    hot-reload), register it with :mod:`detached_worker` for reap-aware
    liveness, and return the ``Popen`` handle so the caller can persist
    ``proc.pid`` onto its own job record.

    ``env_mutator`` lets a caller (recording_jobs) adjust the child's copied
    environment in place before spawn (e.g. resolving a cwd-relative env var
    against this process's cwd rather than the child's).
    """
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = worker_log.open("ab")
    env = os.environ.copy()
    if env_mutator is not None:
        env_mutator(env)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", worker_module, job_id],
            cwd=str(agent_dir),
            env=env,
            start_new_session=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_handle.close()
    detached_worker.register(proc)
    return proc


def agent_dir_from_here(module_file: str) -> Path:
    """``.../vibetrading/agent`` root, given a ``*_jobs.py`` module's
    ``__file__`` (which lives at ``.../agent/src/trade/<name>_jobs.py``).
    ``parents[2]`` from ``src/trade/<file>``: ``[0]`` is ``src/trade``,
    ``[1]`` is ``src`` (no ``src/`` subfolder of its own — the historical
    off-by-one bug), ``[2]`` is ``agent`` (the real root, which does)."""
    return Path(module_file).resolve().parents[2]
