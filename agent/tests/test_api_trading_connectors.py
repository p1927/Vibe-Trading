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


def test_default_profile_prefers_openalgo_when_configured(tmp_path, monkeypatch) -> None:
    from src.trading import profiles

    monkeypatch.setattr(profiles, "get_runtime_root", lambda: tmp_path)
    monkeypatch.setenv("OPENALGO_API_KEY", "test-openalgo-key")
    monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)

    assert profiles.infer_default_profile_id() == "openalgo-paper-sdk"
    assert profiles.load_selected_profile_id() == "openalgo-paper-sdk"


def test_default_profile_falls_back_to_alpaca_without_openalgo(tmp_path, monkeypatch) -> None:
    from src.trading import profiles

    monkeypatch.setattr(profiles, "get_runtime_root", lambda: tmp_path)
    monkeypatch.delenv("OPENALGO_API_KEY", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "alpaca-secret")

    assert profiles.infer_default_profile_id() == "alpaca-paper-sdk"
