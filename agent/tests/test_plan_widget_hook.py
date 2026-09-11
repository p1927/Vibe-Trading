"""The plan-widget binder must not fail silently (Trade backlog 2026-09-07-plan-widget-never-bound).

A widget that is emitted but never bound to its autonomous agent leaves the agent with no plan
pointer. Each reason the binder gives up must be logged at WARNING, and internal errors must
propagate rather than be swallowed at debug level.
"""

from __future__ import annotations

import logging
import sys
import types

import pytest

from src.trade import plan_widget_hook
from src.trade.plan_widget_hook import _resolve_agent_id

LOGGER = "src.trade.plan_widget_hook"


class _Session:
    def __init__(self, config: dict) -> None:
        self.config = config


class _Svc:
    def __init__(self, sessions: dict) -> None:
        self._sessions = sessions

    def get_session(self, session_id: str):
        return self._sessions.get(session_id)


def _install_host(monkeypatch: pytest.MonkeyPatch, svc) -> None:
    host = types.ModuleType("api_server")
    host._get_session_service = lambda: svc
    monkeypatch.setitem(sys.modules, "api_server", host)
    monkeypatch.delitem(sys.modules, "agent.api_server", raising=False)


def test_resolves_the_autonomous_agent(monkeypatch: pytest.MonkeyPatch):
    cfg = {"session_kind": "autonomous_agent", "autonomous_agent_id": "aa_1"}
    _install_host(monkeypatch, _Svc({"s1": _Session(cfg)}))
    assert _resolve_agent_id("s1") == "aa_1"


def test_a_non_autonomous_session_is_quiet(monkeypatch: pytest.MonkeyPatch, caplog):
    _install_host(monkeypatch, _Svc({"s1": _Session({"session_kind": "chat"})}))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _resolve_agent_id("s1") == ""
    assert caplog.records == []


def test_missing_host_module_is_logged(monkeypatch: pytest.MonkeyPatch, caplog):
    monkeypatch.delitem(sys.modules, "api_server", raising=False)
    monkeypatch.delitem(sys.modules, "agent.api_server", raising=False)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _resolve_agent_id("s1") == ""
    assert "api_server module not loaded" in caplog.text


def test_missing_session_service_is_logged(monkeypatch: pytest.MonkeyPatch, caplog):
    _install_host(monkeypatch, None)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _resolve_agent_id("s1") == ""
    assert "no session service" in caplog.text


def test_missing_session_is_logged(monkeypatch: pytest.MonkeyPatch, caplog):
    _install_host(monkeypatch, _Svc({}))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _resolve_agent_id("s1") == ""
    assert "session not found" in caplog.text


def test_autonomous_session_without_agent_id_is_logged(monkeypatch: pytest.MonkeyPatch, caplog):
    _install_host(monkeypatch, _Svc({"s1": _Session({"session_kind": "autonomous_agent"})}))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _resolve_agent_id("s1") == ""
    assert "no autonomous_agent_id" in caplog.text


def test_an_internal_error_propagates(monkeypatch: pytest.MonkeyPatch):
    class _Broken:
        def get_session(self, session_id: str):
            raise RuntimeError("session store unreadable")

    _install_host(monkeypatch, _Broken())
    with pytest.raises(RuntimeError, match="session store unreadable"):
        _resolve_agent_id("s1")


def test_widget_without_id_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        plan_widget_hook.notify_trade_plan_widget("s1", {"type": "trade_plan.widget"})
    assert "widget has no widget_id" in caplog.text
