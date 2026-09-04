"""Tests for the cross-service `GET /scheduler-registry` aggregation route.

Covers the Mechanism-B (stock_simulator) and Mechanism-C (openalgo) fan-out
and their graceful-degradation contract: a down/misconfigured source must
never fail the whole request, plus the Mechanism-C pause/resume routes, per
.claude/backlog/items/2026-08-29-unified-scheduler-registry.md.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _stub_unreachable(monkeypatch: pytest.MonkeyPatch, name: str, error: str) -> None:
    """Force one source's helper to report unreachable, regardless of what
    real config/secrets happen to be present in this process's environment.

    Both helpers are best-effort wrappers around real network calls
    (stock_simulator's control token, openalgo's API key) — asserting
    "unconfigured" behavior by relying on the ambient environment lacking
    those secrets is fragile: this repo's dev checkout often *does* have
    them configured for the real running stack, which made these tests
    flaky depending on what shell they ran from. Stub explicitly instead.
    """
    from src.api import scheduler_registry_routes

    monkeypatch.setattr(
        scheduler_registry_routes,
        name,
        lambda: ([], {"status": "unreachable", "error": error}),
    )


def test_scheduler_registry_reports_unreachable_without_a_configured_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """An unconfigured/unreachable stock_simulator — the route must still
    return 200 with an empty entries list and a clear source status, not a
    5xx."""
    _stub_unreachable(monkeypatch, "_stock_simulator_entries", "no control token configured")
    _stub_unreachable(monkeypatch, "_openalgo_entries", "OPENALGO_API_KEY not configured")

    response = client.get("/scheduler-registry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["entries"] == []
    assert body["sources"]["stock_simulator"]["status"] == "unreachable"


def test_scheduler_registry_surfaces_stock_simulator_entries_when_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from src.api import scheduler_registry_routes

    def fake_entries():
        return (
            [{"id": "B:recorder:us:index", "source": "stock_simulator", "section": "recorder:us"}],
            {"status": "ok"},
        )

    monkeypatch.setattr(scheduler_registry_routes, "_stock_simulator_entries", fake_entries)
    _stub_unreachable(monkeypatch, "_openalgo_entries", "OPENALGO_API_KEY not configured")

    response = client.get("/scheduler-registry")

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == [
        {"id": "B:recorder:us:index", "source": "stock_simulator", "section": "recorder:us"}
    ]
    assert body["sources"]["stock_simulator"]["status"] == "ok"


def test_scheduler_registry_degrades_gracefully_on_client_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from src.api import scheduler_registry_routes

    def fake_entries():
        return [], {"status": "unreachable", "error": "could not reach stock_simulator service"}

    monkeypatch.setattr(scheduler_registry_routes, "_stock_simulator_entries", fake_entries)
    _stub_unreachable(monkeypatch, "_openalgo_entries", "OPENALGO_API_KEY not configured")

    response = client.get("/scheduler-registry")

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["sources"]["stock_simulator"]["status"] == "unreachable"
    assert "could not reach" in body["sources"]["stock_simulator"]["error"]


def test_scheduler_registry_reports_unreachable_without_openalgo_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """An unconfigured/unreachable openalgo — Mechanism A/B entries must
    still render, not be taken down by it."""
    _stub_unreachable(monkeypatch, "_openalgo_entries", "OPENALGO_API_KEY not configured")

    response = client.get("/scheduler-registry")

    assert response.status_code == 200
    body = response.json()
    assert body["sources"]["openalgo"]["status"] == "unreachable"


def test_scheduler_registry_surfaces_openalgo_entries_when_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from src.api import scheduler_registry_routes

    def fake_entries():
        return (
            [{"id": "C:flow:wf_1", "source": "openalgo", "section": "flow"}],
            {"status": "ok"},
        )

    monkeypatch.setattr(scheduler_registry_routes, "_openalgo_entries", fake_entries)
    _stub_unreachable(monkeypatch, "_stock_simulator_entries", "no control token configured")

    response = client.get("/scheduler-registry")

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == [{"id": "C:flow:wf_1", "source": "openalgo", "section": "flow"}]
    assert body["sources"]["openalgo"]["status"] == "ok"


def test_scheduler_registry_combines_stock_simulator_and_openalgo_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from src.api import scheduler_registry_routes

    monkeypatch.setattr(
        scheduler_registry_routes,
        "_stock_simulator_entries",
        lambda: ([{"id": "B:recorder:us:index"}], {"status": "ok"}),
    )
    monkeypatch.setattr(
        scheduler_registry_routes,
        "_openalgo_entries",
        lambda: ([{"id": "C:flow:wf_1"}], {"status": "ok"}),
    )

    response = client.get("/scheduler-registry")

    body = response.json()
    assert body["entries"] == [{"id": "B:recorder:us:index"}, {"id": "C:flow:wf_1"}]


def test_scheduler_registry_openalgo_pause_calls_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.execution import openalgo_client as openalgo_client_module

    calls = []
    monkeypatch.setattr(
        openalgo_client_module.OpenAlgoClient,
        "__init__",
        lambda self: None,
    )
    monkeypatch.setattr(
        openalgo_client_module.OpenAlgoClient,
        "pause_scheduler_job",
        lambda self, source, job_id: calls.append((source, job_id)),
    )

    response = client.post("/scheduler-registry/openalgo/flow/wf_1/pause")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == [("flow", "wf_1")]


def test_scheduler_registry_openalgo_resume_calls_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.execution import openalgo_client as openalgo_client_module

    calls = []
    monkeypatch.setattr(
        openalgo_client_module.OpenAlgoClient,
        "__init__",
        lambda self: None,
    )
    monkeypatch.setattr(
        openalgo_client_module.OpenAlgoClient,
        "resume_scheduler_job",
        lambda self, source, job_id: calls.append((source, job_id)),
    )

    response = client.post("/scheduler-registry/openalgo/historify/sched_2/resume")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == [("historify", "sched_2")]


def test_scheduler_registry_openalgo_pause_unconfigured_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.execution import openalgo_client as openalgo_client_module

    def raise_unconfigured(self):
        raise RuntimeError("OPENALGO_API_KEY not configured")

    monkeypatch.setattr(openalgo_client_module.OpenAlgoClient, "__init__", raise_unconfigured)

    response = client.post("/scheduler-registry/openalgo/flow/wf_1/pause")

    assert response.status_code == 503


def test_scheduler_registry_openalgo_trigger_calls_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.execution import openalgo_client as openalgo_client_module

    calls = []
    monkeypatch.setattr(
        openalgo_client_module.OpenAlgoClient,
        "__init__",
        lambda self: None,
    )
    monkeypatch.setattr(
        openalgo_client_module.OpenAlgoClient,
        "trigger_scheduler_job_now",
        lambda self, source, job_id: calls.append((source, job_id)),
    )

    response = client.post("/scheduler-registry/openalgo/flow/wf_1/trigger")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == [("flow", "wf_1")]


def test_scheduler_registry_openalgo_trigger_paused_job_propagates_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.execution import openalgo_client as openalgo_client_module

    def raise_paused(self, source, job_id):
        raise RuntimeError("Job is paused — resume it before triggering")

    monkeypatch.setattr(openalgo_client_module.OpenAlgoClient, "__init__", lambda self: None)
    monkeypatch.setattr(
        openalgo_client_module.OpenAlgoClient, "trigger_scheduler_job_now", raise_paused
    )

    response = client.post("/scheduler-registry/openalgo/flow/wf_1/trigger")

    # RuntimeError is also what OpenAlgoClient()'s "not configured" guard
    # raises, so this route's existing except-order maps it to 503 too —
    # consistent with pause/resume's identical behavior above.
    assert response.status_code == 503
    assert "paused" in response.json()["detail"]


def test_scheduler_registry_stock_simulator_pause_calls_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.stock_simulator import client as stock_simulator_client_module

    calls = []

    class _FakeClient:
        def __init__(self) -> None:
            pass

        is_configured = True

        def pause_scheduler_job(self, job_id: str):
            calls.append(job_id)

    monkeypatch.setattr(stock_simulator_client_module, "StockSimulatorClient", _FakeClient)

    response = client.post("/scheduler-registry/stock-simulator/B:recorder:us:index/pause")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == ["B:recorder:us:index"]


def test_scheduler_registry_stock_simulator_resume_calls_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.stock_simulator import client as stock_simulator_client_module

    calls = []

    class _FakeClient:
        def __init__(self) -> None:
            pass

        is_configured = True

        def resume_scheduler_job(self, job_id: str):
            calls.append(job_id)

    monkeypatch.setattr(stock_simulator_client_module, "StockSimulatorClient", _FakeClient)

    response = client.post("/scheduler-registry/stock-simulator/B:recorder:us:index/resume")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == ["B:recorder:us:index"]


def test_scheduler_registry_stock_simulator_trigger_calls_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.stock_simulator import client as stock_simulator_client_module

    calls = []

    class _FakeClient:
        def __init__(self) -> None:
            pass

        is_configured = True

        def trigger_scheduler_job_now(self, job_id: str):
            calls.append(job_id)

    monkeypatch.setattr(stock_simulator_client_module, "StockSimulatorClient", _FakeClient)

    response = client.post("/scheduler-registry/stock-simulator/B:recorder:us:index/trigger-now")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert calls == ["B:recorder:us:index"]


def test_scheduler_registry_stock_simulator_pause_unconfigured_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.stock_simulator import client as stock_simulator_client_module

    class _FakeClient:
        def __init__(self) -> None:
            pass

        is_configured = False

    monkeypatch.setattr(stock_simulator_client_module, "StockSimulatorClient", _FakeClient)

    response = client.post("/scheduler-registry/stock-simulator/B:recorder:us:index/pause")

    assert response.status_code == 503


def test_scheduler_registry_stock_simulator_trigger_paused_job_propagates_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from trade_integrations.stock_simulator import client as stock_simulator_client_module
    from trade_integrations.stock_simulator.client import StockSimulatorClientError

    class _FakeClient:
        def __init__(self) -> None:
            pass

        is_configured = True

        def trigger_scheduler_job_now(self, job_id: str):
            raise StockSimulatorClientError(
                f"could not trigger {job_id!r} (unknown recorder/category, or currently paused)",
                status_code=409,
            )

    monkeypatch.setattr(stock_simulator_client_module, "StockSimulatorClient", _FakeClient)

    response = client.post("/scheduler-registry/stock-simulator/B:recorder:us:index/trigger-now")

    assert response.status_code == 409
    assert "paused" in response.json()["detail"]


def test_stock_simulator_entries_stamps_live_log_stream_url(monkeypatch: pytest.MonkeyPatch):
    """`scheduler_introspection.py`'s DTO leaves `live_log_stream_url` as None
    (it doesn't know its own externally-reachable host) — `_stock_simulator_entries`
    must fill it in via `StockSimulatorClient.log_stream_url`, deriving the
    recorder name from the entry's `section` (`"recorder:<name>"`)."""
    from src.api import scheduler_registry_routes
    from trade_integrations.stock_simulator import client as stock_simulator_client_module

    class _FakeClient:
        def __init__(self, timeout: float) -> None:
            pass

        is_configured = True

        def list_scheduler_registry(self):
            return {
                "entries": [
                    {
                        "id": "B:recorder:us:index",
                        "source": "stock_simulator",
                        "section": "recorder:us",
                        "supports_live_log": True,
                        "live_log_stream_url": None,
                    },
                    {
                        "id": "B:recorder:us:policy",
                        "source": "stock_simulator",
                        "section": "recorder:us",
                        "supports_live_log": True,
                        "live_log_stream_url": None,
                    },
                ]
            }

        def log_stream_url(self, recorder_name: str) -> str:
            return f"http://sim.example.com/scheduler-runs/{recorder_name}/stream?token=tok"

    monkeypatch.setattr(stock_simulator_client_module, "StockSimulatorClient", _FakeClient)

    entries, status = scheduler_registry_routes._stock_simulator_entries()

    assert status == {"status": "ok"}
    assert [e["live_log_stream_url"] for e in entries] == [
        "http://sim.example.com/scheduler-runs/us/stream?token=tok",
        "http://sim.example.com/scheduler-runs/us/stream?token=tok",
    ]


def test_stock_simulator_entries_leaves_url_alone_when_live_log_unsupported(
    monkeypatch: pytest.MonkeyPatch,
):
    from src.api import scheduler_registry_routes
    from trade_integrations.stock_simulator import client as stock_simulator_client_module

    class _FakeClient:
        def __init__(self, timeout: float) -> None:
            pass

        is_configured = True

        def list_scheduler_registry(self):
            return {
                "entries": [
                    {
                        "id": "B:recorder:us:index",
                        "source": "stock_simulator",
                        "section": "recorder:us",
                        "supports_live_log": False,
                        "live_log_stream_url": None,
                    }
                ]
            }

        def log_stream_url(self, recorder_name: str) -> str:
            raise AssertionError("must not be called when supports_live_log is False")

    monkeypatch.setattr(stock_simulator_client_module, "StockSimulatorClient", _FakeClient)

    entries, _status = scheduler_registry_routes._stock_simulator_entries()

    assert entries[0]["live_log_stream_url"] is None


def test_openalgo_entries_stamps_live_log_stream_url_for_flow_and_historify(
    monkeypatch: pytest.MonkeyPatch,
):
    """`scheduler_registry_service.py`'s DTO leaves `live_log_stream_url` as None
    (it can't embed its own apikey into a URL from inside a service module) —
    `_openalgo_entries` must fill it in via `OpenAlgoClient.log_stream_url`,
    deriving `source`/`job_id` from the entry's `section`/`id`
    ("C:<source>:<job_id>")."""
    from src.api import scheduler_registry_routes
    from trade_integrations.execution import openalgo_client as openalgo_client_module

    class _FakeClient:
        def __init__(self) -> None:
            pass

        def list_scheduler_registry(self):
            return [
                {
                    "id": "C:flow:flow_workflow_5",
                    "source": "openalgo",
                    "section": "flow",
                    "supports_live_log": True,
                    "live_log_stream_url": None,
                },
                {
                    "id": "C:strategy:job_1",
                    "source": "openalgo",
                    "section": "strategy",
                    "supports_live_log": False,
                    "live_log_stream_url": None,
                },
            ]

        def log_stream_url(self, source: str, job_id: str) -> str:
            return f"http://openalgo.example.com/api/v1/scheduler/registry/{source}/{job_id}/stream?apikey=tok"

    monkeypatch.setattr(openalgo_client_module, "OpenAlgoClient", _FakeClient)

    entries, status = scheduler_registry_routes._openalgo_entries()

    assert status == {"status": "ok"}
    assert entries[0]["live_log_stream_url"] == (
        "http://openalgo.example.com/api/v1/scheduler/registry/flow/flow_workflow_5/stream?apikey=tok"
    )
    # strategy has no live-log support yet — must stay untouched.
    assert entries[1]["live_log_stream_url"] is None
