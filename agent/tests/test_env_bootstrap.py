"""Tests for src.config.bootstrap."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.config.accessor import get_env_config, reset_env_config
from src.config.bootstrap import bootstrap_environment, reset_bootstrap


@pytest.fixture(autouse=True)
def _clean_bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reset_bootstrap()
    monkeypatch.chdir(tmp_path)
    for key in (
        "VIBE_TRADING_ENABLE_SCHEDULER",
        "INDEX_RESEARCH_ENABLE_SCHEDULER",
        "INDEX_MONITOR_ENABLE_SCHEDULER",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    reset_bootstrap()


def test_bootstrap_strips_blank_env_and_loads_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    operator_home = tmp_path / "operator"
    operator_home.mkdir()
    operator_vibe = operator_home / ".vibe-trading"
    operator_vibe.mkdir()
    (operator_vibe / ".env").write_text(
        "VIBE_TRADING_ENABLE_SCHEDULER=1\nINDEX_RESEARCH_ENABLE_SCHEDULER=true\n",
        encoding="utf-8",
    )

    trade_root = tmp_path / "trade"
    trade_root.mkdir()
    (trade_root / ".env").write_text("INDEX_MONITOR_ENABLE_SCHEDULER=true\n", encoding="utf-8")

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    monkeypatch.setenv("VIBE_TRADING_ENABLE_SCHEDULER", "")
    monkeypatch.setattr("src.config.bootstrap.Path.home", lambda: operator_home)
    monkeypatch.setattr("src.config.bootstrap.AGENT_DIR", agent_dir)

    report = bootstrap_environment(trade_root=trade_root)

    assert report.bootstrapped is True
    assert "trade/.env" in report.layers_loaded
    assert "VIBE_TRADING_ENABLE_SCHEDULER" in report.stripped_blank_keys
    cfg = get_env_config()
    assert cfg.agent_tuning.vibe_trading_enable_scheduler is True
    assert cfg.agent_tuning.index_research_enable_scheduler is True
    assert cfg.agent_tuning.index_monitor_enable_scheduler is True


def test_bootstrap_preserves_non_empty_stack_exports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trade_root = tmp_path / "trade"
    trade_root.mkdir()
    (trade_root / ".env").write_text("VIBE_TRADING_ENABLE_SCHEDULER=0\n", encoding="utf-8")

    monkeypatch.setenv("VIBE_TRADING_ENABLE_SCHEDULER", "1")
    monkeypatch.setattr("src.config.bootstrap.AGENT_DIR", tmp_path / "agent")

    report = bootstrap_environment(trade_root=trade_root)

    assert report.vibe_trading_enable_scheduler is True
    assert get_env_config().agent_tuning.vibe_trading_enable_scheduler is True


def test_bootstrap_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trade_root = tmp_path / "trade"
    trade_root.mkdir()
    (trade_root / ".env").write_text("INDEX_RESEARCH_ENABLE_SCHEDULER=true\n", encoding="utf-8")
    monkeypatch.setattr("src.config.bootstrap.AGENT_DIR", tmp_path / "agent")

    first = bootstrap_environment(trade_root=trade_root)
    os.environ["INDEX_RESEARCH_ENABLE_SCHEDULER"] = "false"
    reset_env_config()
    second = bootstrap_environment(trade_root=trade_root)

    assert first.already_bootstrapped is False
    assert second.already_bootstrapped is True
    assert get_env_config().agent_tuning.index_research_enable_scheduler is False
