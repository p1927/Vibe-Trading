"""Plain ``async def`` API handlers must do their store/file/network I/O off the event loop.

The same guard as ``test_sse_job_streams_off_loop.py`` and ``test_swarm_handlers_off_loop.py``,
applied to the rest of the handlers found by the Trade backlog sweep
.claude/backlog/items/2026-09-16-vibe-api-async-handlers-blocking-sweep.md:

- session routes: the session store is one JSON file per session plus an append-only
  ``messages.jsonl`` read whole on every history fetch, and create/delete also touch the
  SQLite search/goal stores;
- scheduled-run routes: every read loads and parses the whole job-store JSON, and every
  mutation rewrites it with two fsyncs (``scheduler/status`` loads it too, via
  ``executor.liveness``);
- ``GET /live/status``: polled every 15s by several panels; per broker it lists connector
  profiles and reads the OAuth dir, the mandate file and the halt sentinels;
- the three trade job-stream handlers' existence check (a job-file read);
- ``GET /options/india/selector``'s eligibility check (OpenAlgo SQLite master contract and a
  live OpenAlgo symbol probe).

Run on the loop thread, one slow call freezes every request on the process for its duration,
``/health`` included. Each test makes every such call block its thread for 0.3s; a probe
coroutine on the same loop must keep ticking (max gap < 0.2s), and every call must run on a
thread other than the loop's.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_sse_job_streams_off_loop import _StoreCalls
from tests.test_swarm_handlers_off_loop import _await_with_probe, _endpoint

_SESSION_ID = "sess-off-loop"
_JOB_ID = "job-off-loop"
_STREAM_JOB_ID = "a" * 32


class _FakeRequest:
    headers: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def _session(title: str = "", last_attempt_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=_SESSION_ID,
        title=title,
        status=SimpleNamespace(value="idle"),
        created_at="2026-09-16T00:00:00Z",
        updated_at="2026-09-16T00:00:00Z",
        last_attempt_id=last_attempt_id,
        config={},
    )


def _message(role: str = "user", content: str = "probe question") -> SimpleNamespace:
    return SimpleNamespace(
        message_id=f"m-{role}",
        session_id=_SESSION_ID,
        role=role,
        content=content,
        created_at="2026-09-16T00:00:00Z",
        linked_attempt_id=None,
        metadata=None,
        tool_trail=[],
    )


def _install_session_service(monkeypatch: pytest.MonkeyPatch, svc: Any) -> None:
    import api_server

    monkeypatch.setattr(api_server, "_get_session_service", lambda: svc)


def test_list_sessions_reads_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _StoreCalls()
    _install_session_service(monkeypatch, SimpleNamespace(list_sessions=calls.blocking([_session()])))
    endpoint = _endpoint("/sessions", "GET")

    rows = _await_with_probe(lambda: endpoint(limit=50), calls, 1)

    assert [r.session_id for r in rows] == [_SESSION_ID]


def test_get_session_reads_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _StoreCalls()
    _install_session_service(monkeypatch, SimpleNamespace(get_session=calls.blocking(_session())))
    endpoint = _endpoint("/sessions/{session_id}", "GET")

    row = _await_with_probe(lambda: endpoint(session_id=_SESSION_ID), calls, 1)

    assert row.session_id == _SESSION_ID


def test_get_messages_reads_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _StoreCalls()
    _install_session_service(monkeypatch, SimpleNamespace(get_messages=calls.blocking([_message()])))
    endpoint = _endpoint("/sessions/{session_id}/messages", "GET")

    rows = _await_with_probe(lambda: endpoint(session_id=_SESSION_ID, limit=100), calls, 1)

    assert [r.content for r in rows] == ["probe question"]


def test_create_session_writes_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _StoreCalls()
    _install_session_service(monkeypatch, SimpleNamespace(create_session=calls.blocking(_session("t"))))
    endpoint = _endpoint("/sessions", "POST")

    row = _await_with_probe(
        lambda: endpoint(request=SimpleNamespace(title="t", config={}), principal=None), calls, 1
    )

    assert row.title == "t"


def test_delete_session_removes_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server

    calls = _StoreCalls()
    _install_session_service(monkeypatch, SimpleNamespace(delete_session=calls.blocking(True)))
    monkeypatch.setattr(
        api_server, "_goal_store", SimpleNamespace(delete_session_goals=calls.blocking(None))
    )
    endpoint = _endpoint("/sessions/{session_id}", "DELETE")

    # delete_session (dir rmtree + search index), then the goal-store delete.
    body = _await_with_probe(lambda: endpoint(session_id=_SESSION_ID), calls, 2)

    assert body == {"status": "deleted", "session_id": _SESSION_ID}


def test_update_session_reads_and_writes_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _StoreCalls()
    store = SimpleNamespace(get_session=calls.blocking(_session()), update_session=calls.blocking(None))
    _install_session_service(monkeypatch, SimpleNamespace(store=store))
    endpoint = _endpoint("/sessions/{session_id}", "PATCH")

    body = _await_with_probe(
        lambda: endpoint(session_id=_SESSION_ID, req=SimpleNamespace(title="renamed")), calls, 2
    )

    assert body == {"status": "updated", "session_id": _SESSION_ID}


def test_auto_title_session_store_io_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.providers.chat as chat

    class _FakeLLM:
        def chat(self, *_a: Any, **_kw: Any) -> SimpleNamespace:
            return SimpleNamespace(content="Probe title")

        def close(self) -> None:
            return None

    monkeypatch.setattr(chat, "ChatLLM", _FakeLLM)
    calls = _StoreCalls()
    store = SimpleNamespace(get_session=calls.blocking(_session()), update_session=calls.blocking(None))
    svc = SimpleNamespace(
        store=store, get_messages=calls.blocking([_message(), _message("assistant", "answer")])
    )
    _install_session_service(monkeypatch, svc)
    endpoint = _endpoint("/sessions/{session_id}/title/auto", "POST")

    # store.get_session, get_messages, store.update_session (the LLM fake doesn't block).
    body = _await_with_probe(lambda: endpoint(session_id=_SESSION_ID), calls, 3)

    assert body["title"] == "Probe title"


def test_session_provenance_reads_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.provenance.store as provenance_store

    calls = _StoreCalls()
    _install_session_service(monkeypatch, SimpleNamespace(get_session=calls.blocking(_session())))
    monkeypatch.setattr(
        provenance_store,
        "get_provenance_store",
        lambda: SimpleNamespace(list_session=calls.blocking([])),
    )
    endpoint = _endpoint("/sessions/{session_id}/provenance", "GET")

    assert _await_with_probe(lambda: endpoint(session_id=_SESSION_ID), calls, 2) == {"sources": []}


def test_session_events_pre_reads_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _StoreCalls()
    attempt = SimpleNamespace(status=SimpleNamespace(value="completed"), started_at=None)
    svc = SimpleNamespace(
        get_session=calls.blocking(_session(last_attempt_id="att-1")),
        store=SimpleNamespace(get_attempt=calls.blocking(attempt)),
    )
    _install_session_service(monkeypatch, svc)
    endpoint = _endpoint("/sessions/{session_id}/events", "GET")

    # get_session, then get_attempt for replay=active. The stream itself is not drained.
    response = _await_with_probe(
        lambda: endpoint(
            session_id=_SESSION_ID, request=_FakeRequest(), last_event_id=None, replay="active"
        ),
        calls,
        2,
    )

    assert response.media_type == "text/event-stream"


# ---------------------------------------------------------------------------
# Scheduled runs
# ---------------------------------------------------------------------------


def _job(**config: Any) -> Any:
    from src.scheduled_research.models import ScheduledResearchJob

    return ScheduledResearchJob(id=_JOB_ID, prompt="probe", schedule="3600000", config=dict(config))


@pytest.fixture
def scheduled(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Warm the handlers' lazy imports (a first import in the probe window reads as a stall)."""
    import src.scheduled_research.executor  # noqa: F401
    import src.scheduled_research.job_details  # noqa: F401
    import src.scheduled_research.pause_control  # noqa: F401
    import src.scheduled_research.playbooks  # noqa: F401
    import src.scheduled_research.proposals  # noqa: F401
    from src.api import scheduled_routes

    def install_store(**store_attrs: Any) -> None:
        monkeypatch.setattr(scheduled_routes, "_scheduled_research_store", SimpleNamespace(**store_attrs))

    return SimpleNamespace(routes=scheduled_routes, install_store=install_store)


def test_scheduler_status_liveness_off_loop(monkeypatch: pytest.MonkeyPatch, scheduled) -> None:
    calls = _StoreCalls()
    executor = SimpleNamespace(liveness=calls.blocking({"running": False, "max_overdue_seconds": 0.0}))
    monkeypatch.setattr(scheduled.routes, "_get_scheduled_research_executor", lambda: executor)
    endpoint = _endpoint("/scheduled-runs/scheduler/status", "GET")

    body = _await_with_probe(endpoint, calls, 1)

    assert body["running"] is False
    assert "enabled" in body


def test_list_scheduled_runs_reads_off_loop(scheduled) -> None:
    calls = _StoreCalls()
    scheduled.install_store(list_jobs=calls.blocking([_job()]))
    endpoint = _endpoint("/scheduled-runs", "GET")

    rows = _await_with_probe(lambda: endpoint(status_filter=None, limit=200), calls, 1)

    assert [r.id for r in rows] == [_JOB_ID]


def test_get_scheduled_run_reads_off_loop(scheduled) -> None:
    calls = _StoreCalls()
    scheduled.install_store(get=calls.blocking(_job()))
    endpoint = _endpoint("/scheduled-runs/{job_id}", "GET")

    assert _await_with_probe(lambda: endpoint(job_id=_JOB_ID), calls, 1).id == _JOB_ID


def test_create_scheduled_run_writes_off_loop(scheduled) -> None:
    calls = _StoreCalls()
    scheduled.install_store(upsert=calls.blocking(None))
    endpoint = _endpoint("/scheduled-runs", "POST")
    request = scheduled.routes.CreateScheduledRunRequest(id=_JOB_ID, prompt="probe", schedule="3600000")

    assert _await_with_probe(lambda: endpoint(request=request), calls, 1).id == _JOB_ID


def test_create_scheduled_run_from_playbook_writes_off_loop(monkeypatch: pytest.MonkeyPatch, scheduled) -> None:
    from src.scheduled_research import playbooks

    playbook = SimpleNamespace(name="Probe", slug="probe", to_job=lambda **_kw: _job())
    monkeypatch.setattr(playbooks, "get_playbook", lambda _slug: playbook)
    calls = _StoreCalls()
    scheduled.install_store(upsert=calls.blocking(None))
    endpoint = _endpoint("/scheduled-runs/playbooks/{slug}", "POST")
    request = scheduled.routes.CreateRunFromPlaybookRequest()

    row = _await_with_probe(lambda: endpoint(slug="probe", request=request), calls, 1)

    assert row.id == _JOB_ID


def test_delete_scheduled_run_writes_off_loop(scheduled) -> None:
    calls = _StoreCalls()
    scheduled.install_store(delete=calls.blocking(True))
    endpoint = _endpoint("/scheduled-runs/{job_id}", "DELETE")

    assert _await_with_probe(lambda: endpoint(job_id=_JOB_ID), calls, 1).status_code == 204


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_pause_resume_scheduled_run_off_loop(monkeypatch: pytest.MonkeyPatch, scheduled, action: str) -> None:
    from src.scheduled_research import pause_control

    calls = _StoreCalls()
    scheduled.install_store()
    monkeypatch.setattr(pause_control, "set_job_enabled", calls.blocking(_job()))
    endpoint = _endpoint(f"/scheduled-runs/{{job_id}}/{action}", "POST")

    assert _await_with_probe(lambda: endpoint(job_id=_JOB_ID), calls, 1).id == _JOB_ID


def test_cancel_scheduled_run_off_loop(monkeypatch: pytest.MonkeyPatch, scheduled) -> None:
    from src.scheduled_research import pause_control

    calls = _StoreCalls()
    scheduled.install_store()
    monkeypatch.setattr(pause_control, "cancel_running_job", calls.blocking(_job()))
    endpoint = _endpoint("/scheduled-runs/{job_id}/cancel", "POST")

    assert _await_with_probe(lambda: endpoint(job_id=_JOB_ID), calls, 1).id == _JOB_ID


def test_trigger_scheduled_run_off_loop(monkeypatch: pytest.MonkeyPatch, scheduled) -> None:
    from src.scheduled_research import pause_control

    calls = _StoreCalls()
    woke: list[bool] = []
    scheduled.install_store(load=calls.blocking({}))
    monkeypatch.setattr(pause_control, "trigger_job_now", calls.blocking(_job()))
    monkeypatch.setattr(
        scheduled.routes,
        "_get_scheduled_research_executor",
        lambda: SimpleNamespace(wake=lambda: woke.append(True)),
    )
    endpoint = _endpoint("/scheduled-runs/{job_id}/trigger", "POST")

    # trigger_job_now, then store.load() to count other due jobs for the
    # due_jobs_ahead response field (see _count_due_jobs_ahead).
    assert _await_with_probe(lambda: endpoint(job_id=_JOB_ID), calls, 2).id == _JOB_ID
    assert woke == [True]  # wake() sets loop-side events, so it stays on the loop


def test_scheduled_run_preview_off_loop(monkeypatch: pytest.MonkeyPatch, scheduled) -> None:
    from src.scheduled_research import job_details

    calls = _StoreCalls()
    scheduled.install_store(get=calls.blocking(_job(job_type="probe")))
    detail = SimpleNamespace(description="probe", preview=calls.blocking({"items": [], "note": "n"}))
    monkeypatch.setattr(job_details, "job_type_detail", lambda _job_type: detail)
    endpoint = _endpoint("/scheduled-runs/{job_id}/preview", "GET")

    # store.get, then the job type's preview callable.
    body = _await_with_probe(lambda: endpoint(job_id=_JOB_ID), calls, 2)

    assert body.preview_available is True
    assert body.preview_note == "n"


def test_stream_scheduled_run_logs_existence_check_off_loop(scheduled) -> None:
    calls = _StoreCalls()
    scheduled.install_store(get=calls.blocking(_job()))
    endpoint = _endpoint("/scheduled-runs/{job_id}/stream", "GET")

    response = _await_with_probe(lambda: endpoint(job_id=_JOB_ID, request=_FakeRequest()), calls, 1)

    assert response.media_type == "text/event-stream"


@pytest.mark.parametrize("action", ["commit", "discard"])
def test_scheduled_proposal_actions_off_loop(monkeypatch: pytest.MonkeyPatch, scheduled, action: str) -> None:
    from src.scheduled_research import proposals

    calls = _StoreCalls()
    monkeypatch.setattr(proposals, f"{action}_proposal", calls.blocking({"status": action}))
    endpoint = _endpoint(f"/scheduled-runs/proposals/{{proposal_id}}/{action}", "POST")

    assert _await_with_probe(lambda: endpoint(proposal_id="prop-1"), calls, 1) == {"status": action}


# ---------------------------------------------------------------------------
# Live status
# ---------------------------------------------------------------------------


def test_live_status_reads_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import api_server
    import src.live.halt as halt
    import src.trading.profiles as profiles
    from src.api import live_routes

    calls = _StoreCalls()
    monkeypatch.setattr(live_routes, "_known_live_brokers", lambda: ["robinhood"])
    monkeypatch.setattr(live_routes, "_live_broker_sdk_connectors", calls.blocking([]))
    monkeypatch.setattr(profiles, "list_profiles", calls.blocking([]))
    monkeypatch.setattr(live_routes, "_oauth_token_present", calls.blocking(False))
    monkeypatch.setattr(api_server, "_active_mandate_state", calls.blocking(None))
    runner = live_routes.RunnerLivenessState(
        broker="robinhood", alive=False, last_tick=None, last_tick_age_seconds=None
    )
    monkeypatch.setattr(live_routes, "_runner_liveness_state", calls.blocking(runner))
    monkeypatch.setattr(halt, "halt_flag_set", calls.blocking(False))
    endpoint = _endpoint("/live/status", "GET")

    # sdk-connector discovery, list_profiles, the four per-broker reads, the global halt read.
    body = _await_with_probe(lambda: endpoint(broker="robinhood"), calls, 7)

    assert body.global_halted is False
    assert [b.auth.broker for b in body.brokers] == ["robinhood"]


# ---------------------------------------------------------------------------
# Trade job streams
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("jobs_module", "handler"),
    [
        ("src.trade.external_predictions_run_jobs", "stream_external_predictions_refresh_job"),
        ("src.trade.index_prediction_run_jobs", "stream_index_prediction_run_job"),
        ("src.trade.recording_jobs", "stream_recording_job"),
    ],
)
def test_trade_job_stream_existence_check_off_loop(
    monkeypatch: pytest.MonkeyPatch, jobs_module: str, handler: str
) -> None:
    import importlib

    from src.api import trade_routes

    module = importlib.import_module(jobs_module)
    calls = _StoreCalls()
    monkeypatch.setattr(module, "_get_job_record", calls.blocking({"status": "running"}))
    endpoint = getattr(trade_routes, handler)

    response = _await_with_probe(
        lambda: endpoint(job_id=_STREAM_JOB_ID, request=_FakeRequest(), _auth=None), calls, 1
    )

    assert response.media_type == "text/event-stream"


# ---------------------------------------------------------------------------
# India options selector
# ---------------------------------------------------------------------------


def test_options_selector_eligibility_check_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    market = pytest.importorskip("trade_integrations.dataflows.options_research.market")
    chain_openalgo = pytest.importorskip(
        "trade_integrations.dataflows.options_research.sources.chain_openalgo"
    )

    calls = _StoreCalls()
    monkeypatch.setattr(market, "options_research_ineligible_reason", calls.blocking(None))
    monkeypatch.setattr(market, "resolve_options_instrument", calls.blocking(SimpleNamespace()))
    stage = SimpleNamespace(status="error", data=None, errors=["probe chain error"])
    monkeypatch.setattr(chain_openalgo, "fetch_chain_stage", calls.blocking(stage))
    endpoint = _endpoint("/options/india/selector", "GET")

    # eligibility check, instrument resolution, then the (already off-loop) chain fetch.
    response = _await_with_probe(
        lambda: endpoint(
            ticker="RELIANCE",
            expiry_date=None,
            horizon_days=7,
            target_profit=100.0,
            rank_by="risk_reward",
            n_paths=5_000,
        ),
        calls,
        3,
    )

    assert response.status_code == 502
    assert b"probe chain error" in response.body
