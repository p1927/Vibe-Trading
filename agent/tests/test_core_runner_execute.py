"""Integration tests for src.core.runner.Runner.execute().

Closes the last remaining gap in the agent-module-coverage-gaps backlog item:
the pure/self-contained sandbox helpers (_rlimit_as_bytes, _copy_runtime_env,
_prepare_sandbox_home, _expand_artifacts_spec) already had unit coverage from
a prior pass (test_core_runner_sandbox_helpers.py); the Runner class body
itself was deferred three times as needing "a real fixture/integration
harness, not a quick unit pass". It turns out execute() genuinely runs a real
Python subprocess against a real entry script -- no mocking is needed, just a
tiny real script on disk, which is what this file does.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from src.core.runner import Runner


def _write_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "entry.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def test_execute_reports_success_and_captures_stdout(tmp_path):
    run_dir = tmp_path / "run"
    entry = _write_script(
        tmp_path,
        """
        print("hello from entry script")
        """,
    )

    result = Runner(timeout=30).execute(entry, run_dir)

    assert result.success is True
    assert result.exit_code == 0
    assert "hello from entry script" in result.stdout
    assert (run_dir / "logs" / "runner_stdout.txt").read_text(encoding="utf-8") == result.stdout
    assert (run_dir / "logs" / "runner_stderr.txt").read_text(encoding="utf-8") == result.stderr


def test_execute_reports_failure_and_captures_stderr(tmp_path):
    run_dir = tmp_path / "run"
    entry = _write_script(
        tmp_path,
        """
        import sys
        print("failing on purpose", file=sys.stderr)
        sys.exit(3)
        """,
    )

    result = Runner(timeout=30).execute(entry, run_dir)

    assert result.success is False
    assert result.exit_code == 3
    assert "failing on purpose" in result.stderr


def test_execute_passes_cli_args_through_to_the_subprocess(tmp_path):
    run_dir = tmp_path / "run"
    entry = _write_script(
        tmp_path,
        """
        import sys
        print("argv:" + ",".join(sys.argv[1:]))
        """,
    )

    result = Runner(timeout=30).execute(entry, run_dir, cli_args=["--foo", "bar"])

    assert result.success is True
    assert "argv:--foo,bar" in result.stdout


def test_execute_collects_only_artifacts_that_exist_on_disk(tmp_path):
    run_dir = tmp_path / "run"
    entry = _write_script(
        tmp_path,
        """
        import os, sys
        run_dir = sys.argv[1]
        with open(os.path.join(run_dir, "present.txt"), "w") as fh:
            fh.write("data")
        """,
    )
    spec = {
        "artifacts": {
            "present": {"path": "present.txt"},
            "missing": {"path": "missing.txt"},
        }
    }

    result = Runner(timeout=30, artifacts_spec=spec).execute(
        entry, run_dir, cli_args=[str(run_dir)]
    )

    assert result.success is True
    assert set(result.artifacts) == {"present"}
    assert result.artifacts["present"] == run_dir / "present.txt"


def test_execute_uses_custom_cwd_and_extends_pythonpath(tmp_path):
    run_dir = tmp_path / "run"
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "helper_module.py").write_text("VALUE = 'from helper'\n", encoding="utf-8")
    entry = _write_script(
        tmp_path,
        """
        import helper_module
        print(helper_module.VALUE)
        """,
    )

    result = Runner(timeout=30).execute(entry, run_dir, cwd=pkg_dir)

    assert result.success is True
    assert "from helper" in result.stdout


def test_execute_raises_timeout_expired_when_subprocess_runs_too_long(tmp_path):
    run_dir = tmp_path / "run"
    entry = _write_script(
        tmp_path,
        """
        import time
        time.sleep(5)
        """,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        Runner(timeout=1).execute(entry, run_dir)


def test_execute_still_cleans_up_sandbox_home_after_timeout(tmp_path, monkeypatch):
    """The ephemeral sandbox HOME is removed in a `finally` even when the
    subprocess itself raises (timeout) rather than returning normally."""
    import src.core.runner as runner_mod

    run_dir = tmp_path / "run"
    entry = _write_script(
        tmp_path,
        """
        import time
        time.sleep(5)
        """,
    )

    created: list[Path] = []
    real_prepare = runner_mod._prepare_sandbox_home

    def _spy_prepare(real_home):
        home = real_prepare(real_home)
        if home is not None:
            created.append(home)
        return home

    monkeypatch.setattr(runner_mod, "_prepare_sandbox_home", _spy_prepare)

    with pytest.raises(subprocess.TimeoutExpired):
        Runner(timeout=1).execute(entry, run_dir)

    assert created, "expected _prepare_sandbox_home to have been called"
    assert not created[0].exists()


def test_pick_python_interpreter_falls_back_to_sys_executable_when_no_venv(tmp_path):
    runner = Runner()
    assert runner._pick_python_interpreter() == sys.executable


def test_run_sandboxed_runs_without_uid_drop_when_no_sandbox_user(monkeypatch):
    import src.core.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_resolve_sandbox_credentials", lambda: None)
    runner = Runner()

    result = runner._run_sandboxed(
        [sys.executable, "-c", "print('ok')"],
        dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10),
    )
    assert result.returncode == 0
    assert "ok" in result.stdout
