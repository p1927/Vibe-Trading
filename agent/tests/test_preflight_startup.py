"""The API boot gate is local-only; network probes run after startup and report as health info.

Regression tests for .claude/backlog/items/2026-09-23-vibe-api-slow-startup.md (Trade repo): the
startup preflight's LLM ping / OKX / yfinance round trips and the ccxt import sat on the startup
path and, under load, pushed Vibe API start past the promotion health wait.
"""

from __future__ import annotations

import io
import logging

import pytest
from rich.console import Console

import src.preflight_startup as startup
import trade_integrations.http as trade_http
from src import preflight
from src.preflight import CheckResult
from tests.test_preflight import _configure_llm_preflight


def _quiet() -> Console:
    return Console(file=io.StringIO())


def test_boot_gate_runs_no_network_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    def network(*_a, **_k):
        raise AssertionError("boot gate made a network probe")

    for name in ("_check_okx", "_check_yfinance", "_check_ccxt", "check_prediction_ml"):
        monkeypatch.setattr(startup, name, network)
    pings: list[bool] = []

    def llm(ping: bool = True) -> CheckResult:
        pings.append(ping)
        return CheckResult("LLM (x)", "ready", "configured", "")

    monkeypatch.setattr(startup, "_check_llm_provider", llm)
    monkeypatch.setattr(startup, "check_environment", lambda: CheckResult("Environment", "ready", "ok", ""))
    monkeypatch.setattr(
        startup, "check_prediction_ml_installed", lambda: CheckResult("Prediction ML", "ready", "ok", "")
    )

    results = startup.run_boot_gate(_quiet())

    assert pings == [False]
    assert [r.name for r in results][:2] == ["Environment", "LLM (x)"]


def test_llm_check_without_ping_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_llm_preflight(monkeypatch)
    monkeypatch.setattr(trade_http, "get", lambda *a, **k: pytest.fail("pinged the LLM provider"))

    result = preflight._check_llm_provider(ping=False)

    assert result.status == "ready" and "reachability probed after startup" in result.message


def test_prediction_ml_installed_check_imports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from src.preflight_checks import check_prediction_ml_installed

    monkeypatch.setattr("importlib.util.find_spec", lambda name: None if name == "darts" else object())
    before = set(sys.modules)
    result = check_prediction_ml_installed()

    assert result.status == "error" and result.critical and "darts" in result.message
    assert not {"lightgbm", "xgboost", "darts", "shap", "sklearn"} & (set(sys.modules) - before)


def test_background_probes_log_failures_and_never_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(startup, "_check_llm_provider",
                        lambda: CheckResult("LLM (x)", "error", "SSLError", "agent cannot function", critical=True))
    monkeypatch.setattr(startup, "_check_okx",
                        lambda: CheckResult("OKX API", "error", "timeout", "crypto backtest unavailable"))
    monkeypatch.setattr(startup, "_check_yfinance", lambda: CheckResult("yfinance", "ready", "reachable", ""))
    monkeypatch.setattr(startup, "_check_ccxt", lambda: CheckResult("ccxt", "ready", "installed", ""))
    monkeypatch.setattr(startup, "check_prediction_ml", lambda: CheckResult("Prediction ML", "ready", "ok", ""))

    with caplog.at_level(logging.WARNING, logger=startup.__name__):
        thread = startup.start_background_probes(_quiet())
        thread.join(timeout=10)

    assert thread.daemon and not thread.is_alive()
    levels = {r.getMessage().split(":")[0]: r.levelno for r in caplog.records}
    assert levels["preflight probe LLM (x)"] == logging.ERROR
    assert levels["preflight probe OKX API"] == logging.WARNING
    assert "preflight probe yfinance" not in levels
