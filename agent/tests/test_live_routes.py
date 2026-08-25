"""TestClient coverage for `live_routes.py` (`/live/*`, `/mandate/*`) — previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Every endpoint here requires `require_auth`
(full API-key-or-loopback auth) — the `client=("127.0.0.1", 50000)` + `_API_KEY=""` combination
already established in `test_qveris_routes.py`/`test_trade_routes_replay.py` satisfies it via the
loopback bypass, same as elsewhere in this backlog.

`runtime_root` isolates halt/mandate state to a tmp dir, same pattern as
`test_agent_loop_order_guard_integration.py`/`test_agent_loop_order_guard_scenarios.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import src.live.paths as paths
import src.trading.service as trading_service


@pytest.fixture
def runtime_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def client(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_status_with_no_state_is_not_halted(client: TestClient) -> None:
    response = client.get("/live/status")

    assert response.status_code == 200
    body = response.json()
    assert body["global_halted"] is False


def test_halt_then_status_reflects_halted_then_resume_clears_it(client: TestClient) -> None:
    halted = client.post("/live/halt", json={"reason": "test halt"})
    assert halted.status_code == 200
    assert halted.json()["halted"] is True

    status = client.get("/live/status")
    assert status.json()["global_halted"] is True

    resumed = client.post("/live/resume", json={})
    assert resumed.status_code == 200
    assert resumed.json()["halted"] is False

    status_after = client.get("/live/status")
    assert status_after.json()["global_halted"] is False


def test_authorize_is_a_pure_read_no_state_change(client: TestClient) -> None:
    response = client.post("/live/authorize", json={"broker": "robinhood"})

    assert response.status_code == 200
    # No halt/mandate state was touched by a pure discovery endpoint.
    assert client.get("/live/status").json()["global_halted"] is False


def test_mandate_commit_requires_consent_ack(client: TestClient) -> None:
    response = client.post(
        "/mandate/commit",
        json={
            "broker": "robinhood",
            # Must match CommitMandateRequest's pattern (mp_ + 32 hex chars) to pass pydantic
            # validation and actually reach the handler's own consent_ack check below.
            "proposal_id": "mp_" + "0" * 32,
            "selected_ordinal": 1,
            "consent_ack": False,
            "account_ref": "acct",
            "session_id": None,
        },
    )

    assert response.status_code == 400
    assert "consent_ack" in response.json()["detail"]


def test_mandate_commit_does_not_block_health_while_ceilings_fetch_is_slow(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for [[2026-08-25-audit-async-routes-for-blocking-io]]:
    _fetch_broker_ceilings makes a real (synchronous, thread-joined) MCP
    round-trip to the broker. Before this fix it ran directly on the event
    loop inside commit_mandate_endpoint, so a slow broker response would
    have stalled every other request, including /health — the same failure
    mode as the original /correlation bug.

    Uses ``with TestClient(...) as client`` (not the module's plain ``client``
    fixture) so requests share one persistent anyio portal/event loop —
    without ``__enter__``, ``TestClient`` opens a fresh portal per request
    (``starlette.testclient.TestClient._portal_factory``), so two "concurrent"
    calls from different threads land on two independent event loops and a
    blocking call in one can never be observed stalling the other. Confirmed
    empirically: the plain ``client`` fixture's non-context-managed pattern
    does not detect this class of regression at all (passes identically
    whether or not the event-loop-starvation fix is present) — see
    [[2026-08-25-blocking-io-regression-tests-not-portal-shared]]."""
    import threading
    import time

    monkeypatch.setattr(api_server, "_API_KEY", "")

    def _slow_ceilings(broker: str):
        time.sleep(1.5)
        return None

    monkeypatch.setattr(api_server, "_fetch_broker_ceilings", _slow_ceilings)

    with TestClient(api_server.app, client=("127.0.0.1", 50000)) as client:
        results: dict[str, object] = {}

        def _call_commit():
            results["commit"] = client.post(
                "/mandate/commit",
                json={
                    "broker": "robinhood",
                    "proposal_id": "mp_" + "0" * 32,
                    "selected_ordinal": 1,
                    "consent_ack": True,
                    "account_ref": "acct",
                    "session_id": None,
                },
            )

        thread = threading.Thread(target=_call_commit)
        thread.start()
        time.sleep(0.3)  # let the slow commit request actually start first

        started = time.monotonic()
        health_resp = client.get("/health")
        health_elapsed = time.monotonic() - started

        thread.join(timeout=5)

    assert health_resp.status_code == 200
    assert health_elapsed < 1.0
    # The commit itself still completes (400 here since there's no real
    # proposal fixture — what matters is /health wasn't stuck behind it).
    assert results["commit"].status_code in (200, 400)


def test_verify_connector_does_not_block_health_while_status_check_is_slow(
    runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for [[2026-08-25-audit-async-routes-for-blocking-io]]:
    verify_connector_endpoint always calls _check_connector_status with
    force=True (bypassing the status cache), which reaches a real broker SDK
    connectivity check. Before this fix it ran directly on the event loop, so
    a slow/hung broker check would have stalled every other request,
    including /health. See the sibling mandate-commit test above for why this
    uses ``with TestClient(...) as client`` instead of the module's plain
    ``client`` fixture."""
    import threading
    import time

    import src.trading.profiles as profiles

    monkeypatch.setattr(api_server, "_API_KEY", "")

    fake_profile = profiles.TradingProfile(
        id="fake-broker_sdk-readonly",
        connector="fake",
        label="Fake",
        environment="live",
        transport="broker_sdk",
        capabilities=(),
        readonly=True,
        config={},
        notes=None,
    )
    monkeypatch.setattr(profiles, "profile_by_id", lambda profile_id: fake_profile)

    def _slow_check(profile_id: str, force: bool = False):
        time.sleep(1.5)
        return {"profile_id": profile_id, "status": "ok"}

    monkeypatch.setattr(api_server, "_check_connector_status", _slow_check)

    with TestClient(api_server.app, client=("127.0.0.1", 50000)) as client:
        results: dict[str, object] = {}

        def _call_verify():
            results["verify"] = client.post("/live/connectors/fake-broker_sdk-readonly/verify")

        thread = threading.Thread(target=_call_verify)
        thread.start()
        time.sleep(0.3)  # let the slow verify request actually start first

        started = time.monotonic()
        health_resp = client.get("/health")
        health_elapsed = time.monotonic() - started

        thread.join(timeout=5)

    assert health_resp.status_code == 200
    assert health_elapsed < 1.0
    assert results["verify"].status_code == 200
    assert results["verify"].json()["profile_id"] == "fake-broker_sdk-readonly"


def test_runner_start_rejects_unsupported_broker(client: TestClient) -> None:
    response = client.post("/live/runner/start", json={"broker": "not-a-real-broker"})

    assert response.status_code == 400


def test_runner_start_without_mandate_returns_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trading_service, "broker_supports_live_runner", lambda broker: True)

    response = client.post("/live/runner/start", json={"broker": "robinhood"})

    assert response.status_code == 409
    assert "mandate" in response.json()["detail"]


def test_runner_start_succeeds_past_all_gates_with_construction_faked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With broker support, an unexpired mandate, and no halt all satisfied (and the runner's
    own construction faked out — LiveRunner internals are already covered end-to-end by
    test_live_runner_order_authorization_state_machine.py), the route reports a fresh start.

    Doesn't assert on the background task surviving past this request: `TestClient`'s sync
    request handling runs each call in its own event-loop lifecycle, so an `asyncio.Task`
    scheduled via `ensure_future` inside one request doesn't reliably keep running once that
    request's response is returned — a real behavior difference from a persistent uvicorn
    process this test harness can't faithfully reproduce without diverging from the house
    TestClient pattern. `stop()`'s own logic (pop from `_runner_tasks`, "was_running" when
    nothing is tracked) IS covered directly below without depending on that persistence."""
    from types import SimpleNamespace

    monkeypatch.setattr(trading_service, "broker_supports_live_runner", lambda broker: True)
    monkeypatch.setattr(
        api_server,
        "_active_mandate_state",
        lambda broker: SimpleNamespace(expired=False),
    )
    monkeypatch.setattr(api_server, "_build_live_runner", lambda broker: object())

    async def fake_drive_runner(runner: object) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(api_server, "_drive_runner", fake_drive_runner)

    start = client.post("/live/runner/start", json={"broker": "robinhood"})

    assert start.status_code == 200
    assert start.json() == {"broker": "robinhood", "started": True, "already_running": False}


def test_runner_stop_with_nothing_tracked_reports_was_running_false(client: TestClient) -> None:
    stop = client.post("/live/runner/stop", json={"broker": "robinhood"})

    assert stop.status_code == 200
    assert stop.json() == {"broker": "robinhood", "stopped": False, "was_running": False}


def test_endpoints_require_auth_when_api_key_configured(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With an API key configured, an unauthenticated non-loopback-looking request must be
    rejected — the negative control proving `require_auth` is actually wired on these routes,
    not just trivially passing because every test in this file happens to use the loopback
    bypass."""
    monkeypatch.setattr(api_server, "_API_KEY", "a-real-key")
    client = TestClient(api_server.app, client=("203.0.113.5", 12345))

    response = client.get("/live/status")

    assert response.status_code in (401, 403)
