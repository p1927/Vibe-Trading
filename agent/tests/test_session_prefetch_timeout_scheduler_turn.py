"""A prefetch timeout must fail a scheduler turn, not run it without its context.

``[agent_context]`` is a scheduler turn's only copy of the agent's learning and progress. When the
prefetch times out the turn used to carry on with ``research_context=""``: the agent decided blind
and the session looked healthy. A chat turn still runs without the optional research block.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import src.session.service as service_module
from src.session.models import AttemptStatus
from src.session.service import SessionService
from src.session.events import EventBus
from src.session.store import SessionStore


class _DummyIndex:
    def index_session(self, *a, **k) -> None: ...
    def index_message(self, *a, **k) -> None: ...
    def delete_session(self, *a, **k) -> None: ...


class _TimeoutAsyncio:
    """``asyncio`` whose ``wait_for`` times out at once (the real clamp is >= 5 s)."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def wait_for(awaitable, timeout=None):
        awaitable.close()
        raise asyncio.TimeoutError


def _service(tmp_path: Path, monkeypatch) -> SessionService:
    monkeypatch.setattr("src.session.service.get_shared_index", lambda: _DummyIndex())
    monkeypatch.setattr(service_module, "asyncio", _TimeoutAsyncio())
    # An agent session's intent refresh can call the LLM (30 s+); not what this test is about.
    monkeypatch.setattr("src.session.service_hooks.maybe_refresh_agent_intent", lambda *a, **k: None)
    return SessionService(store=SessionStore(tmp_path / "sessions"), event_bus=EventBus(), runs_dir=tmp_path / "runs")


def _run(coro):
    return asyncio.run(coro)


def test_scheduler_turn_fails_when_prefetch_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch)
    ran: list[str] = []

    async def fake_run(self, session, attempt, **kwargs):
        ran.append(kwargs.get("research_context", "unset"))

    monkeypatch.setattr(SessionService, "_run_attempt", fake_run)
    session = service.create_session(title="agent", config={"session_kind": "autonomous_agent"})

    async def scenario():
        sent = await service.send_message(session.session_id, "# Autonomous agent turn", turn_kind="bootstrap")
        await real_asyncio.wait_for(service._active_tasks[session.session_id], timeout=10)
        return sent

    real_asyncio = asyncio
    sent = _run(scenario())

    attempt = service.store.get_attempt(session.session_id, sent["attempt_id"])
    assert attempt.status == AttemptStatus.FAILED
    assert "prefetch timed out" in (attempt.error or "")
    assert ran == [], "the turn must not run without its [agent_context]"
    assert session.session_id not in service._inflight


def test_chat_turn_still_runs_without_the_research_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path, monkeypatch)
    ran: list[str] = []

    async def fake_run(self, session, attempt, **kwargs):
        ran.append(kwargs.get("research_context", "unset"))
        self._active_tasks.pop(session.session_id, None)
        self._release_session(session.session_id)

    monkeypatch.setattr(SessionService, "_run_attempt", fake_run)
    session = service.create_session(title="chat")
    real_asyncio = asyncio

    async def scenario():
        await service.send_message(session.session_id, "hello")
        await real_asyncio.wait_for(service._active_tasks[session.session_id], timeout=10)

    _run(scenario())

    assert ran == [""]
