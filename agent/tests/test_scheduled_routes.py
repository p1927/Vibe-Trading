"""Route-level contracts for the scheduled research endpoints.

Exercises the REST surface mounted by ``register_scheduled_routes``:
``POST /scheduled-runs`` (create), ``GET /scheduled-runs`` (list + filter),
and ``DELETE /scheduled-runs/{job_id}`` (cancel). Each test drives the app
through ``TestClient`` and asserts the persisted store state, so the route
wiring, validation, and status codes are covered end to end.

The store singleton is redirected to a per-test ``tmp_path`` file so nothing
touches the real runtime root, and the default ``TestClient`` client host
(``testclient``) is treated as a loopback caller, so ``require_auth`` passes
without a configured API key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import scheduled_routes
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScheduledResearchJobStore:
    """Isolate the module-level store singleton onto a temp file."""
    isolated = ScheduledResearchJobStore(path=tmp_path / "scheduled_jobs.json")
    monkeypatch.setattr(scheduled_routes, "_scheduled_research_store", isolated)
    return isolated


@pytest.fixture
def client(store: ScheduledResearchJobStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _seed(store: ScheduledResearchJobStore, **overrides: object) -> ScheduledResearchJob:
    defaults: dict[str, object] = {
        "id": "job-seed",
        "prompt": "scan momentum names",
        "schedule": "60000",
        "next_run_at": 1_700_000_000_000,
        "status": JobStatus.PENDING,
        "created_at": 1_700_000_000_000,
    }
    defaults.update(overrides)
    job = ScheduledResearchJob(**defaults)  # type: ignore[arg-type]
    store.upsert(job)
    return job


def test_create_persists_job_and_returns_201(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.post(
        "/scheduled-runs",
        json={
            "id": "daily-scan",
            "prompt": "rank S&P 500 by 12-1 momentum",
            "schedule": "0 9 * * *",
            "config": {"universe": "sp500"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "daily-scan"
    assert body["status"] == "pending"
    assert body["last_run_at"] is None
    assert body["consecutive_failures"] == 0
    assert body["last_error"] is None
    assert body["failure_kind"] is None
    assert body["config"] == {"universe": "sp500"}

    stored = store.get("daily-scan")
    assert stored is not None
    assert stored.prompt == "rank S&P 500 by 12-1 momentum"
    assert stored.schedule == "0 9 * * *"


def test_create_generates_id_and_defaults_next_run_when_omitted(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.post(
        "/scheduled-runs",
        json={"prompt": "rebalance check", "schedule": "300000"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["next_run_at"] > 0
    assert store.get(body["id"]) is not None


def test_create_rejects_malformed_schedule_with_422(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.post(
        "/scheduled-runs",
        json={"prompt": "bad cron", "schedule": "0 99 * * *"},
    )

    assert response.status_code == 422
    assert store.list_jobs() == []


def test_list_returns_jobs_newest_first(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="older", created_at=1_700_000_000_000)
    _seed(store, id="newer", created_at=1_700_000_500_000)

    response = client.get("/scheduled-runs")

    assert response.status_code == 200
    ids = [job["id"] for job in response.json()]
    assert ids == ["newer", "older"]


def test_list_filters_by_status(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="pending-one", status=JobStatus.PENDING)
    _seed(store, id="done-one", status=JobStatus.COMPLETED)

    response = client.get("/scheduled-runs", params={"status": "completed"})

    assert response.status_code == 200
    body = response.json()
    assert [job["id"] for job in body] == ["done-one"]


def test_list_surfaces_retry_diagnostics(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(
        store,
        id="retrying",
        status=JobStatus.PENDING,
        last_run_at=1_700_000_100_000,
        consecutive_failures=2,
        last_error="TimeoutError: provider timed out",
        failure_kind="dispatch",
    )

    response = client.get("/scheduled-runs")

    assert response.status_code == 200
    body = response.json()[0]
    assert body["last_run_at"] == 1_700_000_100_000
    assert body["consecutive_failures"] == 2
    assert body["last_error"] == "TimeoutError: provider timed out"
    assert body["failure_kind"] == "dispatch"


def test_list_rejects_out_of_range_limit(client: TestClient):
    assert client.get("/scheduled-runs", params={"limit": 0}).status_code == 422
    assert client.get("/scheduled-runs", params={"limit": 500}).status_code == 422


def test_delete_removes_job_and_returns_204(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="cancel-me")

    response = client.delete("/scheduled-runs/cancel-me")

    assert response.status_code == 204
    assert not response.content
    assert "content-type" not in response.headers
    assert store.get("cancel-me") is None


def test_delete_unknown_job_returns_404(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.delete("/scheduled-runs/never-existed")

    assert response.status_code == 404


def test_delete_rejects_unsafe_job_id(
    client: TestClient, store: ScheduledResearchJobStore
):
    # A single path segment that still fails the safe-id pattern (the dot is
    # outside ``[A-Za-z0-9_-]``) is rejected by the handler before any store
    # lookup, so it returns 400 rather than the 404 used for unknown ids.
    response = client.delete("/scheduled-runs/bad.id")

    assert response.status_code == 400


def test_job_id_routes_accept_a_colon_namespaced_id(
    client: TestClient, store: ScheduledResearchJobStore
):
    """recording_wake_jobs namespaces its ids as ``recording_wake:<uuid>`` —
    every job-id route must accept the colon, not just create/list/stream."""
    job_id = "recording_wake:3d53a1871aa743769672e32f8abb91e3"
    _seed(store, id=job_id, status=JobStatus.RUNNING)

    assert client.post(f"/scheduled-runs/{job_id}/cancel").status_code == 200
    assert client.post(f"/scheduled-runs/{job_id}/pause").status_code == 200
    assert client.post(f"/scheduled-runs/{job_id}/resume").status_code == 200
    assert client.post(f"/scheduled-runs/{job_id}/trigger").status_code == 200
    assert client.delete(f"/scheduled-runs/{job_id}").status_code == 204


def test_stream_route_accepts_a_colon_namespaced_id(
    client: TestClient, store: ScheduledResearchJobStore
):
    job_id = "recording_wake:3d53a1871aa743769672e32f8abb91e3"
    _seed(store, id=job_id)

    with client.stream("GET", f"/scheduled-runs/{job_id}/stream") as response:
        assert response.status_code == 200


def test_job_id_routes_still_reject_a_path_traversal_shaped_id(
    client: TestClient,
):
    # Confirms tightening the pattern to allow ':' didn't also open up '/'
    # or '..' — still a single safe path segment, just a wider charset.
    response = client.post("/scheduled-runs/../etc/pause")

    assert response.status_code in (400, 404)


def test_pause_sets_paused_without_touching_schedule(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="pause-me", next_run_at=1_700_000_000_000)

    response = client.post("/scheduled-runs/pause-me/pause")

    assert response.status_code == 200
    body = response.json()
    assert body["paused"] is True
    assert body["next_run_at"] == 1_700_000_000_000
    stored = store.get("pause-me")
    assert stored is not None
    assert stored.paused is True
    assert stored.next_run_at == 1_700_000_000_000


def test_resume_clears_paused(client: TestClient, store: ScheduledResearchJobStore):
    job = _seed(store, id="resume-me")
    job.paused = True
    store.upsert(job)

    response = client.post("/scheduled-runs/resume-me/resume")

    assert response.status_code == 200
    assert response.json()["paused"] is False
    stored = store.get("resume-me")
    assert stored is not None
    assert stored.paused is False


def test_pause_unknown_job_returns_404(client: TestClient):
    response = client.post("/scheduled-runs/never-existed/pause")

    assert response.status_code == 404


def test_cancel_running_job_sets_cancelled_status_leaves_paused_untouched(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="cancel-me", status=JobStatus.RUNNING)

    response = client.post("/scheduled-runs/cancel-me/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["paused"] is False
    stored = store.get("cancel-me")
    assert stored is not None
    assert stored.status == JobStatus.CANCELLED
    assert stored.paused is False
    assert stored.last_error == "cancelled by user"


def test_cancel_non_running_job_returns_409(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="idle-job", status=JobStatus.PENDING)

    response = client.post("/scheduled-runs/idle-job/cancel")

    assert response.status_code == 409


def test_cancel_unknown_job_returns_404(client: TestClient):
    response = client.post("/scheduled-runs/never-existed/cancel")

    assert response.status_code == 404


def test_trigger_sets_next_run_at_to_now_without_changing_status(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="trigger-me", next_run_at=9_999_999_999_000, status=JobStatus.PENDING)

    response = client.post("/scheduled-runs/trigger-me/trigger")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["next_run_at"] < 9_999_999_999_000
    stored = store.get("trigger-me")
    assert stored is not None
    assert stored.next_run_at < 9_999_999_999_000
    assert stored.status == JobStatus.PENDING


def test_trigger_paused_job_returns_409(client: TestClient, store: ScheduledResearchJobStore):
    _seed(store, id="paused-job", status=JobStatus.PENDING)
    job = store.get("paused-job")
    assert job is not None
    job.paused = True
    store.upsert(job)

    response = client.post("/scheduled-runs/paused-job/trigger")

    assert response.status_code == 409
    assert "paused" in response.json()["detail"]


def test_trigger_already_running_job_returns_409(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="running-job", status=JobStatus.RUNNING)

    response = client.post("/scheduled-runs/running-job/trigger")

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_trigger_unknown_job_returns_404(client: TestClient):
    response = client.post("/scheduled-runs/never-existed/trigger")

    assert response.status_code == 404


def test_trigger_wakes_executor(
    client: TestClient, store: ScheduledResearchJobStore, monkeypatch: pytest.MonkeyPatch
):
    from unittest import mock

    _seed(store, id="wake-me", status=JobStatus.PENDING)

    stub_executor = mock.Mock()
    monkeypatch.setattr(
        scheduled_routes, "_get_scheduled_research_executor", lambda: stub_executor
    )

    response = client.post("/scheduled-runs/wake-me/trigger")

    assert response.status_code == 200
    stub_executor.wake.assert_called_once()


def test_pause_and_resume_share_the_single_mutation_helper(
    client: TestClient, store: ScheduledResearchJobStore, monkeypatch: pytest.MonkeyPatch
):
    """Both the generic route and the prediction-jobs module must call the
    same ``set_job_enabled`` helper, so the two surfaces can never disagree
    about a job's pause state again."""
    from src.scheduled_research import pause_control
    from src.trade import index_prediction_jobs

    _seed(
        store,
        id="index-shared",
        config={"job_type": "index_factor_snapshot"},
    )

    calls: list[tuple[str, bool]] = []
    original = pause_control.set_job_enabled

    def spy(job_id: str, enabled: bool, *, store):
        calls.append((job_id, enabled))
        return original(job_id, enabled, store=store)

    monkeypatch.setattr(pause_control, "set_job_enabled", spy)
    monkeypatch.setattr(index_prediction_jobs, "set_job_enabled", spy)

    client.post("/scheduled-runs/index-shared/pause")
    index_prediction_jobs.resume_index_prediction_job("index-shared", store=store)

    assert ("index-shared", False) in calls
    assert ("index-shared", True) in calls
    stored = store.get("index-shared")
    assert stored is not None


def test_trigger_shares_the_single_mutation_helper(
    client: TestClient, store: ScheduledResearchJobStore, monkeypatch: pytest.MonkeyPatch
):
    """Both the generic route and the prediction-jobs trigger-now action
    must call the same ``trigger_job_now`` helper, so they can never
    diverge on what "run now" actually does to a job."""
    from src.scheduled_research import pause_control
    from src.trade import index_prediction_jobs

    _seed(store, id="trigger-shared", config={"job_type": "index_factor_snapshot"})
    _seed(store, id="trigger-shared-2", config={"job_type": "index_factor_snapshot"})

    calls: list[str] = []
    original = pause_control.trigger_job_now

    def spy(job_id: str, *, store):
        calls.append(job_id)
        return original(job_id, store=store)

    monkeypatch.setattr(pause_control, "trigger_job_now", spy)
    monkeypatch.setattr(index_prediction_jobs, "trigger_job_now", spy)

    client.post("/scheduled-runs/trigger-shared/trigger")
    index_prediction_jobs.trigger_index_prediction_job("trigger-shared-2", store=store)

    assert "trigger-shared" in calls
    assert "trigger-shared-2" in calls


def test_index_prediction_pause_uses_paused_field_not_status(
    store: ScheduledResearchJobStore,
):
    from src.trade.index_prediction_jobs import (
        _serialize_job,
        pause_index_prediction_job,
        resume_index_prediction_job,
    )

    job = _seed(
        store,
        id="index-pause-me",
        config={"job_type": "index_factor_snapshot"},
        status=JobStatus.PENDING,
    )

    result = pause_index_prediction_job(job.id, store=store)
    assert result["status"] == "ok"
    stored = store.get(job.id)
    assert stored is not None
    assert stored.paused is True
    assert stored.status == JobStatus.PENDING  # never overloaded as CANCELLED
    assert _serialize_job(stored)["paused"] is True

    result = resume_index_prediction_job(job.id, store=store)
    assert result["status"] == "ok"
    stored = store.get(job.id)
    assert stored is not None
    assert stored.paused is False


def test_index_prediction_resume_does_not_demote_a_running_job(
    store: ScheduledResearchJobStore,
):
    from src.trade.index_prediction_jobs import resume_index_prediction_job

    job = _seed(
        store,
        id="index-running",
        config={"job_type": "index_factor_snapshot"},
        status=JobStatus.RUNNING,
    )
    job.paused = True
    store.upsert(job)

    resume_index_prediction_job(job.id, store=store)

    stored = store.get(job.id)
    assert stored is not None
    assert stored.paused is False
    assert stored.status == JobStatus.RUNNING  # not forced back to PENDING


def test_list_includes_section_derived_from_job_type(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="ad-hoc", config={})
    _seed(store, id="index-job", config={"job_type": "index_factor_snapshot"})
    _seed(store, id="options-job", config={"job_type": "options_plan_refresh"})
    _seed(store, id="hub-job", config={"job_type": "hub_morning_calibration"})

    body = {row["id"]: row for row in client.get("/scheduled-runs").json()}

    assert body["ad-hoc"]["section"] == "general"
    assert body["index-job"]["section"] == "prediction"
    assert body["options-job"]["section"] == "options"
    assert body["hub-job"]["section"] == "hub"


def test_index_prediction_cancel_running_job(store: ScheduledResearchJobStore):
    from src.trade.index_prediction_jobs import cancel_index_prediction_job

    job = _seed(
        store,
        id="index-cancel-me",
        config={"job_type": "index_factor_snapshot"},
        status=JobStatus.RUNNING,
    )

    result = cancel_index_prediction_job(job.id, store=store)

    assert result["status"] == "ok"
    stored = store.get(job.id)
    assert stored is not None
    assert stored.status == JobStatus.CANCELLED
    assert stored.paused is False
def test_create_with_timezone_echoes_and_persists(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.post(
        "/scheduled-runs",
        json={
            "id": "auckland-scan",
            "prompt": "pre-open scan of NZX names",
            "schedule": "30 23 * * 1-5",
            "timezone": "Pacific/Auckland",
        },
    )

    assert response.status_code == 201
    assert response.json()["timezone"] == "Pacific/Auckland"

    saved = store.get("auckland-scan")
    assert saved is not None
    assert saved.timezone == "Pacific/Auckland"
    assert saved.schedule == "30 23 * * 1-5"


def test_create_without_timezone_defaults_to_null(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.post(
        "/scheduled-runs",
        json={"id": "utc-scan", "prompt": "scan", "schedule": "0 9 * * *"},
    )

    assert response.status_code == 201
    body = response.json()
    assert "timezone" in body
    assert body["timezone"] is None
    saved = store.get("utc-scan")
    assert saved is not None
    assert saved.timezone is None


def test_create_rejects_unknown_timezone(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.post(
        "/scheduled-runs",
        json={
            "id": "bad-tz",
            "prompt": "scan",
            "schedule": "0 9 * * *",
            "timezone": "Not/AZone",
        },
    )

    assert response.status_code == 422
    assert "IANA timezone" in response.json()["detail"]
    assert store.get("bad-tz") is None


def test_list_includes_timezone(client: TestClient, store: ScheduledResearchJobStore):
    _seed(store, id="tz-listed", schedule="0 9 * * 1-5", timezone="Australia/Adelaide")

    response = client.get("/scheduled-runs")

    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}
    assert rows["tz-listed"]["timezone"] == "Australia/Adelaide"


def test_create_tz_cron_defaults_next_run_to_first_authored_occurrence(
    client: TestClient, store: ScheduledResearchJobStore
):
    from src.scheduled_research.executor import next_due

    before = int(__import__("time").time() * 1000)
    response = client.post(
        "/scheduled-runs",
        json={
            "id": "first-occurrence",
            "prompt": "scan",
            "schedule": "30 23 * * 1-5",
            "timezone": "Pacific/Auckland",
        },
    )
    after = int(__import__("time").time() * 1000)

    assert response.status_code == 201
    next_run_at = response.json()["next_run_at"]
    # The first fire is the first authored wall-clock occurrence, which for a
    # 23:30 weekday cadence is strictly in the future — never "now".
    assert next_run_at > after
    assert next_due("30 23 * * 1-5", before, "Pacific/Auckland") <= next_run_at
    assert next_run_at <= next_due("30 23 * * 1-5", after, "Pacific/Auckland")


def test_create_without_timezone_keeps_immediate_first_fire(
    client: TestClient, store: ScheduledResearchJobStore
):
    before = int(__import__("time").time() * 1000)
    response = client.post(
        "/scheduled-runs",
        json={"id": "legacy-default", "prompt": "scan", "schedule": "0 9 * * *"},
    )
    after = int(__import__("time").time() * 1000)

    assert response.status_code == 201
    assert before <= response.json()["next_run_at"] <= after


def test_create_interval_with_timezone_keeps_immediate_first_fire(
    client: TestClient, store: ScheduledResearchJobStore
):
    before = int(__import__("time").time() * 1000)
    response = client.post(
        "/scheduled-runs",
        json={
            "id": "interval-tz",
            "prompt": "scan",
            "schedule": "60000",
            "timezone": "Pacific/Auckland",
        },
    )
    after = int(__import__("time").time() * 1000)

    assert response.status_code == 201
    assert before <= response.json()["next_run_at"] <= after


def test_create_rejects_ids_the_delete_route_would_refuse(
    client: TestClient, store: ScheduledResearchJobStore
):
    for bad_id in ("my scan.v1", "a/b", "café", "x" * 129):
        response = client.post(
            "/scheduled-runs",
            json={"id": bad_id, "prompt": "scan", "schedule": "60000"},
        )
        assert response.status_code == 422, bad_id
        assert "job id" in response.json()["detail"]
        assert store.get(bad_id) is None


def test_created_job_is_always_deletable(
    client: TestClient, store: ScheduledResearchJobStore
):
    created = client.post(
        "/scheduled-runs",
        json={"prompt": "scan", "schedule": "60000"},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    assert client.delete(f"/scheduled-runs/{job_id}").status_code == 204
    assert store.get(job_id) is None


def test_create_accepts_ids_within_the_id_rule(
    client: TestClient, store: ScheduledResearchJobStore
):
    for good_id in ("daily-scan", "scan_2026", "A" * 128):
        response = client.post(
            "/scheduled-runs",
            json={"id": good_id, "prompt": "scan", "schedule": "60000"},
        )
        assert response.status_code == 201, good_id
        assert client.delete(f"/scheduled-runs/{good_id}").status_code == 204
def test_create_interval_accepts_a_timezone_it_will_never_use(
    client: TestClient, store: ScheduledResearchJobStore
):
    # The composer attaches the browser zone to every create. An interval
    # schedule ignores it, and the executor never resolves it, so a key this
    # host cannot resolve must not block the create.
    response = client.post(
        "/scheduled-runs",
        json={
            "id": "interval-unknown-zone",
            "prompt": "scan",
            "schedule": "60000",
            "timezone": "Mars/Olympus_Mons",
        },
    )

    assert response.status_code == 201
    saved = store.get("interval-unknown-zone")
    assert saved is not None
    assert saved.timezone == "Mars/Olympus_Mons"


def test_create_cron_still_rejects_an_unresolvable_timezone(
    client: TestClient, store: ScheduledResearchJobStore
):
    response = client.post(
        "/scheduled-runs",
        json={
            "id": "cron-unknown-zone",
            "prompt": "scan",
            "schedule": "0 9 * * *",
            "timezone": "Mars/Olympus_Mons",
        },
    )

    assert response.status_code == 422
    assert "IANA timezone" in response.json()["detail"]
    assert store.get("cron-unknown-zone") is None


def test_create_rejects_a_blank_timezone_for_both_schedule_forms(
    client: TestClient, store: ScheduledResearchJobStore
):
    for schedule in ("60000", "0 9 * * *"):
        response = client.post(
            "/scheduled-runs",
            json={"prompt": "scan", "schedule": schedule, "timezone": "   "},
        )
        assert response.status_code == 422, schedule


def test_list_carries_the_last_verdict_record(
    client: TestClient, store: ScheduledResearchJobStore
):
    from src.scheduled_research.verdict import VerdictItem, VerdictRecord

    verdict = VerdictRecord(
        session_id="sess-9",
        recorded_at=1_700_000_100_000,
        parse="ok",
        outcome="DRIFT",
        items=[VerdictItem(symbol="600519.SH", state="DRIFT", reason="band crossed")],
        previous=VerdictRecord(
            session_id="sess-8",
            recorded_at=1_700_000_000_000,
            parse="ok",
            outcome="no_calls",
            items=[],
        ),
    )
    _seed(store, id="with-verdict", last_verdict=verdict)

    response = client.get("/scheduled-runs")

    assert response.status_code == 200
    (row,) = response.json()
    assert row["last_verdict"]["outcome"] == "DRIFT"
    assert row["last_verdict"]["items"][0]["symbol"] == "600519.SH"
    assert row["last_verdict"]["previous"]["outcome"] == "no_calls"


def test_list_omits_verdict_when_never_recorded(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="no-verdict")

    response = client.get("/scheduled-runs")

    assert response.status_code == 200
    (row,) = response.json()
    assert row["last_verdict"] is None


# --- GET /scheduled-runs/{job_id}/stream (step 6, live-log-tail SSE) -------

def _parse_sse_events(body: str) -> list[tuple[str, str]]:
    """Parse ``event: <name>\\ndata: <json>\\n\\n`` frames into (event, data) pairs."""
    events: list[tuple[str, str]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if event_name is not None and data is not None:
            events.append((event_name, data))
    return events


def test_stream_unknown_job_returns_404(client: TestClient):
    response = client.get("/scheduled-runs/does-not-exist/stream")
    assert response.status_code == 404


def test_stream_replays_buffered_logs_then_emits_terminal_status(
    client: TestClient, store: ScheduledResearchJobStore
):
    from src.scheduled_research import run_log_buffer

    _seed(store, id="job-done", status=JobStatus.COMPLETED)
    run_log_buffer.clear_logs("job-done")
    run_log_buffer.append_log("job-done", "starting (index_research)")
    run_log_buffer.append_log("job-done", "completed")

    response = client.get("/scheduled-runs/job-done/stream")

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert [name for name, _ in events] == ["log", "log", "status"]
    assert "starting" in events[0][1]
    assert "completed" in events[1][1]
    import json as _json

    assert _json.loads(events[2][1]) == {"status": "completed"}


def test_stream_with_no_buffered_logs_emits_only_terminal_status(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="job-pending", status=JobStatus.PENDING)

    response = client.get("/scheduled-runs/job-pending/stream")

    assert response.status_code == 200
    events = _parse_sse_events(response.text)
    assert [name for name, _ in events] == ["status"]


def test_preview_unknown_job_returns_404(client: TestClient):
    response = client.get("/scheduled-runs/never-existed/preview")

    assert response.status_code == 404


def test_preview_no_job_type_returns_generic_description(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="ad-hoc-monitor", config={})

    response = client.get("/scheduled-runs/ad-hoc-monitor/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["preview_available"] is False
    assert body["description"]
    assert body["preview_items"] == []


def test_preview_returns_description_only_for_job_type_without_preview(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(store, id="options-plan", config={"job_type": "options_plan_refresh"})

    response = client.get("/scheduled-runs/options-plan/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["preview_available"] is False
    assert body["preview_error"] is None
    assert body["description"]
    assert body["preview_items"] == []


def test_preview_hub_news_ingest_returns_resolved_rss_urls(
    client: TestClient, store: ScheduledResearchJobStore
):
    _seed(
        store,
        id="nifty-news-light",
        config={
            "job_type": "hub_news_ingest",
            "market": "IN",
            "ticker": "NIFTY",
            "mode": "light",
        },
    )

    response = client.get("/scheduled-runs/nifty-news-light/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["preview_available"] is True
    assert body["preview_error"] is None
    assert body["preview_items"], "expected at least one resolved RSS URL"
    assert all(item.startswith("http") for item in body["preview_items"])
    assert "mode=light" in body["preview_note"]


def test_preview_degrades_gracefully_when_preview_callable_raises(
    client: TestClient, store: ScheduledResearchJobStore, monkeypatch: pytest.MonkeyPatch
):
    from src.scheduled_research import job_details

    def _boom(_config: dict) -> dict:
        raise RuntimeError("registry file unreadable")

    monkeypatch.setitem(
        job_details._JOB_DETAILS,
        "hub_capture_factor_snapshot",
        job_details.JobTypeDetail("Captures factor snapshots.", preview=_boom),
    )
    _seed(store, id="factor-snap", config={"job_type": "hub_capture_factor_snapshot"})

    response = client.get("/scheduled-runs/factor-snap/preview")

    assert response.status_code == 200
    body = response.json()
    assert body["preview_available"] is False
    assert body["preview_error"] and "registry file unreadable" in body["preview_error"]
