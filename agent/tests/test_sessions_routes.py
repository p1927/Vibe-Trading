"""TestClient coverage for src.api.sessions_routes.

Completes the agent-api-route-coverage-audit backlog item: this was the last
route module with zero TestClient coverage. Goal-subgroup routes
(create/get/update/status/evidence) and auto-title already have coverage in
test_goal_api.py; this file covers session CRUD/action routes and provenance,
which had none.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
from src.provenance.models import ProvenanceSource
from src.provenance.store import get_provenance_store
from src.session.models import Message
from src.session.service import SessionBusyError


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("VIBE_TRADING_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path / "runs"))
    monkeypatch.setattr(api_server, "_goal_store", None)
    monkeypatch.setattr(api_server, "_session_service", None)
    monkeypatch.setattr(api_server, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _create_session(client: TestClient, *, title: str = "") -> str:
    response = client.post("/sessions", json={"title": title})
    assert response.status_code == 201
    return response.json()["session_id"]


# ---------------------------------------------------------------------------
# create / list / get
# ---------------------------------------------------------------------------


def test_create_session_returns_full_shape(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/sessions", json={"title": "my session", "config": {"a": 1}})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "my session"
    assert body["status"]
    assert body["created_at"] and body["updated_at"]
    assert body["last_attempt_id"] is None


def test_list_sessions_hides_autonomous_kind_and_title(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    visible_id = _create_session(client, title="visible")
    hidden_by_kind_id = _create_session(client, title="hidden kind")
    hidden_by_title_id = _create_session(client, title="autonomous: run 1")

    service = api_server._get_session_service()
    session = service.store.get_session(hidden_by_kind_id)
    session.config = {"session_kind": "autonomous_agent"}
    service.store.update_session(session)

    response = client.get("/sessions")
    assert response.status_code == 200
    ids = {row["session_id"] for row in response.json()}
    assert visible_id in ids
    assert hidden_by_kind_id not in ids
    assert hidden_by_title_id not in ids


def test_list_sessions_respects_limit_bounds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/sessions", params={"limit": 0}).status_code == 422
    assert client.get("/sessions", params={"limit": 201}).status_code == 422
    assert client.get("/sessions", params={"limit": 5}).status_code == 200


def test_get_session_returns_404_for_unknown_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/sessions/does-not-exist")
    assert response.status_code == 404


def test_get_session_rejects_unsafe_path_param(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/sessions/bad.id")
    assert response.status_code == 400


def test_get_session_returns_the_created_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client, title="fetchable")
    response = client.get(f"/sessions/{sid}")
    assert response.status_code == 200
    assert response.json()["session_id"] == sid


# ---------------------------------------------------------------------------
# delete / update / auto-title status paths not covered by test_goal_api.py
# ---------------------------------------------------------------------------


def test_delete_session_removes_it_and_its_goals(tmp_path, monkeypatch):
    from src.api import sessions_routes

    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    client.post(f"/sessions/{sid}/goal", json={"objective": "check delete cleanup"})
    goal_store = sessions_routes._get_goal_store()
    assert goal_store.get_current_snapshot(sid) is not None

    response = client.delete(f"/sessions/{sid}")
    assert response.status_code == 200
    assert response.json() == {"status": "deleted", "session_id": sid}

    assert client.get(f"/sessions/{sid}").status_code == 404
    assert goal_store.get_current_snapshot(sid) is None


def test_delete_session_404s_for_unknown_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.delete("/sessions/does-not-exist").status_code == 404


def test_update_session_changes_title(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client, title="old title")

    response = client.patch(f"/sessions/{sid}", json={"title": "new title"})
    assert response.status_code == 200
    assert response.json() == {"status": "updated", "session_id": sid}

    fetched = client.get(f"/sessions/{sid}")
    assert fetched.json()["title"] == "new title"


def test_update_session_with_no_title_leaves_it_unchanged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client, title="stays the same")
    client.patch(f"/sessions/{sid}", json={})
    assert client.get(f"/sessions/{sid}").json()["title"] == "stays the same"


def test_update_session_404s_for_unknown_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.patch("/sessions/does-not-exist", json={"title": "x"}).status_code == 404


def test_auto_title_409s_with_no_user_message(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    response = client.post(f"/sessions/{sid}/title/auto")
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# messages / send / cancel
# ---------------------------------------------------------------------------


def test_get_messages_returns_stored_history(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    service = api_server._get_session_service()
    service.store.append_message(Message(session_id=sid, role="user", content="hello"))
    service.store.append_message(Message(session_id=sid, role="assistant", content="hi there"))

    response = client.get(f"/sessions/{sid}/messages")
    assert response.status_code == 200
    roles = [m["role"] for m in response.json()]
    assert roles == ["user", "assistant"]


def test_get_messages_respects_limit_bounds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    assert client.get(f"/sessions/{sid}/messages", params={"limit": 0}).status_code == 422
    assert client.get(f"/sessions/{sid}/messages", params={"limit": 1001}).status_code == 422


def test_send_message_returns_service_result(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    service = api_server._get_session_service()

    async def _fake_send_message(*, session_id, content, include_shell_tools):
        assert session_id == sid
        assert content == "do research"
        return {"message_id": "m1", "attempt_id": "a1"}

    monkeypatch.setattr(service, "send_message", _fake_send_message)

    response = client.post(f"/sessions/{sid}/messages", json={"content": "do research"})
    assert response.status_code == 200
    assert response.json() == {"message_id": "m1", "attempt_id": "a1"}


def test_send_message_rejects_empty_content(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    response = client.post(f"/sessions/{sid}/messages", json={"content": ""})
    assert response.status_code == 422


def test_send_message_returns_409_when_session_busy(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    service = api_server._get_session_service()

    async def _busy(*, session_id, content, include_shell_tools):
        raise SessionBusyError("already running")

    monkeypatch.setattr(service, "send_message", _busy)

    response = client.post(f"/sessions/{sid}/messages", json={"content": "again"})
    assert response.status_code == 409


def test_send_message_returns_404_for_missing_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _create_session(client)  # ensures the session service singleton is initialised
    response = client.post("/sessions/does-not-exist/messages", json={"content": "hi"})
    assert response.status_code == 404


def test_cancel_session_reports_no_active_loop_by_default(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    response = client.post(f"/sessions/{sid}/cancel")
    assert response.status_code == 200
    assert response.json() == {"status": "no_active_loop"}


def test_cancel_session_reports_cancelled_when_a_loop_is_active(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    service = api_server._get_session_service()
    monkeypatch.setattr(service, "cancel_current", lambda session_id: True)

    response = client.post(f"/sessions/{sid}/cancel")
    assert response.status_code == 200
    assert response.json() == {"status": "cancelled"}


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


def test_get_session_provenance_returns_empty_list_by_default(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    response = client.get(f"/sessions/{sid}/provenance")
    assert response.status_code == 200
    assert response.json() == {"sources": []}


def test_get_session_provenance_returns_recorded_sources(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)
    get_provenance_store().add(
        ProvenanceSource(
            ref_id="ref-1",
            session_id=sid,
            display_name="NVDA quote",
            summary="Latest NVDA price",
        )
    )

    response = client.get(f"/sessions/{sid}/provenance")
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["ref_id"] == "ref-1"
    assert sources[0]["display_name"] == "NVDA quote"


def test_get_session_provenance_404s_for_unknown_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/sessions/does-not-exist/provenance")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# events (SSE) — auth gate and existence check only; the real stream is
# scoped out the same way test_live_routes.py scoped out its runner
# streaming (see that file's own notes on TestClient + long-lived tasks).
# ---------------------------------------------------------------------------


def test_session_events_404s_for_unknown_session(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/sessions/does-not-exist/events")
    assert response.status_code == 404


def test_session_events_requires_ticket_or_bearer_once_api_key_is_configured(
    tmp_path, monkeypatch
):
    from src.config.accessor import reset_env_config

    client = _client(tmp_path, monkeypatch)
    sid = _create_session(client)

    monkeypatch.setenv("API_AUTH_KEY", "secret-key")
    monkeypatch.setattr(api_server, "_API_KEY", "secret-key")
    reset_env_config()
    try:
        response = client.get(f"/sessions/{sid}/events")
    finally:
        reset_env_config()
    assert response.status_code == 401
