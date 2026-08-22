"""TestClient coverage for `attribution_routes.py` (`GET /runs/{run_id}/attribution`) —
previously untested.

Domain: `2026-08-21-agent-api-route-coverage-audit`. Confirmed correctly mounted via
`register_attribution_routes(app)` in `api_server.py`.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tmp = Path(tempfile.mkdtemp(prefix="attribution_routes_test_"))
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


@pytest.fixture
def runs_dir(client: TestClient) -> Path:
    return api_server.RUNS_DIR


def _write_equity_csv(run_dir: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    with (artifacts / "equity.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_unknown_run_returns_404(client: TestClient) -> None:
    response = client.get("/runs/does-not-exist/attribution")
    assert response.status_code == 404


@pytest.mark.parametrize("bad_run_id", ["../etc/passwd", "a/b", "a b", "..", ""])
def test_path_traversal_shaped_run_id_rejected(client: TestClient, bad_run_id: str) -> None:
    response = client.get(f"/runs/{bad_run_id}/attribution")
    assert response.status_code in (400, 404)


def test_run_dir_with_no_artifacts_reports_not_exists(client: TestClient, runs_dir: Path) -> None:
    (runs_dir / "run-empty").mkdir()
    response = client.get("/runs/run-empty/attribution")
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert body["factor"] is None
    assert body["brinson"] is None
    assert body["notes"]


def test_run_with_equity_csv_produces_attribution_payload(client: TestClient, runs_dir: Path) -> None:
    run_dir = runs_dir / "run-with-data"
    run_dir.mkdir()
    rows = [
        {
            "timestamp": f"2026-08-{day:02d}T00:00:00Z",
            "ret": str(0.001 * (day % 3 - 1)),
            "benchmark_equity": str(100.0 + day * 0.1),
        }
        for day in range(1, 21)
    ]
    _write_equity_csv(run_dir, rows, ["timestamp", "ret", "benchmark_equity"])

    response = client.get("/runs/run-with-data/attribution")
    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is True
    assert body["benchmark"] == {"ticker": None, "mode": "auto_equal_weight"}
    assert body["factor"] is not None


def test_equity_csv_missing_required_columns_reports_not_exists(
    client: TestClient, runs_dir: Path
) -> None:
    run_dir = runs_dir / "run-bad-csv"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "equity.csv").write_text("wrong,columns\n1,2\n", encoding="utf-8")

    response = client.get("/runs/run-bad-csv/attribution")
    assert response.status_code == 200
    assert response.json()["exists"] is False
