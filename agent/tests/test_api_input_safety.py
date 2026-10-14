"""Path-input safety and error handling of the API routes (review vibe-api-routes-unread).

A ticker / recipe / run_id / path parameter was joined onto a hub or artifact directory (and,
unquoted, onto the stock_simulator URL the client calls with its control token) without a
check, so ``../`` reached ``mkdir``/``read_text``/``write_text`` outside the store. These tests
drive the real app (no mocks of the routes) against the conftest scratch hub.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import helpers, live_routes, runs_routes, scheduled_routes
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub_dir = tmp_path / "reports" / "hub"
    hub_dir.mkdir(parents=True)
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub_dir))
    return hub_dir


def _escaped(tmp_path: Path) -> list[str]:
    """Anything created next to the hub (the traversal target) instead of inside it."""
    return sorted(p.name for p in (tmp_path / "reports").iterdir() if p.name != "hub")


@pytest.mark.parametrize(
    "ticker",
    ["../escape", "..\\escape", "a/../../escape", "..", ".", "a\x00b", "x\ny"],
)
def test_ticker_query_cannot_leave_the_hub(client: TestClient, hub: Path, tmp_path: Path, ticker: str) -> None:
    for path in (
        "/trade/index-prediction/news-scenarios/recent",
        "/trade/index-prediction/forecast-engine-latest",
        "/trade/index-prediction/snapshots",
    ):
        response = client.get(path, params={"ticker": ticker})
        assert response.status_code == 400, (path, ticker, response.text)
    assert _escaped(tmp_path) == []


def test_entity_id_and_body_ticker_cannot_leave_the_hub(client: TestClient, hub: Path, tmp_path: Path) -> None:
    assert client.get("/trade/capture-registry", params={"entity_id": "../x"}).status_code == 400
    assert client.post("/trade/index-prediction/run/start", json={"ticker": "../x"}).status_code == 400
    assert _escaped(tmp_path) == []


def test_recipe_and_run_id_cannot_pick_the_artifact_file(client: TestClient, hub: Path) -> None:
    for params in ({"recipe": "../../../etc/passwd"}, {"recipe": "ok", "run_id": "../../x"}):
        for path in (
            "/trade/index-prediction/forecast-engine-evaluation",
            "/trade/index-prediction/forecast-engine-run-detail",
            "/trade/index-prediction/forecast-engine-runs",
        ):
            assert client.get(path, params=params).status_code == 400, (path, params)


def test_forecast_engine_run_start_rejects_path_shaped_fields(client: TestClient, hub: Path) -> None:
    good = {"ticker": "NIFTY", "recipe": "r", "start": "2026-01-01", "end": "2026-02-01"}
    for override in ({"start": "../../x"}, {"end": "2026-01-01/../../x"}, {"recipe": "../r"}, {"ticker": "a/b"}):
        response = client.post("/trade/index-prediction/forecast-engine-run/start", json={**good, **override})
        assert response.status_code == 422, (override, response.text)


def test_dot_segment_path_params_never_reach_the_simulator_url(client: TestClient) -> None:
    # %2e%2e decodes to ".." before routing; the client would have put it, unquoted, in /history/{country}/...
    with patch("src.api.trade_routes._run_control") as run_control:
        for path in (
            "/trade/markets/%2e%2e/factors/gdp",
            "/trade/markets/in/index/%2e%2e",
            "/trade/markets/global_macro/%2e%2e",
            "/trade/markets/recording/%2e%2e/stop",
        ):
            response = client.request("POST" if path.endswith("/stop") else "GET", path)
            assert response.status_code == 400, (path, response.text)
        run_control.assert_not_called()


def test_names_that_are_not_paths_still_work(client: TestClient, hub: Path, tmp_path: Path) -> None:
    # Control: "M&M" and "NIFTY 50" are legitimate tickers/index names and must not be refused.
    for ticker in ("M&M", "NIFTY 50", "BAJAJ-AUTO", "^NSEI"):
        response = client.get("/trade/index-prediction/news-scenarios/recent", params={"ticker": ticker})
        assert response.status_code == 200, (ticker, response.text)
    assert _escaped(tmp_path) == []


def test_unsafe_component_rules() -> None:
    assert all(helpers.is_unsafe_path_component(v) for v in ("..", ".", "a/b", "a\\b", "a\x00b", "a\nb"))
    assert not any(helpers.is_unsafe_path_component(v) for v in ("NIFTY", "M&M", "NIFTY 50", "a..b", "2026-01-01"))


def test_error_detail_hides_paths_and_keys_but_keeps_the_diagnosis() -> None:
    exc = RuntimeError(
        f"could not read {Path.home()}/secret/store.json via https://x.test/q?api_key=SECRETVALUE123 failed"
    )
    detail = helpers.redacted_error_detail(exc)
    assert "SECRETVALUE123" not in detail
    assert str(Path.home()) not in detail
    assert "could not read" in detail and len(detail) <= 300


def test_error_detail_is_bounded_and_never_empty() -> None:
    assert helpers.redacted_error_detail(RuntimeError("x" * 5000)) == "x" * 300
    assert helpers.redacted_error_detail(ValueError()) == "ValueError"


# ---- execute-basket: a bridge agent's widget never unlocks a free-form basket -------------------


def test_execute_basket_refuses_agent_widget_even_with_explicit_orders(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    widget = {"underlying": "NIFTY", "autonomous_agent_id": "ag_1", "implementation_steps": []}
    monkeypatch.setattr("src.api.trade_routes.load_trade_widget", lambda _wid: widget)
    with patch("trade_integrations.execution.enforce.is_bridge_autonomous_agent", return_value=True):
        response = client.post(
            "/trade/execute-basket",
            json={"widget_id": "tp_NIFTY_abc123def456", "orders": [{"symbol": "NIFTY", "action": "BUY"}]},
        )
    assert response.status_code == 403


def test_execute_basket_human_widget_still_reaches_the_broker_stage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Control: a human's own widget gets past the agent check; stop at the
    # unconfigured OpenAlgo key (503) so nothing is sent anywhere.
    widget = {"underlying": "NIFTY", "autonomous_agent_id": None, "implementation_steps": []}
    monkeypatch.setattr("src.api.trade_routes.load_trade_widget", lambda _wid: widget)
    monkeypatch.setenv("OPENALGO_API_KEY", "")
    response = client.post(
        "/trade/execute-basket",
        json={"widget_id": "tp_NIFTY_abc123def456", "orders": [{"symbol": "NIFTY", "action": "BUY"}]},
    )
    assert response.status_code == 503


def test_execute_basket_free_form_orders_without_a_widget_are_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.api.trade_routes.load_trade_widget", lambda _wid: None)
    assert client.post("/trade/execute-basket", json={"orders": [{"symbol": "NIFTY", "action": "BUY"}]}).status_code == 400
    response = client.post(
        "/trade/execute-basket",
        json={"widget_id": "tp_NIFTY_abc123def456", "orders": [{"symbol": "NIFTY", "action": "BUY"}]},
    )
    assert response.status_code == 404


def test_execute_basket_unknown_widget_without_orders_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.api.trade_routes.load_trade_widget", lambda _wid: None)
    assert client.post("/trade/execute-basket", json={"widget_id": "tp_NIFTY_abc123def456"}).status_code == 404


# ---- run artifacts: a corrupt file is loud, an absent one is quiet ------------------------------


def test_corrupt_run_artifact_is_logged_not_silently_absent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bad = tmp_path / "state.json"
    bad.write_text("{not json", encoding="utf-8")
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    csv_bad = tmp_path / "bad.csv"
    csv_bad.write_bytes(b"\xff\xfe\x00bad")
    with caplog.at_level(logging.WARNING, logger=runs_routes.logger.name):
        assert runs_routes._load_json_file(bad) is None
        assert runs_routes._load_json_file(listy) is None
        assert runs_routes._load_csv_to_dict(csv_bad) == []
    assert [r.levelno for r in caplog.records].count(logging.WARNING) == 3


def test_absent_run_artifact_is_quiet(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=runs_routes.logger.name):
        assert runs_routes._load_json_file(tmp_path / "nope.json") is None
        assert runs_routes._load_csv_to_dict(tmp_path / "nope.csv") == []
    assert caplog.records == []


def test_run_code_refuses_a_symlinked_source(tmp_path: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")
    code = tmp_path / "runs" / "r1" / "code"
    code.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 1", encoding="utf-8")
    (code / "signal_engine.py").symlink_to(outside)
    response = client.get("/runs/r1/code")
    assert response.status_code == 200 and response.json() == {}


# ---- scheduled routes: one id rule for every {job_id} route ------------------------------------


def test_colon_job_id_is_readable_like_every_other_job_route(
    tmp_path: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ScheduledResearchJobStore(path=tmp_path / "jobs.json")
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_store", store)
    store.upsert(
        ScheduledResearchJob(
            id="recording_wake:abc123", prompt="p", schedule="60000", next_run_at=1_700_000_000_000,
            status=JobStatus.PENDING, created_at=1_700_000_000_000,
        )
    )
    assert client.get("/scheduled-runs/recording_wake:abc123").status_code == 200
    assert client.get("/scheduled-runs/bad%20id").status_code == 400


# ---- live runner: a crashed task is logged -----------------------------------------------------


def test_crashed_live_runner_task_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async def boom() -> None:
            raise RuntimeError("broker feed died")

        task = asyncio.ensure_future(boom())
        task.add_done_callback(live_routes._log_runner_exit("robinhood"))
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=live_routes.logger.name):
        asyncio.run(scenario())
    assert any("robinhood" in r.getMessage() and r.exc_info for r in caplog.records)


def test_cancelled_live_runner_task_is_not_an_error(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async def forever() -> None:
            await asyncio.sleep(60)

        task = asyncio.ensure_future(forever())
        task.add_done_callback(live_routes._log_runner_exit("robinhood"))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    with caplog.at_level(logging.ERROR, logger=live_routes.logger.name):
        asyncio.run(scenario())
    assert caplog.records == []
