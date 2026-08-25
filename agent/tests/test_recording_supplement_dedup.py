"""Tests for the recording-supplement dedup/skip guards.

Covers the bug filed at
``.claude/backlog/items/2026-08-25-recording-auto-rearm-no-backoff-duplicate-supplement.md``:
several recording jobs for the same calendar date (e.g. Auto Record's
rearm poller retrying a fast-failing recording) must not each spawn a
redundant end-of-session supplement scrape, and a recording that captured
zero cycles must not trigger a supplement run at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.trade import recording_jobs
from src.trade.recording_supplement_worker import spawn_supplement_worker


@pytest.fixture(autouse=True)
def _isolated_jobs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "recording_jobs"
    monkeypatch.setattr(recording_jobs, "_jobs_root", lambda: root)
    return root


def test_claim_supplement_run_first_caller_wins() -> None:
    assert recording_jobs.claim_supplement_run("2026-08-25", "job-a") is True


def test_claim_supplement_run_second_caller_same_date_loses() -> None:
    assert recording_jobs.claim_supplement_run("2026-08-25", "job-a") is True
    assert recording_jobs.claim_supplement_run("2026-08-25", "job-b") is False
    assert recording_jobs.claim_supplement_run("2026-08-25", "job-c") is False


def test_claim_supplement_run_different_dates_both_win() -> None:
    assert recording_jobs.claim_supplement_run("2026-08-25", "job-a") is True
    assert recording_jobs.claim_supplement_run("2026-08-26", "job-b") is True


def test_spawn_supplement_worker_skips_on_zero_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(
        "subprocess.Popen", lambda *a, **k: calls.append((a, k)) or pytest.fail("must not spawn")
    )
    pid = spawn_supplement_worker("job-a", "2026-08-25", cycles=0)
    assert pid is None
    assert calls == []
    # A zero-cycle skip must not consume the date's claim — a later job
    # that *does* record something should still be able to supplement.
    assert recording_jobs.claim_supplement_run("2026-08-25", "job-b") is True


def test_spawn_supplement_worker_skips_when_date_already_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert recording_jobs.claim_supplement_run("2026-08-25", "job-a") is True
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: pytest.fail("must not spawn a second time for the same date"),
    )
    pid = spawn_supplement_worker("job-b", "2026-08-25", cycles=5)
    assert pid is None
