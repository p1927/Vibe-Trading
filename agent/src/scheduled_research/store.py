"""Crash-safe store for scheduled research jobs.

Uses the same atomic write pattern as ``src.live.runtime.jobstore`` (write a
temp file in the same directory, fsync, replace, fsync the parent dir) so the
store survives a SIGKILL at any point without corruption.

A missing store file is the only clean empty result. A file that exists but
fails to parse is quarantined and ``load`` raises ``CorruptStoreError`` instead
of silently returning an empty list.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.config.paths import get_runtime_root
from src.scheduled_research.models import ScheduledResearchJob, validate_schedule, validate_timezone_shape

logger = logging.getLogger(__name__)

_STORE_FILENAME = "scheduled_research_jobs.json"
#: Bumped 1->2 when each job record's on-disk shape switched from flat to
#: nested `definition`/`state` (see `ScheduledResearchJob.to_dict`). Purely
#: informational today — `load()` doesn't branch on it, since `from_dict`
#: already accepts both shapes and every record self-migrates to the new one
#: on its next write.
_SCHEMA_VERSION = 2

#: Fields `update_run_state` is allowed to patch. Covers both the executor's
#: lifecycle fields (status, timing, delivery, verdict, config-as-scratch) and
#: pause_control's ownership fields (paused, auto_paused_reason) — each caller
#: passes only the subset it actually owns, which is what keeps one writer
#: from clobbering the other's concurrent change (see `update_run_state`).
#: `config` is included even though it also carries user-authored definition
#: parameters: the executor uses it as scratch space too (`_timed_out`,
#: `_last_result_summary`, `_cancel_requested`) and must persist those pops in
#: the same write as the lifecycle fields they accompany.
RUN_STATE_FIELDS = frozenset(
    {
        "status",
        "next_run_at",
        "last_run_at",
        "consecutive_failures",
        "last_error",
        "failure_kind",
        "delivery",
        "last_verdict",
        "paused",
        "auto_paused_reason",
        "last_result_summary",
        "config",
    }
)


def _default_store_path() -> Path:
    """Return the default path for the scheduled-research store.

    Roots job state under the user runtime dir (``~/.vibe-trading`` by
    default), never inside the repo working tree — the same root the live
    runtime, swarm config, and persistent memory resolve via
    :func:`src.config.paths.get_runtime_root`.
    """
    return get_runtime_root() / "scheduled_research" / _STORE_FILENAME


class CorruptStoreError(RuntimeError):
    """Raised when the store exists but cannot be parsed.

    The corrupt file is renamed aside (quarantined) before this is raised.

    Attributes:
        original: Path that failed to parse.
        quarantined: Path the corrupt file was moved to.
        cause: Short description of the parse failure.
    """

    def __init__(self, original: Path, quarantined: Path, cause: str) -> None:
        super().__init__(f"scheduled-research store {original} is corrupt ({cause}); quarantined to {quarantined}")
        self.original = original
        self.quarantined = quarantined
        self.cause = cause


class ScheduledResearchJobStore:
    """Durable, crash-safe persistence for scheduled research jobs.

    The store is a thin envelope around a dict of
    :class:`~src.scheduled_research.models.ScheduledResearchJob` keyed by
    job id. It owns only serialization and atomic I/O; scheduling decisions
    live elsewhere.

    Attributes:
        path: Absolute path of the backing JSON file.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        """Initialize the store.

        Args:
            path: Explicit path. Defaults to
                ``<runtime root>/scheduled_research/scheduled_research_jobs.json``
                (see :func:`_default_store_path`).
        """
        self.path: Path = path if path is not None else _default_store_path()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Dict[str, ScheduledResearchJob]:
        """Load all persisted jobs.

        Returns:
            A dict mapping job id to job. Empty when the store has never been
            written.

        Raises:
            CorruptStoreError: When the file exists but cannot be parsed.
        """
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            envelope = json.loads(raw)
            jobs_raw = self._extract_jobs(envelope)
            result: Dict[str, ScheduledResearchJob] = {}
            for item in jobs_raw:
                job = ScheduledResearchJob.from_dict(item)
                result[job.id] = job
            return result
        except (OSError, ValueError, KeyError, TypeError) as exc:
            quarantined = self._quarantine(str(exc))
            raise CorruptStoreError(self.path, quarantined, str(exc)) from exc

    def save(self, jobs: Dict[str, ScheduledResearchJob]) -> None:
        """Atomically persist the full job set.

        Write sequence: temp file in same dir -> fsync -> os.replace -> fsync
        parent dir. A SIGKILL at any step leaves either the old complete store
        or the new one, never a partial write.

        Args:
            jobs: Mapping of job id to job (the full set, not a delta).

        Raises:
            OSError: When the directory cannot be created or the write fails.
        """
        target = self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._envelope(jobs), ensure_ascii=False, indent=2)

        last_exc: OSError | None = None
        for attempt in range(5):
            tmp = target.with_name(f".{target.name}.{os.getpid()}.{attempt}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, payload.encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

            try:
                os.replace(tmp, target)
                self._fsync_dir(target.parent)
                return
            except OSError as exc:
                last_exc = exc
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.05 * (attempt + 1))

        if last_exc is not None:
            raise last_exc

    def upsert(self, job: ScheduledResearchJob, *, validate: bool = True) -> None:
        """Insert or replace a job by id.

        Validates the schedule string and the timezone's shape before
        persisting. The timezone key is deliberately not resolved here — see
        :func:`~src.scheduled_research.models.validate_timezone_shape` — so
        executor lifecycle writes keep working on a host whose timezone
        database lacks a key that validated where the job was created.

        Args:
            job: The job to store.
            validate: When ``False``, skip schedule/timezone validation. Set by
                the executor when recording lifecycle state (RUNNING, FAILED,
                a retry time) for a job that is *already* persisted: such a
                write must always land, otherwise a record whose schedule no
                longer validates could never be marked failed and would retry
                every tick forever. Creation paths keep the default.

        Raises:
            ValueError: When ``job.schedule`` or ``job.timezone`` is malformed
                and *validate* is true.
            CorruptStoreError: When the existing store cannot be parsed.
        """
        if validate:
            validate_schedule(job.schedule)
            validate_timezone_shape(job.timezone)
        jobs = self.load()
        jobs[job.id] = job
        self.save(jobs)

    def update_run_state(self, job_id: str, **state_fields) -> Optional[ScheduledResearchJob]:
        """Patch only the named run-state fields of one job.

        Unlike :meth:`upsert`, which replaces the entire record with whatever
        the caller's in-memory copy holds, this re-reads the job fresh from
        disk immediately before writing and only overwrites the given fields
        — every field the caller did not name (in particular ``paused``/
        ``auto_paused_reason`` when the executor is the caller, or the
        lifecycle fields when :mod:`pause_control` is the caller) is left
        exactly as it is on disk. This is what stops a lifecycle write from a
        long-running dispatch from silently reverting a pause/resume click
        that landed while that dispatch was in flight, and vice versa.

        Args:
            job_id: The job to patch.
            **state_fields: Field name -> new value. Every key must be in
                :data:`RUN_STATE_FIELDS`.

        Returns:
            The updated job, or ``None`` if *job_id* no longer exists (deleted
            or replaced by a concurrent write).

        Raises:
            ValueError: A key in *state_fields* is not a run-state field.
            CorruptStoreError: When the existing store cannot be parsed.
        """
        unknown = set(state_fields) - RUN_STATE_FIELDS
        if unknown:
            raise ValueError(f"not run-state fields: {sorted(unknown)}")
        jobs = self.load()
        job = jobs.get(job_id)
        if job is None:
            return None
        for key, value in state_fields.items():
            setattr(job, key, value)
        self.save(jobs)
        return job

    def get(self, job_id: str) -> Optional[ScheduledResearchJob]:
        """Return a job by id, or ``None`` when it does not exist.

        Args:
            job_id: Job identifier.

        Returns:
            The matching job or ``None``.
        """
        return self.load().get(job_id)

    def list_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[ScheduledResearchJob]:
        """Return jobs, optionally filtered by status.

        Args:
            status: When provided, include only jobs whose status matches this
                string (e.g. ``"pending"``).
            limit: Maximum number of jobs to return (newest first by
                ``created_at``).

        Returns:
            A list of at most *limit* jobs sorted descending by ``created_at``.
        """
        jobs = list(self.load().values())
        if status is not None:
            jobs = [j for j in jobs if j.status.value == status]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    def delete(self, job_id: str) -> bool:
        """Remove a job by id.

        Args:
            job_id: Identifier of the job to remove.

        Returns:
            ``True`` when the job was found and removed; ``False`` when it was
            not in the store.
        """
        jobs = self.load()
        if job_id not in jobs:
            return False
        del jobs[job_id]
        self.save(jobs)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _quarantine(self, cause: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        quarantined = self.path.with_name(f"{self.path.name}.corrupt-{ts}")
        try:
            os.replace(self.path, quarantined)
            logger.error(
                "scheduled-research store %s corrupt (%s) — quarantined to %s",
                self.path,
                cause,
                quarantined,
            )
        except OSError:
            logger.error(
                "scheduled-research store %s corrupt (%s) — quarantine rename failed",
                self.path,
                cause,
                exc_info=True,
            )
            return self.path
        return quarantined

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            logger.debug("parent-dir fsync unsupported on %s", directory, exc_info=True)
        finally:
            os.close(dir_fd)

    @staticmethod
    def _envelope(jobs: Dict[str, ScheduledResearchJob]) -> dict:
        return {
            "schema_version": _SCHEMA_VERSION,
            "jobs": [j.to_dict() for j in jobs.values()],
        }

    @staticmethod
    def _extract_jobs(envelope: object) -> List[dict]:
        if not isinstance(envelope, dict):
            raise ValueError("store root is not a JSON object")
        jobs = envelope.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("store 'jobs' is missing or not a list")
        if not all(isinstance(item, dict) for item in jobs):
            raise ValueError("store 'jobs' contains a non-object entry")
        return jobs
