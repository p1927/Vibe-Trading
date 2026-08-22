"""Tests for the pure/self-contained sandbox-prep helpers in src.core.runner.

Domain: 2026-08-21-agent-module-coverage-gaps (core/runner.py was flagged as a remaining gap —
the full Runner class needs a real subprocess/fixture harness, out of scope for a quick pass,
but these standalone helpers are pure and worth covering on their own).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import runner


class TestRlimitAsBytes:
    def test_defaults_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(runner._SANDBOX_RLIMIT_AS_MB_ENV, raising=False)
        assert runner._rlimit_as_bytes() == runner._DEFAULT_RLIMIT_AS_MB * 1024 * 1024

    def test_honors_valid_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(runner._SANDBOX_RLIMIT_AS_MB_ENV, "512")
        assert runner._rlimit_as_bytes() == 512 * 1024 * 1024

    def test_falls_back_on_non_numeric_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(runner._SANDBOX_RLIMIT_AS_MB_ENV, "not-a-number")
        assert runner._rlimit_as_bytes() == runner._DEFAULT_RLIMIT_AS_MB * 1024 * 1024

    def test_falls_back_on_zero_or_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(runner._SANDBOX_RLIMIT_AS_MB_ENV, "0")
        assert runner._rlimit_as_bytes() == runner._DEFAULT_RLIMIT_AS_MB * 1024 * 1024
        monkeypatch.setenv(runner._SANDBOX_RLIMIT_AS_MB_ENV, "-10")
        assert runner._rlimit_as_bytes() == runner._DEFAULT_RLIMIT_AS_MB * 1024 * 1024

    def test_falls_back_on_blank_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(runner._SANDBOX_RLIMIT_AS_MB_ENV, "   ")
        assert runner._rlimit_as_bytes() == runner._DEFAULT_RLIMIT_AS_MB * 1024 * 1024


class TestRuntimeEnvAllowlist:
    @pytest.mark.parametrize(
        "key",
        ["PATH", "HOME", "TZ", "TUSHARE_TOKEN", "HTTP_PROXY", "LC_ALL", "LC_TIME"],
    )
    def test_allows_known_safe_keys(self, key: str) -> None:
        assert runner._is_runtime_env_key_allowed(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "API_AUTH_KEY",
            "OPENALGO_API_KEY",
            "VIBE_TRADING_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "SOME_RANDOM_VAR",
        ],
    )
    def test_rejects_unlisted_keys_including_credential_shaped_ones(self, key: str) -> None:
        assert runner._is_runtime_env_key_allowed(key) is False

    def test_copy_runtime_env_filters_to_allowlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
        monkeypatch.setenv("TUSHARE_TOKEN", "safe-to-copy")

        copied = runner._copy_runtime_env()

        assert copied.get("PATH") == "/usr/bin"
        assert copied.get("TUSHARE_TOKEN") == "safe-to-copy"
        assert "ANTHROPIC_API_KEY" not in copied

    def test_copy_runtime_env_never_leaks_any_non_allowlisted_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Exercise the allowlist against the *real* ambient environment (not a curated
        # subset) — the actual security property this function exists for is "nothing
        # outside the allowlist ever crosses the boundary", regardless of what happens
        # to be set in whichever environment the test runs in.
        copied = runner._copy_runtime_env()
        for key in copied:
            assert runner._is_runtime_env_key_allowed(key), f"leaked non-allowlisted key: {key}"


class TestPrepareSandboxHome:
    def test_creates_a_fresh_writable_directory(self) -> None:
        sandbox = runner._prepare_sandbox_home(None)
        try:
            assert sandbox.is_dir()
            assert (sandbox.stat().st_mode & 0o777) == 0o755
        finally:
            import shutil

            shutil.rmtree(sandbox, ignore_errors=True)

    def test_re_exposes_only_the_allowlisted_vibe_trading_subpaths(self, tmp_path: Path) -> None:
        real_home = tmp_path / "real_home"
        vt_dir = real_home / ".vibe-trading"
        vt_dir.mkdir(parents=True)
        for rel in runner._SANDBOX_HOME_REEXPOSE:
            target = vt_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("allowed", encoding="utf-8")
        secret = vt_dir / "not-on-the-allowlist.txt"
        secret.write_text("should not be re-exposed", encoding="utf-8")

        sandbox = runner._prepare_sandbox_home(real_home)
        try:
            dst_root = sandbox / ".vibe-trading"
            for rel in runner._SANDBOX_HOME_REEXPOSE:
                assert (dst_root / rel).exists()
            assert not (dst_root / "not-on-the-allowlist.txt").exists()
        finally:
            import shutil

            shutil.rmtree(sandbox, ignore_errors=True)

    def test_seeds_mootdx_config_from_real_home_when_present(self, tmp_path: Path) -> None:
        real_home = tmp_path / "real_home"
        mootdx_dir = real_home / ".mootdx"
        mootdx_dir.mkdir(parents=True)
        (mootdx_dir / "config.json").write_text(
            json.dumps({"bestip": {"hq": ["1.2.3.4:7709"]}}), encoding="utf-8"
        )

        sandbox = runner._prepare_sandbox_home(real_home)
        try:
            written = json.loads((sandbox / ".mootdx" / "config.json").read_text(encoding="utf-8"))
            assert written == {"bestip": {"hq": ["1.2.3.4:7709"]}}
        finally:
            import shutil

            shutil.rmtree(sandbox, ignore_errors=True)

    def test_handles_missing_real_home_gracefully(self) -> None:
        sandbox = runner._prepare_sandbox_home(Path("/nonexistent/definitely/not/here"))
        try:
            assert sandbox.is_dir()
        finally:
            import shutil

            shutil.rmtree(sandbox, ignore_errors=True)


class TestExpandArtifactsSpec:
    def test_non_dict_spec_returns_empty(self) -> None:
        assert runner._expand_artifacts_spec(None) == {}
        assert runner._expand_artifacts_spec("not a dict") == {}  # type: ignore[arg-type]

    def test_expands_default_artifacts_spec(self) -> None:
        expanded = runner._expand_artifacts_spec(runner._ARTIFACTS_SPEC)
        assert expanded["equity"]["path"] == "artifacts/equity.csv"
        assert expanded["equity"]["required"] is True
        assert expanded["positions"]["required"] is False
        assert expanded["equity"]["columns"] == runner._ARTIFACTS_SPEC["schemas"]["equity_csv"]["columns"]

    def test_explicit_required_flag_overrides_defaults_list(self) -> None:
        spec = {
            "defaults": {"required": []},
            "schemas": {},
            "artifacts": {"custom": {"path": "artifacts/custom.csv", "required": True}},
        }
        expanded = runner._expand_artifacts_spec(spec)
        assert expanded["custom"]["required"] is True

    def test_skips_non_dict_artifact_entries(self) -> None:
        spec = {"artifacts": {"bad": "not-a-dict", "good": {"path": "x.csv"}}}
        expanded = runner._expand_artifacts_spec(spec)
        assert "bad" not in expanded
        assert "good" in expanded
