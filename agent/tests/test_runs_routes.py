"""Route-level tests for GET /runs and GET /runs/{run_id}.

Previously untested at the HTTP layer (only the unrelated MCP-server
`list_runs`/`get_run_result` tools had coverage). Written alongside the fix
for [[2026-08-25-runs-routes-inconsistent-blocking-io-wrapping]]: both
routes now run their blocking filesystem work via `run_in_threadpool`
instead of directly on the event loop, matching the file's own established
convention. Covers both plain correctness (the refactor didn't break
anything) and the event-loop-starvation regression itself, using the
`with TestClient(...) as client:` pattern established in
[[2026-08-25-blocking-io-regression-tests-not-portal-shared]] — the plain
`client = TestClient(app)` form (no `__enter__`) gives every request its
own anyio portal, so a thread-based "does this block /health" test built
against it would pass regardless of whether the fix is present.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import runs_routes


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _make_run_dir(runs_dir: Path, run_id: str, *, prompt: str | None = None) -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    if prompt is not None:
        (run_dir / "req.json").write_text(json.dumps({"prompt": prompt}), encoding="utf-8")
    return run_dir


def test_list_runs_returns_known_runs(tmp_path: Path, client: TestClient) -> None:
    runs_dir = tmp_path / "runs"
    _make_run_dir(runs_dir, "run_20260101_120000", prompt="backtest AAPL")
    _make_run_dir(runs_dir, "run_20260102_120000", prompt="backtest SPY")

    response = client.get("/runs")

    assert response.status_code == 200
    body = response.json()
    assert {row["run_id"] for row in body} == {"run_20260101_120000", "run_20260102_120000"}
    prompts = {row["run_id"]: row["prompt"] for row in body}
    assert prompts["run_20260101_120000"] == "backtest AAPL"
    assert prompts["run_20260102_120000"] == "backtest SPY"


def test_list_runs_empty_dir_returns_empty_list(tmp_path: Path, client: TestClient) -> None:
    response = client.get("/runs")

    assert response.status_code == 200
    assert response.json() == []


def test_get_run_result_404s_for_unknown_run(client: TestClient) -> None:
    response = client.get("/runs/does-not-exist")

    assert response.status_code == 404


def test_get_run_result_returns_run_details(tmp_path: Path, client: TestClient) -> None:
    runs_dir = tmp_path / "runs"
    run_dir = _make_run_dir(runs_dir, "run_20260101_120000", prompt="backtest AAPL")
    (run_dir / "state.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")

    response = client.get("/runs/run_20260101_120000")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run_20260101_120000"
    assert body["status"] == "success"
    assert body["prompt"] == "backtest AAPL"


def test_list_runs_does_not_block_health_while_scanning_is_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    (tmp_path / "runs").mkdir()

    real_list_runs_sync = runs_routes._list_runs_sync

    def _slow_list_runs_sync(*args, **kwargs):
        time.sleep(1.5)
        return real_list_runs_sync(*args, **kwargs)

    monkeypatch.setattr(runs_routes, "_list_runs_sync", _slow_list_runs_sync)

    with TestClient(api_server.app, client=("127.0.0.1", 50000)) as client:
        results: dict[str, object] = {}

        def _call_list_runs():
            results["runs"] = client.get("/runs")

        thread = threading.Thread(target=_call_list_runs)
        thread.start()
        time.sleep(0.3)  # let the slow request actually start first

        started = time.monotonic()
        health_resp = client.get("/health")
        health_elapsed = time.monotonic() - started

        thread.join(timeout=5)

    assert health_resp.status_code == 200
    assert health_elapsed < 1.0
    assert results["runs"].status_code == 200
    assert results["runs"].json() == []


def test_get_run_result_does_not_block_health_while_building_is_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(api_server, "_API_KEY", "")
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    _make_run_dir(tmp_path / "runs", "run_20260101_120000")

    real_build_response = runs_routes._build_response_from_run_dir

    def _slow_build_response(*args, **kwargs):
        time.sleep(1.5)
        return real_build_response(*args, **kwargs)

    monkeypatch.setattr(runs_routes, "_build_response_from_run_dir", _slow_build_response)

    with TestClient(api_server.app, client=("127.0.0.1", 50000)) as client:
        results: dict[str, object] = {}

        def _call_get_run():
            results["run"] = client.get("/runs/run_20260101_120000")

        thread = threading.Thread(target=_call_get_run)
        thread.start()
        time.sleep(0.3)  # let the slow request actually start first

        started = time.monotonic()
        health_resp = client.get("/health")
        health_elapsed = time.monotonic() - started

        thread.join(timeout=5)

    assert health_resp.status_code == 200
    assert health_elapsed < 1.0
    assert results["run"].status_code == 200
    assert results["run"].json()["run_id"] == "run_20260101_120000"
