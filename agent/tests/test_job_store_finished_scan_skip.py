"""`GET /recording/active` took 14 s on release: with no adopt window every poll parsed every finished
recording's job.json (large logs). A finished job whose file has not been rewritten is skipped; a rewrite or
a new active job is still found."""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.trade import recording_jobs


def _setup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(recording_jobs, "_jobs_root", lambda: tmp_path)
    with recording_jobs._JOBS_LOCK:
        recording_jobs.RECORDING_JOBS.clear()
        recording_jobs._ACTIVE_BY_SCOPE.clear()
    recording_jobs._STORE._finished_mtime.clear()


def _finished_job(root: Path, job_id: str) -> Path:
    path = root / job_id
    path.mkdir()
    (path / "job.json").write_text(json.dumps({"job_id": job_id, "status": "done", "logs": [{"at": "t", "message": "m"}] * 50}))
    return path / "job.json"


def test_finished_jobs_are_read_once_until_their_file_changes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ids = [f"{i:032x}" for i in range(1, 6)]
    files = [_finished_job(tmp_path, i) for i in ids]
    reads: list[str] = []
    real_read = recording_jobs._STORE.read
    monkeypatch.setattr(recording_jobs._STORE, "read", lambda job_id: (reads.append(job_id), real_read(job_id))[1])

    assert recording_jobs.get_active_job() is None
    assert sorted(reads) == ids
    reads.clear()
    assert recording_jobs.get_active_job() is None
    assert reads == []  # nothing re-parsed

    # A rewrite (new mtime) is read again; and a live job written later is still adopted.
    os.utime(files[0], (1, files[0].stat().st_mtime + 5))
    assert recording_jobs.get_active_job() is None
    assert reads == [ids[0]]


def test_a_job_that_turns_active_after_being_seen_finished_is_found(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    job_id = f"{9:032x}"
    file = _finished_job(tmp_path, job_id)
    assert recording_jobs.get_active_job() is None
    payload = json.loads(file.read_text())
    payload.update(status="waiting_for_open", worker_pid=None)
    file.write_text(json.dumps(payload))
    os.utime(file, (1, file.stat().st_mtime + 5))
    found = recording_jobs.get_active_job()
    assert found is not None and found["job_id"] == job_id
