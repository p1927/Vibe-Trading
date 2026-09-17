"""Regression test for
``.claude/backlog/items/2026-09-11-recording-job-workers-die-before-run.md``:

``recording_jobs.py``, ``index_prediction_run_jobs.py``, and
``external_predictions_run_jobs.py`` each define a private ``_agent_dir()`` used as the
``cwd`` for the ``python -m src.trade.<worker>`` subprocess their ``spawn_worker()`` launches.
It used to return ``here.parents[1]`` -- for a file at
``.../vibetrading/agent/src/trade/<module>.py`` that's ``.../agent/src``, which has no
``src/`` subfolder of its own. With that ``cwd``, ``-m src.trade.<worker>`` cannot resolve
``src`` locally and silently falls through to whatever *other* ``vibetrading/agent`` happens
to sit on ``PYTHONPATH`` (observed live: a stale copy from a different checkout), which then
resolves its own job store against the *wrong* repo root -- so the freshly created job is never
found (``job is None``), and the worker exits cleanly (code 0) with zero log output, looking
like nothing ran at all.

The fix is ``here.parents[2]`` (the actual ``.../agent`` root). This test guards the invariant
directly: ``_agent_dir()`` must return a directory that actually contains a ``src/`` folder,
which is the one property the whole ``-m src.trade.<worker>`` invocation depends on.
"""

from __future__ import annotations

from src.trade import external_predictions_run_jobs, index_prediction_run_jobs, recording_jobs


def test_recording_jobs_agent_dir_has_src_subfolder() -> None:
    agent_dir = recording_jobs._agent_dir()
    assert (agent_dir / "src").is_dir()
    assert (agent_dir / "src" / "trade" / "recording_worker.py").is_file()


def test_index_prediction_run_jobs_agent_dir_has_src_subfolder() -> None:
    agent_dir = index_prediction_run_jobs._agent_dir()
    assert (agent_dir / "src").is_dir()
    assert (agent_dir / "src" / "trade" / "index_prediction_run_worker.py").is_file()


def test_external_predictions_run_jobs_agent_dir_has_src_subfolder() -> None:
    agent_dir = external_predictions_run_jobs._agent_dir()
    assert (agent_dir / "src").is_dir()
    assert (agent_dir / "src" / "trade" / "external_predictions_run_worker.py").is_file()
