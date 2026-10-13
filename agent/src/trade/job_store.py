"""ONE file-backed detached-worker job store (D306).

``external_predictions_run_jobs`` and ``forecast_engine_run_jobs`` used to carry two near-identical
copies of this lifecycle (in-memory dict, active-by-scope index, disk merge, reconcile, prune,
start/fail/complete); each module now keeps only its scope key, its snapshot/serialize shape and its
worker body, and delegates the rest to a :class:`JobStore`.

Design (the disk-first one ``index_prediction``/``recording`` already used):

* The disk file is the truth. Every mutation is read-modify-write under the cross-process ``flock``
  (:func:`job_runner.job_file_lock`), so a worker subprocess and the API process cannot overwrite each
  other; the in-memory dict is only a cache refreshed from the mutated record.
* A mutation of ``append_log``/``complete``/``fail`` declines (writes nothing) on an already-terminal
  job, so a late worker cannot resurrect a job the watchdog already failed.
* Lock order is ``start_lock -> file flock -> jobs_lock`` and the in-memory ``jobs_lock`` is never held
  while waiting for a file lock (a flock is per open file, so waiting for one while holding the lock
  another thread needs to release it deadlocks: the P1 fixed in ``test_job_store_dead_worker_restart``).

Per-module hooks are passed as callables so each module's tests can keep monkeypatching its own
``_jobs_root`` / ``_WALL_CLOCK_SECONDS`` after import (nothing is captured at construction time).
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.trade import detached_worker, job_runner

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset({"queued", "running"})
_JOB_TTL_SECONDS = 60 * 60
_RUNNING_NO_PID_SECONDS = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_timestamp(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def job_age_seconds(job: dict[str, Any]) -> float:
    created = _parse_iso_timestamp(str(job.get("created_at") or ""))
    if created is None:
        return 0.0
    return max(0.0, datetime.now(timezone.utc).timestamp() - created)


def worker_alive(
    job: dict[str, Any] | None,
    *,
    pidless_alive: frozenset[str] = frozenset({"queued"}),
    active_statuses: frozenset[str] = ACTIVE_STATUSES,
) -> bool:
    """A job with no pid is alive only in a ``pidless_alive`` status (it legitimately has no worker
    yet: ``queued``, or recording's ``waiting_for_open``, which a cron wake spawns later)."""
    if job is None:
        return False
    status = str(job.get("status") or "")
    pid = job.get("worker_pid")
    if status in pidless_alive and pid is None:
        return True
    if pid is None:
        return status not in active_statuses
    return detached_worker.is_alive(int(pid))


class JobStore:
    def __init__(
        self,
        *,
        jobs_root: Callable[[], Path],
        serialize: Callable[[dict[str, Any]], dict[str, Any]],
        scope_for: Callable[[dict[str, Any]], str],
        wall_clock_seconds: Callable[[dict[str, Any]], float],
        queued_no_pid_seconds: Callable[[], float],
        result_key: str,
        jobs_ref: Callable[[], dict[str, dict[str, Any]]],
        active_ref: Callable[[], dict[str, str]],
        terminal_statuses: frozenset[str] = frozenset({"done", "error"}),
        running_no_pid_seconds: Callable[[], float] = lambda: _RUNNING_NO_PID_SECONDS,
        stale_reason: Callable[[dict[str, Any], float], str | None] | None = None,
        normalize: Callable[[dict[str, Any]], None] | None = None,
        active_statuses: frozenset[str] = ACTIVE_STATUSES,
        startable_statuses: frozenset[str] = frozenset({"queued"}),
        pidless_alive_statuses: frozenset[str] = frozenset({"queued"}),
        run_age_seconds: Callable[[dict[str, Any]], float] = job_age_seconds,
        on_running: Callable[[dict[str, Any]], None] | None = None,
        creation_lock: Callable[[], Any] | None = None,
    ) -> None:
        self._jobs_root = jobs_root
        self._serialize = serialize
        self._scope_for = scope_for
        self._wall_clock = wall_clock_seconds
        self._queued_no_pid = queued_no_pid_seconds
        self._result_key = result_key
        self._terminal = terminal_statuses
        self._running_no_pid = running_no_pid_seconds
        self._stale_reason = stale_reason
        self._normalize = normalize
        self._active_statuses = active_statuses
        self._startable = startable_statuses
        self._pidless_alive = pidless_alive_statuses
        self._run_age = run_age_seconds
        self._on_running = on_running
        # A cross-process lock around claim(); when set, claim() also scans the jobs dir for a live
        # job of the same scope that another process created (recording's one-session rule).
        self._creation_lock = creation_lock
        self._jobs_ref = jobs_ref
        self._active_ref = active_ref
        self.jobs_lock = threading.RLock()
        self._start_lock = threading.RLock()

    # The module owns its two dicts and passes them as getters (tests replace the module attributes).
    @property
    def jobs(self) -> dict[str, dict[str, Any]]:
        return self._jobs_ref()

    @property
    def active(self) -> dict[str, str]:
        return self._active_ref()

    def alive(self, job: dict[str, Any] | None) -> bool:
        return worker_alive(job, pidless_alive=self._pidless_alive, active_statuses=self._active_statuses)

    def _is_active(self, job: dict[str, Any] | None) -> bool:
        return job is not None and job.get("status") in self._active_statuses

    def _promote(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        if self._on_running is not None:
            self._on_running(job)

    # -- paths / disk ---------------------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self._jobs_root() / job_id

    def job_file(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "job.json"

    def _lock(self, job_id: str, *, exclusive: bool = True):
        return job_runner.job_file_lock(
            self.job_dir(job_id) / ".job.lock", valid=job_runner.job_id_valid(job_id), exclusive=exclusive
        )

    def _read_unsafe(self, job_id: str) -> dict[str, Any] | None:
        payload = job_runner.read_job_json_unsafe(self.job_file(job_id))
        if payload is None or not job_runner.job_id_valid(str(payload.get("job_id") or job_id)):
            return None
        payload.setdefault("job_id", job_id)
        payload.setdefault("logs", [])
        if self._normalize is not None:
            self._normalize(payload)
        return payload

    def _write_unsafe(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "")
        if job_runner.job_id_valid(job_id):
            job_runner.write_job_json_unsafe(self.job_file(job_id), self._serialize(job))

    def write(self, job: dict[str, Any]) -> None:
        with self._lock(str(job.get("job_id") or "")):
            self._write_unsafe(job)

    def read(self, job_id: str) -> dict[str, Any] | None:
        with self._lock(job_id, exclusive=False):
            return self._read_unsafe(job_id)

    def _index(self, job: dict[str, Any]) -> None:
        """Keep the active-by-scope index consistent with ``job``'s status (caller holds jobs_lock)."""
        scope = self._scope_for(job)
        job_id = job["job_id"]
        if self._is_active(job):
            self.active[scope] = job_id
        elif self.active.get(scope) == job_id:
            self.active.pop(scope, None)

    def mutate(self, job_id: str, mutator: Callable[[dict[str, Any]], Any]) -> dict[str, Any] | None:
        """Read-modify-write one job under the file lock; ``mutator`` returning ``False`` declines the
        write (``None``/``True`` persist). Returns the mutated record, or ``None`` if absent/declined."""
        with self._lock(job_id):
            job = self._read_unsafe(job_id)
            if job is None:
                with self.jobs_lock:
                    cached = self.jobs.get(job_id)
                if cached is None:
                    return None
                job = dict(cached)
            if mutator(job) is False:
                return None
            self._write_unsafe(job)
            with self.jobs_lock:
                self.jobs[job_id] = job
                self._index(job)
            return job

    # -- read side ------------------------------------------------------------------------------
    def _merge(self, job_id: str, memory: dict[str, Any] | None) -> dict[str, Any] | None:
        disk = self.read(job_id)
        if disk is None:
            return memory
        if memory is None:
            return disk
        mem_logs, disk_logs = len(memory.get("logs") or []), len(disk.get("logs") or [])
        mem_status, disk_status = str(memory.get("status") or ""), str(disk.get("status") or "")
        if disk_logs > mem_logs or disk_status != mem_status:
            return dict(disk)
        key = self._result_key
        if disk.get(key) is not None and memory.get(key) is None:
            merged = dict(memory)
            merged.update(
                {
                    "status": disk_status or mem_status,
                    key: disk.get(key),
                    "error": disk.get("error"),
                    "worker_pid": disk.get("worker_pid", memory.get("worker_pid")),
                    "_finished_at": disk.get("_finished_at", memory.get("_finished_at")),
                }
            )
            if disk_logs >= mem_logs:
                merged["logs"] = list(disk.get("logs") or [])
            return merged
        return memory

    def get_record(self, job_id: str) -> dict[str, Any] | None:
        with self.jobs_lock:
            memory = self.jobs.get(job_id)
        merged = self._merge(job_id, memory)
        if merged is not None:
            with self.jobs_lock:
                self.jobs[job_id] = merged
                self._index(merged)
        return merged

    def hydrate(self) -> None:
        root = self._jobs_root()
        if not root.is_dir():
            return
        for path in root.iterdir():
            if not path.is_dir() or not job_runner.job_id_valid(path.name):
                continue
            job = self.read(path.name)
            if job is None:
                continue
            with self.jobs_lock:
                self.jobs[path.name] = job
                if self._is_active(job):
                    self.active[self._scope_for(job)] = path.name

    def prune(self) -> None:
        cutoff = time.time() - _JOB_TTL_SECONDS
        with self.jobs_lock:
            stale = [
                job_id
                for job_id, job in self.jobs.items()
                if job.get("status") in self._terminal and (job.get("_finished_at") or 0) < cutoff
            ]
            for job_id in stale:
                job = self.jobs.pop(job_id)
                detached_worker.forget(job.get("worker_pid"))
                scope = self._scope_for(job)
                if self.active.get(scope) == job_id:
                    self.active.pop(scope, None)

    def get_active(self, scope: str) -> dict[str, Any] | None:
        with self.jobs_lock:
            job_id = self.active.get(scope)
        if not job_id:
            return None
        job = self.get_record(job_id)
        if not self._is_active(job):
            with self.jobs_lock:
                if self.active.get(scope) == job_id:
                    self.active.pop(scope, None)
            return None
        if not self.alive(job):
            self.reconcile_zombie(job_id)
            return None
        return job

    # -- state machine --------------------------------------------------------------------------
    def mark_running(self, job_id: str) -> None:
        def _apply(job: dict[str, Any]) -> bool:
            if job.get("status") not in self._startable:
                return False
            self._promote(job)
            return True

        self.mutate(job_id, _apply)

    def append(self, job_id: str, entry: dict[str, Any], **extra: Any) -> None:
        """Append one log entry (promoting queued->running) plus optional extra top-level fields."""

        def _apply(job: dict[str, Any]) -> bool:
            if not self._is_active(job):
                return False
            if job.get("status") == "queued":  # a waiting_for_open job has no worker: only mark_running wakes it
                self._promote(job)
            job.setdefault("logs", []).append(entry)
            job.update(extra)
            return True

        self.mutate(job_id, _apply)

    def complete(self, job_id: str, *, status: str = "done", **fields: Any) -> None:
        def _apply(job: dict[str, Any]) -> bool:
            if not self._is_active(job):
                logger.info("complete skipped, job already terminal (job=%s status=%s)", job_id, job.get("status"))
                return False
            job.update(fields)
            job["status"] = status
            job["_finished_at"] = time.time()
            return True

        self.mutate(job_id, _apply)

    def fail(self, job_id: str, message: str, *, terminate_worker: bool = False) -> None:
        if terminate_worker:
            job = self.get_record(job_id)
            if job is not None:
                job_runner.terminate_worker(job.get("worker_pid"))

        def _apply(job: dict[str, Any]) -> bool:
            if not self._is_active(job):
                return False
            job["status"] = "error"
            job["error"] = message
            job["_finished_at"] = time.time()
            return True

        self.mutate(job_id, _apply)

    def claim(self, scope: str, make_job: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        """Reuse the live active job for ``scope`` or create+persist a new one. Returns (job, reused).
        A dead-worker or never-started active job is failed and replaced, not reused.

        With a ``creation_lock`` the whole check-then-create runs under that cross-process lock and a
        live job another process created (found by scanning the jobs dir) is reused too: the in-memory
        index cannot see it (recording: five concurrent jobs for one session, reproduced live)."""
        with self._start_lock, (self._creation_lock() if self._creation_lock else nullcontext()):
            self.prune()
            with self.jobs_lock:
                existing_id = self.active.get(scope)
            existing = self.get_record(existing_id) if existing_id else None
            if self._is_active(existing):
                pid = existing.get("worker_pid")
                if pid is not None and not self.alive(existing):
                    self.fail(existing_id, "worker process exited unexpectedly")
                elif (
                    existing.get("status") == "running"
                    and pid is None
                    and self._run_age(existing) > self._running_no_pid()
                ):
                    self.fail(existing_id, "worker never started")
                else:
                    return existing, True
            if self._creation_lock is not None:
                on_disk = self._find_live_on_disk(scope)
                if on_disk is not None:
                    with self.jobs_lock:
                        self.jobs[on_disk["job_id"]] = on_disk
                        self.active[scope] = on_disk["job_id"]
                    return on_disk, True
            job = make_job()
            with self.jobs_lock:
                self.jobs[job["job_id"]] = job
                self.active[scope] = job["job_id"]
            self.write(job)
            return job, False

    def _find_live_on_disk(self, scope: str) -> dict[str, Any] | None:
        """The live active job of ``scope`` found by scanning the jobs dir, failing any active row whose
        worker died on the way (a crashed process's stale row must not wedge claim forever)."""
        root = self._jobs_root()
        if not root.is_dir():
            return None
        live: dict[str, Any] | None = None
        for path in sorted(root.iterdir()):
            if not path.is_dir() or not job_runner.job_id_valid(path.name):
                continue
            job = self.read(path.name)
            if not self._is_active(job) or self._scope_for(job) != scope:
                continue
            if self.alive(job):
                live = live or job
            else:
                self.fail(path.name, "worker process exited unexpectedly")
        return live

    def spawn(
        self,
        job_id: str,
        *,
        worker_module: str,
        agent_dir: Path,
        env_mutator: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        proc = job_runner.spawn_worker_process(
            job_id=job_id,
            worker_module=worker_module,
            agent_dir=agent_dir,
            worker_log=self.job_dir(job_id) / "worker.log",
            env_mutator=env_mutator,
        )
        self.mutate(job_id, lambda job: job.update(worker_pid=proc.pid))

    # -- reconcile ------------------------------------------------------------------------------
    def reconcile_zombie(self, job_id: str) -> bool:
        job = self.get_record(job_id)
        if not self._is_active(job) or self.alive(job):
            return False
        pid = job.get("worker_pid")
        reason = detached_worker.describe_exit(pid) if pid is not None else None
        self.fail(job_id, reason or "worker process exited unexpectedly")
        return True

    def reconcile_queued(self, job_id: str) -> bool:
        """Fail queued jobs whose worker never received a PID."""
        job = self.get_record(job_id)
        if job is None or job.get("status") != "queued" or job.get("worker_pid") is not None:
            return False
        limit = self._queued_no_pid()
        if job_age_seconds(job) <= limit:
            return False
        self.fail(job_id, f"worker never spawned after {limit}s", terminate_worker=True)
        return True

    def reconcile_stale(self, job_id: str) -> bool:
        """Fail running jobs that exceed the wall-clock budget."""
        job = self.get_record(job_id)
        if job is None or job.get("status") != "running" or not self.alive(job):
            return False
        wall_age, budget = self._run_age(job), self._wall_clock(job)
        reason = (
            f"run exceeded wall-clock budget ({int(wall_age)}s > {budget}s)"
            if wall_age > budget
            else self._stale_reason(job, wall_age) if self._stale_reason else None
        )
        if reason is None:
            return False
        self.fail(job_id, reason, terminate_worker=True)
        return True

    def reconcile(self, job_id: str) -> bool:
        return self.reconcile_zombie(job_id) or self.reconcile_queued(job_id) or self.reconcile_stale(job_id)

    def reconcile_all_active(self) -> int:
        """Reconcile every active job (a crashed worker nobody polls would read "running" forever)."""
        with self.jobs_lock:
            job_ids = [i for i, j in self.jobs.items() if self._is_active(j)]
        return sum(1 for job_id in job_ids if self.reconcile(job_id))
