"""API tests for trading connector profile routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import api_server


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path), raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_list_trading_connectors_includes_openalgo(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/trading/connectors")
    assert response.status_code == 200
    body = response.json()
    ids = {p["id"] for p in body["profiles"]}
    assert "openalgo-paper-sdk" in ids
    assert "openalgo-live-sdk-readonly" in ids
    assert "openalgo-paper-trade" in ids


def test_select_trading_connector_persists_choice(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.post("/trading/connectors/select", json={"profile_id": "openalgo-paper-sdk"})
    assert response.status_code == 200
    assert response.json()["selected_profile"] == "openalgo-paper-sdk"

    listed = client.get("/trading/connectors").json()
    assert listed["selected_profile"] == "openalgo-paper-sdk"
    selected = [p for p in listed["profiles"] if p["selected"]]
    assert len(selected) == 1
    assert selected[0]["id"] == "openalgo-paper-sdk"
