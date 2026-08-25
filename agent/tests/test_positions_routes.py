"""TestClient coverage for `positions_routes.py` (`/board/positions/*`) —
2026-08-25-live-positions-forecast-band-board.

`test_router_is_mounted_on_the_app` mirrors `test_board_routes.py`/`test_advisory_routes.py`'s
own regression test for the "router defined but never `include_router`'d" bug.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import trade_integrations.context.hub as hub_context
from trade_integrations.autonomous_agents.store import save_agent


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="positions_routes_test_"))
    monkeypatch.setattr(hub_context, "get_hub_dir", lambda: tmp)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _make_agent(agent_id: str = "aa_test1") -> dict:
    agent = {"id": agent_id, "status": "running", "symbols": ["NIFTY"]}
    save_agent(agent)
    return agent


def test_router_is_mounted_on_the_app(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_agent()
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.live_pop.compute_live_pop_for_agent",
        lambda agent_id: {"groups": [], "skipped": []},
    )
    response = client.get("/board/positions/aa_test1")

    assert response.status_code != 404, (
        "GET /board/positions/<id> returned 404 — positions_router is not mounted on the "
        "app. Check api_server.py includes `from src.api.positions_routes import "
        "positions_router` + `app.include_router(positions_router)`."
    )
    assert response.status_code == 200


def test_positions_for_unknown_agent_is_a_client_error(client: TestClient) -> None:
    response = client.get("/board/positions/does-not-exist")
    assert response.status_code == 404


def test_positions_returns_compute_live_pop_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_agent()
    canned = {
        "groups": [
            {
                "underlying": "NIFTY",
                "expiry_days": 5,
                "probability_of_profit": 0.62,
                "trajectory": [{"days_ahead": 0, "pnl_inr": -500.0}],
                "pnl_forecast_band": [
                    {"days_ahead": 0, "pnl_p10_inr": -500.0, "pnl_p50_inr": -500.0, "pnl_p90_inr": -500.0},
                ],
                "legs": [],
            }
        ],
        "skipped": [],
    }
    monkeypatch.setattr(
        "nautilus_openalgo_bridge.live_pop.compute_live_pop_for_agent",
        lambda agent_id: dict(canned),
    )

    response = client.get("/board/positions/aa_test1")

    assert response.status_code == 200
    assert response.json() == canned


def test_positions_translates_broker_failure_to_a_diagnosable_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`compute_live_pop_for_agent` has no internal fallback for a broker call failure
    (e.g. OpenAlgo unreachable) -- confirmed live: an unhandled exception here produces a
    bare 500 with none of the app's CORS/security headers (they're only applied on a
    response that returns normally through the middleware stack), which the browser then
    reports as an opaque CORS failure with no diagnosable message. This must come back as
    a real HTTPException instead."""
    _make_agent()

    def _raise(agent_id: str):
        raise RuntimeError("OpenAlgo request failed: connection refused")

    monkeypatch.setattr("nautilus_openalgo_bridge.live_pop.compute_live_pop_for_agent", _raise)

    response = client.get("/board/positions/aa_test1")

    assert response.status_code == 502
    assert "OpenAlgo request failed" in response.json()["detail"]
