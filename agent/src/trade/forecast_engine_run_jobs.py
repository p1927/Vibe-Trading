"""File-backed job store for on-demand forecast_engine evaluation runs (a named historical run
over a user-chosen date range — see `trade_integrations.forecast_engine.artifact`'s
"Named historical runs" section, distinct from the single always-overwritten "latest" evaluation).

Mirrors `external_predictions_run_jobs.py`'s shape closely: jobs persist under
``log/forecast_engine_run_jobs/{job_id}/job.json`` so SSE polling survives API hot-reload,
workers run in a detached subprocess, and active-job dedup uses a composite scope key — here
``(ticker, recipe)`` — so two different recipes can run concurrently but the same recipe can't
be double-started. No creation lock is needed (unlike recording_jobs.py): this isn't a
singleton-resource case, just a per-scope one, same as external predictions.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.trade import detached_worker, job_runner

logger = logging.getLogger(__name__)

FORECAST_ENGINE_RUN_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_BY_SCOPE: dict[str, str] = {}
_JOBS_LOCK = threading.Lock()

_JOB_TTL_SECONDS = 60 * 60
_ACTIVE_STATUSES = frozenset({"queued", "running"})
# TimesFM evaluation runs are real, multi-minute model inference (one call per cutoff) — give
# this a generous budget rather than reusing index-prediction's/external-predictions' numbers,
# which cover very different workloads.
_WALL_CLOCK_SECONDS = 30 * 60
_QUEUED_NO_PID_SECONDS = 60


def _is_pid_alive(pid: int | None) -> bool:
    return detached_worker.is_alive(pid)


def worker_alive(job: dict[str, Any] | None) -> bool:
    if job is None:
        return False
    status = str(job.get("status") or "")
    pid = job.get("worker_pid")
    if status == "queued" and pid is None:
        return True
    if pid is None:
        return status not in _ACTIVE_STATUSES
    return _is_pid_alive(int(pid))


def reconcile_zombie_job(job_id: str) -> bool:
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        return False
    if worker_alive(job):
        return False
    pid = job.get("worker_pid")
    reason = detached_worker.describe_exit(pid) if pid is not None else None
    fail_job(job_id, reason or "worker process exited unexpectedly")
    return True


def _parse_iso_timestamp(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _job_age_seconds(job: dict[str, Any]) -> float:
    created = _parse_iso_timestamp(str(job.get("created_at") or ""))
    if created is None:
        return 0.0
    return max(0.0, datetime.now(timezone.utc).timestamp() - created)


def reconcile_queued_job(job_id: str) -> bool:
    """Fail queued jobs whose worker never received a PID."""
    job = _get_job_record(job_id)
    if job is None or job.get("status") != "queued":
        return False
    if job.get("worker_pid") is not None:
        return False
    if _job_age_seconds(job) <= _QUEUED_NO_PID_SECONDS:
        return False
    fail_job(job_id, f"worker never spawned after {_QUEUED_NO_PID_SECONDS}s", terminate_worker=True)
    return True


def reconcile_stale_job(job_id: str) -> bool:
    """Fail running jobs that exceed wall-clock budget."""
    job = _get_job_record(job_id)
    if job is None or job.get("status") != "running":
        return False
    if not worker_alive(job):
        return False
    wall_age = _job_age_seconds(job)
    if wall_age > _WALL_CLOCK_SECONDS:
        fail_job(
            job_id,
            f"run exceeded wall-clock budget ({int(wall_age)}s > {_WALL_CLOCK_SECONDS}s)",
            terminate_worker=True,
        )
        return True
    return False


def reconcile_job(job_id: str) -> bool:
    """Run all reconciliation checks. Returns True if job was terminalized."""
    if reconcile_zombie_job(job_id):
        return True
    if reconcile_queued_job(job_id):
        return True
    if reconcile_stale_job(job_id):
        return True
    return False


def reconcile_all_active_jobs() -> int:
    """Run reconciliation over every job currently marked active — a crashed worker for a job
    nobody is polling would otherwise sit reporting "running" forever. Returns the number of
    jobs terminalized."""
    with _JOBS_LOCK:
        job_ids = [
            job_id
            for job_id, job in FORECAST_ENGINE_RUN_JOBS.items()
            if job.get("status") in _ACTIVE_STATUSES
        ]
    return sum(1 for job_id in job_ids if reconcile_job(job_id))


def _terminate_worker(job: dict[str, Any] | None) -> None:
    if job is None:
        return
    job_runner.terminate_worker(job.get("worker_pid"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_key(ticker: str, recipe: str) -> str:
    return f"{(ticker or 'NIFTY').strip().upper()}:{(recipe or '').strip()}"


def _jobs_root() -> Path:
    from src.trade.hub_bridge import trade_repo_root

    root = trade_repo_root()
    if root is None:
        root = Path.cwd()
    return root / "log" / "forecast_engine_run_jobs"


def _job_dir(job_id: str) -> Path:
    return _jobs_root() / job_id


def _job_file(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _job_lock_file(job_id: str) -> Path:
    return _job_dir(job_id) / ".job.lock"


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "logs": list(job.get("logs") or []),
        "error": job.get("error"),
        "worker_pid": job.get("worker_pid"),
        "_finished_at": job.get("_finished_at"),
        "extra": job.get("extra") or {},
        "result": job.get("result"),
    }


def _write_job_to_disk_unsafe(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "")
    if not job_id_valid(job_id):
        return
    job_runner.write_job_json_unsafe(_job_file(job_id), _serialize_job(job))


def _read_job_from_disk_unsafe(job_id: str) -> dict[str, Any] | None:
    payload = job_runner.read_job_json_unsafe(_job_file(job_id))
    if payload is None or not job_id_valid(str(payload.get("job_id") or job_id)):
        return None
    payload.setdefault("job_id", job_id)
    payload.setdefault("logs", [])
    payload.setdefault("extra", {})
    return payload


def _write_job_to_disk(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "")
    with job_runner.job_file_lock(_job_lock_file(job_id), valid=job_id_valid(job_id), exclusive=True):
        _write_job_to_disk_unsafe(job)


def _read_job_from_disk(job_id: str) -> dict[str, Any] | None:
    with job_runner.job_file_lock(_job_lock_file(job_id), valid=job_id_valid(job_id), exclusive=False):
        return _read_job_from_disk_unsafe(job_id)


def _mutate_job(job_id: str, mutator) -> dict[str, Any] | None:
    """Apply an in-memory job mutation under a cross-process file lock (API vs worker
    subprocess), then persist atomically — see external_predictions_run_jobs.py's own
    ``_mutate_job`` docstring for why this needs the lock unlike the ``_mutate_job_on_disk``
    variant the other two systems use."""
    with job_runner.job_file_lock(_job_lock_file(job_id), valid=job_id_valid(job_id), exclusive=True):
        with _JOBS_LOCK:
            job = FORECAST_ENGINE_RUN_JOBS.get(job_id)
        if job is None:
            job = _read_job_from_disk_unsafe(job_id)
            if job is None:
                return None
        mutator(job)
        _write_job_to_disk_unsafe(job)
        with _JOBS_LOCK:
            FORECAST_ENGINE_RUN_JOBS[job_id] = job
        return job


def _merge_job_from_disk(job_id: str, memory: dict[str, Any] | None) -> dict[str, Any] | None:
    disk = _read_job_from_disk(job_id)
    if disk is None:
        return memory
    if memory is None:
        return disk
    mem_logs = len(memory.get("logs") or [])
    disk_logs = len(disk.get("logs") or [])
    mem_status = str(memory.get("status") or "")
    disk_status = str(disk.get("status") or "")
    if disk_logs > mem_logs or disk_status != mem_status:
        merged = dict(disk)
        merged.setdefault("job_id", job_id)
        return merged
    if disk.get("result") is not None and memory.get("result") is None:
        merged = dict(memory)
        merged.update(
            {
                "status": disk_status or mem_status,
                "result": disk.get("result"),
                "error": disk.get("error"),
                "worker_pid": disk.get("worker_pid", memory.get("worker_pid")),
                "_finished_at": disk.get("_finished_at", memory.get("_finished_at")),
            }
        )
        if disk_logs >= mem_logs:
            merged["logs"] = list(disk.get("logs") or [])
        return merged
    return memory


def _scope_for(job: dict[str, Any]) -> str:
    extra = job.get("extra") or {}
    return _scope_key(str(extra.get("ticker") or "NIFTY"), str(extra.get("recipe") or ""))


def _get_job_record(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        memory = FORECAST_ENGINE_RUN_JOBS.get(job_id)
    merged = _merge_job_from_disk(job_id, memory)
    if merged is not None:
        with _JOBS_LOCK:
            FORECAST_ENGINE_RUN_JOBS[job_id] = merged
            scope = _scope_for(merged)
            if merged.get("status") in _ACTIVE_STATUSES:
                _ACTIVE_BY_SCOPE[scope] = job_id
            elif _ACTIVE_BY_SCOPE.get(scope) == job_id:
                _ACTIVE_BY_SCOPE.pop(scope, None)
    return merged


def hydrate_jobs_from_disk() -> None:
    root = _jobs_root()
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_dir():
            continue
        job_id = path.name
        if not job_id_valid(job_id):
            continue
        job = _read_job_from_disk(job_id)
        if job is None:
            continue
        with _JOBS_LOCK:
            FORECAST_ENGINE_RUN_JOBS[job_id] = job
            scope = _scope_for(job)
            if job.get("status") in _ACTIVE_STATUSES:
                _ACTIVE_BY_SCOPE[scope] = job_id


def _prune_old_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _JOBS_LOCK:
        stale: list[str] = []
        for job_id, job in FORECAST_ENGINE_RUN_JOBS.items():
            if job.get("status") in ("done", "error") and job.get("_finished_at", 0) < cutoff:
                stale.append(job_id)
        for job_id in stale:
            job = FORECAST_ENGINE_RUN_JOBS.pop(job_id, None)
            if job:
                detached_worker.forget(job.get("worker_pid"))
                scope = _scope_for(job)
                if _ACTIVE_BY_SCOPE.get(scope) == job_id:
                    _ACTIVE_BY_SCOPE.pop(scope, None)


def job_id_valid(job_id: str | None) -> bool:
    return job_runner.job_id_valid(job_id)


def _job_snapshot(job: dict[str, Any], *, include_logs: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job["job_id"],
        "status": job["status"],
        "created_at": job.get("created_at"),
        "error": job.get("error"),
        "extra": job.get("extra") or {},
    }
    if include_logs:
        out["logs"] = list(job.get("logs") or [])
    if job.get("status") == "done" and job.get("result") is not None:
        out["result"] = job["result"]
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    job = _get_job_record(job_id)
    if job is None:
        return None
    return _job_snapshot(job)


def get_active_job(*, ticker: str, recipe: str) -> dict[str, Any] | None:
    scope = _scope_key(ticker, recipe)
    with _JOBS_LOCK:
        job_id = _ACTIVE_BY_SCOPE.get(scope)
    if not job_id:
        return None
    job = _get_job_record(job_id)
    if job is None or job.get("status") not in _ACTIVE_STATUSES:
        with _JOBS_LOCK:
            if _ACTIVE_BY_SCOPE.get(scope) == job_id:
                _ACTIVE_BY_SCOPE.pop(scope, None)
        return None
    if not worker_alive(job):
        reconcile_zombie_job(job_id)
        return None
    return _job_snapshot(job)


def start_job(*, ticker: str, recipe: str, start: str, end: str, horizon: int) -> tuple[str, str, bool]:
    """Create or reuse an active job for ``(ticker, recipe)``. Returns ``(job_id, run_id, reused)``.

    ``run_id`` is generated here, at job-start time — not at completion — so the caller (the API
    handler) can hand it back to the frontend immediately, and the frontend can navigate straight
    to the finished run's detail view once the job reports ``done`` without a second round-trip.
    """
    from trade_integrations.forecast_engine.artifact import make_run_id

    _prune_old_jobs()
    key = (ticker or "NIFTY").strip().upper()
    recipe_name = (recipe or "").strip()
    scope = _scope_key(key, recipe_name)
    with _JOBS_LOCK:
        existing_id = _ACTIVE_BY_SCOPE.get(scope)
        if existing_id:
            existing = FORECAST_ENGINE_RUN_JOBS.get(existing_id) or _read_job_from_disk(existing_id)
            if existing and existing.get("status") in _ACTIVE_STATUSES:
                if existing.get("worker_pid") is not None and not worker_alive(existing):
                    fail_job(existing_id, "worker process exited unexpectedly")
                else:
                    if existing_id not in FORECAST_ENGINE_RUN_JOBS:
                        FORECAST_ENGINE_RUN_JOBS[existing_id] = existing
                    extra = existing.get("extra") or {}
                    return existing_id, str(extra.get("run_id") or ""), True

        job_id = uuid.uuid4().hex
        run_id = make_run_id(start=start, end=end)
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": _now_iso(),
            "logs": [],
            "result": None,
            "error": None,
            "worker_pid": None,
            "_finished_at": None,
            "extra": {
                "ticker": key,
                "recipe": recipe_name,
                "start": start,
                "end": end,
                "horizon": horizon,
                "run_id": run_id,
            },
        }
        FORECAST_ENGINE_RUN_JOBS[job_id] = job
        _ACTIVE_BY_SCOPE[scope] = job_id
    _write_job_to_disk(job)
    return job_id, run_id, False


def mark_running(job_id: str) -> None:
    def _apply(job: dict[str, Any]) -> None:
        if job.get("status") == "queued":
            job["status"] = "running"

    _mutate_job(job_id, _apply)


def append_log(job_id: str, message: str, *, level: str = "info") -> None:
    def _apply(job: dict[str, Any]) -> None:
        if job.get("status") == "queued":
            job["status"] = "running"
        job.setdefault("logs", []).append({"message": message, "level": level, "at": _now_iso()})

    _mutate_job(job_id, _apply)


def complete_job(job_id: str, *, result: dict[str, Any]) -> None:
    def _apply(job: dict[str, Any]) -> None:
        job["status"] = "done"
        job["result"] = result
        job["_finished_at"] = time.time()
        scope = _scope_for(job)
        if _ACTIVE_BY_SCOPE.get(scope) == job_id:
            _ACTIVE_BY_SCOPE.pop(scope, None)

    _mutate_job(job_id, _apply)


def fail_job(job_id: str, message: str, *, terminate_worker: bool = False) -> None:
    if terminate_worker:
        _terminate_worker(_get_job_record(job_id))

    def _apply(job: dict[str, Any]) -> None:
        job["status"] = "error"
        job["error"] = message
        job["_finished_at"] = time.time()
        scope = _scope_for(job)
        if _ACTIVE_BY_SCOPE.get(scope) == job_id:
            _ACTIVE_BY_SCOPE.pop(scope, None)

    _mutate_job(job_id, _apply)


def run_worker(job_id: str) -> None:
    job = _get_job_record(job_id)
    if job is None:
        return
    extra = job.get("extra") or {}
    ticker = str(extra.get("ticker") or "NIFTY")
    recipe_name = str(extra.get("recipe") or "")
    start = str(extra.get("start") or "")
    end = str(extra.get("end") or "")
    horizon = int(extra.get("horizon") or 5)
    run_id = str(extra.get("run_id") or "")

    mark_running(job_id)
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.forecast_engine.artifact import build_evaluation_artifact, write_run_artifact
        from trade_integrations.forecast_engine.engines.timesfm_engine import CHECKPOINT, TimesFMEngine
        from trade_integrations.forecast_engine.evaluation import evaluate_recipe
        from trade_integrations.forecast_engine.registry import RECIPES

        recipe = RECIPES[recipe_name]

        append_log(job_id, f"loading TimesFM ({CHECKPOINT})...")
        engine = TimesFMEngine()

        def on_progress(message: str) -> None:
            append_log(job_id, message)

        report = evaluate_recipe(
            recipe,
            engine,
            start=start,
            end=end,
            horizon=horizon,
            client=None,
            on_progress=on_progress,
        )
        artifact = build_evaluation_artifact(
            report, ticker=ticker, recipe=recipe, engine_checkpoint=CHECKPOINT, horizon=horizon
        )
        write_run_artifact(artifact, run_id, run_start=start, run_end=end)
        append_log(
            job_id,
            f"done: n_observations={report.n_observations} mean_pinball_loss={report.mean_pinball_loss:.2f} "
            f"passes_gate={report.passes_gate}",
        )
        complete_job(
            job_id,
            result={
                "run_id": run_id,
                "ticker": ticker,
                "recipe": recipe_name,
                "n_observations": report.n_observations,
                "n_skipped_gaps": report.n_skipped_gaps,
                "mean_pinball_loss": report.mean_pinball_loss,
                "passes_gate": report.passes_gate,
            },
        )
    except Exception as exc:
        logger.exception("forecast-engine run worker failed (job=%s)", job_id)
        append_log(job_id, str(exc), level="error")
        fail_job(job_id, str(exc))


def _agent_dir() -> Path:
    return job_runner.agent_dir_from_here(__file__)


def spawn_worker(job_id: str) -> None:
    proc = job_runner.spawn_worker_process(
        job_id=job_id,
        worker_module="src.trade.forecast_engine_run_worker",
        agent_dir=_agent_dir(),
        worker_log=_job_dir(job_id) / "worker.log",
    )
    job = _get_job_record(job_id)
    if job is not None:
        job["worker_pid"] = proc.pid
        _write_job_to_disk(job)


def kick_forecast_engine_run(*, ticker: str, recipe: str, start: str, end: str, horizon: int) -> tuple[str, str, str, bool]:
    """Returns ``(job_id, run_id, job_status, reused)``."""
    job_id, run_id, reused = start_job(ticker=ticker, recipe=recipe, start=start, end=end, horizon=horizon)
    if not reused:
        spawn_worker(job_id)
    else:
        existing = _get_job_record(job_id)
        if existing is not None and not worker_alive(existing):
            spawn_worker(job_id)
    snap = get_job(job_id) or {}
    return job_id, run_id, str(snap.get("status") or "queued"), reused
