"""TestClient coverage for `trading_connector_routes.py` (`/trading/connectors/*`) —
previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Router is mounted via
`register_trade_and_watch_routes()` (`trade_routes.py`), not directly in `api_server.py` —
confirmed present (no missing-mount bug here, unlike `autonomous_routes`/`observability_routes`
found earlier in this audit).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import src.trading.service as trading_service


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="trading_connector_routes_test_"))
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp))
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_list_connectors_returns_builtin_profiles(client: TestClient) -> None:
    response = client.get("/trading/connectors")
    assert response.status_code == 200
    body = response.json()
    assert body["profiles"]
    assert body["selected_profile"]
    ids = {p["id"] for p in body["profiles"]}
    assert body["selected_profile"] in ids


def test_exactly_one_profile_is_marked_selected(client: TestClient) -> None:
    body = client.get("/trading/connectors").json()
    selected_flags = [p["selected"] for p in body["profiles"] if p["id"] == body["selected_profile"]]
    assert selected_flags == [True]


def test_select_unknown_profile_returns_404(client: TestClient) -> None:
    response = client.post("/trading/connectors/select", json={"profile_id": "not-a-real-profile"})
    assert response.status_code == 404


def test_select_known_profile_persists_and_is_reflected_in_list(client: TestClient) -> None:
    profiles = client.get("/trading/connectors").json()["profiles"]
    other = next(p for p in profiles if not p["selected"])

    select = client.post("/trading/connectors/select", json={"profile_id": other["id"]})
    assert select.status_code == 200
    assert select.json() == {"status": "ok", "selected_profile": other["id"]}

    after = client.get("/trading/connectors").json()
    assert after["selected_profile"] == other["id"]


def test_select_rejects_empty_profile_id(client: TestClient) -> None:
    response = client.post("/trading/connectors/select", json={"profile_id": ""})
    assert response.status_code == 422


def test_check_unknown_profile_returns_404(client: TestClient) -> None:
    response = client.get("/trading/connectors/not-a-real-profile/check")
    assert response.status_code == 404


def test_check_known_profile_delegates_to_check_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = client.get("/trading/connectors").json()["profiles"]
    target = profiles[0]["id"]

    called_with = {}

    def fake_check_connection(profile_id: str, **overrides):
        called_with["profile_id"] = profile_id
        return {"connected": False, "profile_id": profile_id, "detail": "stubbed for test"}

    monkeypatch.setattr(trading_service, "check_connection", fake_check_connection)

    response = client.get(f"/trading/connectors/{target}/check")
    assert response.status_code == 200
    assert response.json()["connected"] is False
    assert called_with["profile_id"] == target
