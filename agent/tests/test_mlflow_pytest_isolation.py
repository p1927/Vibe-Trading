"""A vibetrading pytest run never resolves an inherited MLflow store from Trade.

Both tiers' `.env` set `MLFLOW_TRACKING_URI`, and Trade's
`trade_integrations.observability.mlflow_config.tracking_uri()` lets an explicit value win
over its own per-process pytest temp store. Trade's own `tests/conftest.py` deletes the
inherited value (see `.claude/backlog/archive/items/2026-09-16-mlflow-tier-stores-hold-pytest-runs.md`),
but this fork's `tests/conftest.py` only sandboxed `HOME`/`USERPROFILE` and popped
`VIBE_TRADING_HOME` — a vibetrading test that reaches a Trade MLflow writer (board routes,
ledger reconcilers, version stores) from a `.env`-loaded shell could still log into the real
tier store. `conftest.py` now pops `MLFLOW_TRACKING_URI` beside `VIBE_TRADING_HOME`, the same
mechanism, at import time.

This runs a real child pytest with the variable exported, exactly as a `.env`-loaded shell
would, rather than asserting on this process's own environment: the defect was what a process
launched *with* the inherited value resolves to.

See ../../../.claude/backlog/items/2026-09-16-vibetrading-tests-inherit-mlflow-tracking-uri.md.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

# vibetrading/agent/tests/this_file.py -> vibetrading/agent/tests -> agent -> vibetrading -> Trade/
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
THIS_FILE = pathlib.Path(__file__).resolve().relative_to(REPO_ROOT)


def test_a_child_pytest_with_an_inherited_uri_never_resolves_to_it(tmp_path):
    inherited_store = tmp_path / "inherited" / "mlflow.db"
    inherited_store.parent.mkdir()
    env = dict(os.environ)
    env["MLFLOW_TRACKING_URI"] = f"sqlite:///{inherited_store}"
    env.pop("PYTEST_CURRENT_TEST", None)

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            f"{THIS_FILE}::test_child_process_does_not_see_the_inherited_uri",
            "-q", "-p", "no:randomly", "-p", "no:cacheprovider",
            f"--basetemp={tmp_path / 'child'}",
        ],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    assert not inherited_store.exists(), (
        f"the inherited store {inherited_store} was created — MLFLOW_TRACKING_URI leaked "
        "into a vibetrading test's process instead of being stripped by conftest.py"
    )


def test_child_process_does_not_see_the_inherited_uri():
    """Only meaningful when run as the child subprocess above, with the variable exported
    before the interpreter starts. Standalone (normal suite run, nothing inherited) it still
    passes trivially — conftest.py has nothing to pop in that case either."""
    from trade_integrations.observability import mlflow_config

    assert "MLFLOW_TRACKING_URI" not in os.environ, (
        "conftest.py should have popped an inherited MLFLOW_TRACKING_URI at import time"
    )
    resolved = mlflow_config.tracking_uri()
    assert "inherited" not in resolved, (
        f"tracking_uri() resolved to the inherited store despite the pop: {resolved}"
    )
