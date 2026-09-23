"""The API lifespan refuses to start when a self-declared critical preflight check fails.

Regression tests for .claude/backlog/items/2026-09-07-preflight-critical-not-blocking.md: the
banner printed "Critical check failed" and the server went on to log "Application startup
complete", because `_run_startup_preflight` discarded `run_preflight`'s results.
"""

from __future__ import annotations

import asyncio

import pytest

from src.preflight import CheckResult, critical_failures

READY = CheckResult("Environment", "ready", "ok", "")
LLM_DOWN = CheckResult("LLM (minimax)", "error", "SSLError", "agent cannot function", critical=True)
TUSHARE_OFF = CheckResult("Tushare", "not_configured", "no token", "CN data degraded")


def _stub_startup(monkeypatch: pytest.MonkeyPatch, results: list[CheckResult]) -> list[str]:
    started: list[str] = []
    monkeypatch.setattr("src.config.migrate.migrate_legacy_state", lambda: None)
    monkeypatch.setattr("src.preflight_startup.run_boot_gate", lambda console: results)
    monkeypatch.setattr(
        "src.preflight_startup.start_background_probes", lambda console: started.append("probes")
    )
    monkeypatch.setattr(
        "src.api.scheduled_routes._start_scheduled_research_executor",
        lambda: started.append("executor"),
    )
    monkeypatch.setattr("src.trade.job_watchdog.start_job_watchdog", lambda: started.append("job_watchdog"))
    monkeypatch.setattr(
        "src.api.loop_stall_watchdog.start_loop_stall_watchdog", lambda: started.append("stall_watchdog")
    )
    monkeypatch.setattr(
        "src.config.accessor.get_env_config",
        lambda: type(
            "Cfg",
            (),
            {"agent_tuning": type("Tuning", (), {"vibe_trading_channels_auto_start": False})()},
        )(),
    )
    return started


def test_critical_failure_aborts_startup_before_anything_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server

    started = _stub_startup(monkeypatch, [READY, LLM_DOWN, TUSHARE_OFF])
    with pytest.raises(RuntimeError, match=r"critical preflight check\(s\) failed: LLM \(minimax\)"):
        asyncio.run(api_server._run_startup_preflight())
    assert started == []


def test_critical_failure_fails_the_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server

    _stub_startup(monkeypatch, [READY, LLM_DOWN])
    served: list[str] = []

    async def scenario() -> None:
        async with api_server._lifespan(api_server.app):
            served.append("serving")

    with pytest.raises(RuntimeError, match="refusing to start"):
        asyncio.run(scenario())
    assert served == []


def test_non_critical_failure_still_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server

    started = _stub_startup(monkeypatch, [READY, TUSHARE_OFF])
    asyncio.run(api_server._run_startup_preflight())
    assert "executor" in started


def test_critical_failures_names_only_critical_checks_that_are_not_ready() -> None:
    ready_critical = CheckResult("LLM (minimax)", "ready", "ok", "", critical=True)
    assert critical_failures([READY, ready_critical, TUSHARE_OFF]) == []
    assert critical_failures([READY, LLM_DOWN, TUSHARE_OFF]) == ["LLM (minimax)"]
