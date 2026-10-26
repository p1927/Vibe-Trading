"""A corrupt job.json that no worker can own any more is quarantined, not allowed to block every later claim.

Uses the recording store: it has no adopt window, so `_find_live_on_disk` reads every job file."""

from __future__ import annotations

import os
import time

import pytest

from src.trade import job_store, recording_jobs as rec


@pytest.fixture(autouse=True)
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(rec, "_jobs_root", lambda: tmp_path / "rec")
    rec.RECORDING_JOBS.clear()
    rec._ACTIVE_BY_SCOPE.clear()


def _corrupt_job(age_s: float, name: str = "a" * 32):
    d = rec._jobs_root() / name
    d.mkdir(parents=True)
    f = d / "job.json"
    f.write_text("{not json", encoding="utf-8")
    t = time.time() - age_s
    os.utime(f, (t, t))
    return d


def _start():
    return rec.start_job(underlyings=["NIFTY"])


def test_old_corrupt_file_is_quarantined_and_claim_proceeds():
    d = _corrupt_job(job_store._ADOPT_WINDOW_SECONDS + 3600)
    job_id, reused = _start()
    assert not reused and job_id
    assert (d / "job.json.corrupt").read_text() == "{not json"  # bytes kept for inspection
    assert not (d / "job.json").exists()


def test_recent_corrupt_file_still_fails_closed():
    d = _corrupt_job(60)  # could be a live worker's mid-write
    with pytest.raises(ValueError):
        _start()
    assert (d / "job.json").exists() and not (d / "job.json.corrupt").exists()


def test_several_old_corrupt_files_all_quarantined():
    old = job_store._ADOPT_WINDOW_SECONDS + 10
    ds = [_corrupt_job(old, f"{i}" * 32) for i in range(3)]
    _start()
    assert all((d / "job.json.corrupt").exists() for d in ds)


def test_quarantined_file_is_not_requarantined_or_read_again():
    d = _corrupt_job(job_store._ADOPT_WINDOW_SECONDS + 10)
    _start()
    rec.RECORDING_JOBS.clear()
    rec._ACTIVE_BY_SCOPE.clear()
    _start()  # second claim over the same dir: nothing left to read, nothing raised
    assert (d / "job.json.corrupt").exists()


def test_healthy_live_job_is_still_found_next_to_an_old_corrupt_one():
    _corrupt_job(job_store._ADOPT_WINDOW_SECONDS + 10)
    first, _ = _start()
    rec.RECORDING_JOBS.clear()
    rec._ACTIVE_BY_SCOPE.clear()
    again, reused = _start()
    assert reused and again == first
