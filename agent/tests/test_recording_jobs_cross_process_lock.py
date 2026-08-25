"""Cross-process regression tests for the recording-job single-instance lock.

Covers
``.claude/backlog/items/2026-08-25-recording-active-job-guard-not-cross-process.md``:
``_ACTIVE_JOB_ID`` (``recording_jobs.py``) used to be only an in-process
cache, so two independent process invocations of ``start_job`` each saw
"nothing active in my memory" and both created a job. Reproduced live:
5 concurrently-running ``recording_worker`` subprocesses for the identical
underlyings, none of which the others knew about.

These tests spawn genuinely separate ``python -c`` processes (not threads,
not mocks sharing this test's interpreter) so a passing test actually
proves the fix holds across process boundaries — the whole bug was
invisible to any test that shares memory with the code under test.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

AGENT_SRC = str(Path(__file__).resolve().parents[1] / "src")
INTEGRATIONS_ROOT = str(Path(__file__).resolve().parents[3] / "integrations")
AGENT_DIR = str(Path(__file__).resolve().parents[1])


def _script(jobs_root: Path, *, delay: float = 0.0) -> str:
    return (
        "import json, sys, time\n"
        f"sys.path.insert(0, {AGENT_SRC!r})\n"
        f"sys.path.insert(0, {INTEGRATIONS_ROOT!r})\n"
        "from pathlib import Path\n"
        "from src.trade import recording_jobs\n"
        f"recording_jobs._jobs_root = lambda: Path({str(jobs_root)!r})\n"
        f"time.sleep({delay!r})\n"
        "job_id, reused = recording_jobs.start_job(underlyings=['NIFTY'])\n"
        "print(json.dumps({'job_id': job_id, 'reused': reused}))\n"
    )


def _spawn(jobs_root: Path, *, delay: float = 0.0) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _script(jobs_root, delay=delay)],
        cwd=AGENT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _result(proc: subprocess.Popen) -> dict:
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, f"subprocess failed (rc={proc.returncode}): {err}"
    return json.loads(out.strip().splitlines()[-1])


def test_two_concurrent_processes_converge_on_one_job(tmp_path: Path) -> None:
    """Two genuinely separate processes calling start_job at the same time
    for the same window must not both create a job — exactly one creates,
    the other reuses. Without the cross-process lock, both would see an
    empty in-memory _ACTIVE_JOB_ID and both create (the live bug)."""
    jobs_root = tmp_path / "recording_jobs"

    # Started together (no ordering guarantee) so they genuinely race for
    # the lock rather than one trivially finishing before the other starts.
    proc_a = _spawn(jobs_root)
    proc_b = _spawn(jobs_root)

    result_a = _result(proc_a)
    result_b = _result(proc_b)

    creators = [r for r in (result_a, result_b) if not r["reused"]]
    reusers = [r for r in (result_a, result_b) if r["reused"]]
    assert len(creators) == 1, f"expected exactly one creator, got: {result_a}, {result_b}"
    assert len(reusers) == 1
    assert reusers[0]["job_id"] == creators[0]["job_id"]


def test_third_process_after_first_two_also_reuses(tmp_path: Path) -> None:
    """A third, later process must also converge on the same job rather
    than starting a fresh one, proving the lock holds beyond just the
    first pairwise race."""
    jobs_root = tmp_path / "recording_jobs"

    first = _result(_spawn(jobs_root))
    assert first["reused"] is False

    second = _result(_spawn(jobs_root))
    third = _result(_spawn(jobs_root))

    assert second["reused"] is True
    assert third["reused"] is True
    assert second["job_id"] == first["job_id"]
    assert third["job_id"] == first["job_id"]


def test_dead_worker_pid_does_not_wedge_the_lock_forever(tmp_path: Path) -> None:
    """A job dir left behind by a process that died without cleaning up
    (worker_pid no longer alive) must be reconciled, not treated as a
    permanent block on ever starting a new recording."""
    jobs_root = tmp_path / "recording_jobs"
    jobs_root.mkdir(parents=True)
    stale_id = "a" * 32
    stale_dir = jobs_root / stale_id
    stale_dir.mkdir()
    # A PID essentially guaranteed not to be a live process on this
    # machine right now (max is typically 32768/4194304 territory).
    (stale_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": stale_id,
                "status": "running",
                "underlyings": ["NIFTY"],
                "worker_pid": 9_999_999,
                "logs": [],
                "created_at": "2026-08-24T00:00:00+00:00",
                "_finished_at": None,
                "result": None,
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    result = _result(_spawn(jobs_root))
    assert result["reused"] is False
    assert result["job_id"] != stale_id

    stale_after = json.loads((stale_dir / "job.json").read_text(encoding="utf-8"))
    assert stale_after["status"] == "error"
