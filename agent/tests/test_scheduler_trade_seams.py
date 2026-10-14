"""Seam checks for the Vibe scheduler / trade bridge (Trade review R2, seams-vibe-fork-internals).

One file, one check per defect found at the boundary:

* every recovery path advances a job's ``next_run_at`` in the job's OWN timezone (IST jobs were
  advanced in UTC by shutdown / stale / boot recovery and by the startup backlog deferral);
* the recording store (the last store moved onto ``JobStore``) keeps its recording-only rules:
  ``waiting_for_open`` has no pid and is not a dead worker, a log append must not promote it to
  ``running`` (no worker exists yet), a failed ``wait_for_open`` scheduling fails the new job and
  raises instead of spawning a worker against a closed market;
* ``persist_trade_widget`` returns ``None`` for an id its store refuses (a ticker like ``M&M``): the
  advisory approve route must say so instead of returning a widget id execute-basket cannot load.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.scheduled_research import lifecycle
from src.scheduled_research.executor import ScheduledResearchExecutor, first_fire_ms, next_due
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore

IST = "Asia/Kolkata"
# 2026-10-14 05:00 UTC = 10:30 IST: the next "30 9 * * 1-5" slot is tomorrow 09:30 IST (04:00 UTC)
# in IST but today 09:30 UTC (15:00 IST) in UTC, so the two readings cannot coincide.
NOW_MS = int(datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc).timestamp() * 1000)
CRON = "30 9 * * 1-5"


def _job(job_id: str, *, status: JobStatus, job_type: str = "index_plan_refresh") -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id=job_id, prompt="p", schedule=CRON, timezone=IST, next_run_at=NOW_MS - 60_000, status=status,
        created_at=1, last_run_at=NOW_MS - 3_600_000, config={"job_type": job_type},
    )


def _expect_ist(job: ScheduledResearchJob) -> None:
    assert job.next_run_at == next_due(CRON, NOW_MS, IST)
    assert job.next_run_at != next_due(CRON, NOW_MS, None), "advanced in UTC, not the job's own timezone"
    local = datetime.fromtimestamp(job.next_run_at / 1000, ZoneInfo(IST))
    assert (local.hour, local.minute) == (9, 30)


@pytest.fixture()
def store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(path=tmp_path / "jobs.json")


async def _noop(job: ScheduledResearchJob) -> None:
    return None


def test_executor_recovery_paths_advance_in_the_jobs_timezone(store, monkeypatch) -> None:
    monkeypatch.setenv("STACK_PROFILE", "dev")
    store.upsert(_job("stale", status=JobStatus.RUNNING))
    executor = ScheduledResearchExecutor(store, _noop)
    assert executor.recover_stale_running(NOW_MS, startup=True) == 1
    _expect_ist(store.get("stale"))

    store.upsert(_job("shutdown", status=JobStatus.RUNNING))
    assert executor.recover_all_running_on_shutdown(NOW_MS) == 1
    _expect_ist(store.get("shutdown"))

    # a collection job on the dev tier cannot dispatch: its overdue slot is deferred, in its own timezone
    store.upsert(_job("deferred", status=JobStatus.PENDING, job_type="hub_news_ingest"))
    assert executor.defer_startup_backlog(NOW_MS) >= 1
    _expect_ist(store.get("deferred"))


def test_stack_recovery_advances_in_the_jobs_timezone(store, monkeypatch) -> None:
    store.upsert(_job("boot", status=JobStatus.RUNNING))
    monkeypatch.setattr(lifecycle.time, "time", lambda: NOW_MS / 1000)
    assert lifecycle.recover_persisted_scheduler_jobs(store, mode="all_running", reason="r") == 1
    _expect_ist(store.get("boot"))


@pytest.fixture()
def rec(tmp_path, monkeypatch):
    from src.trade import recording_jobs

    monkeypatch.setattr(recording_jobs, "_jobs_root", lambda: tmp_path / "recording")
    recording_jobs.RECORDING_JOBS.clear()
    recording_jobs._ACTIVE_BY_SCOPE.clear()
    return recording_jobs


def test_waiting_for_open_is_not_a_dead_worker_and_logs_do_not_wake_it(rec) -> None:
    job_id, _ = rec.start_job(underlyings=["NIFTY"], wait_for_open=True)
    assert rec.set_waiting_for_open_at(job_id, next_open_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert rec.reconcile_job(job_id) is False  # no pid is normal while waiting
    rec.append_log(job_id, {"stage": "control", "message": "stop requested"})
    snap = rec.get_job(job_id)
    assert snap["status"] == "waiting_for_open"  # a log must not promote it: no worker exists yet
    assert snap["run_started_at"] is None
    rec.mark_running(job_id)
    assert rec.get_job(job_id)["status"] == "running"
    assert rec.get_job(job_id)["run_started_at"]  # the wall-clock budget runs from here, not creation


def test_one_live_recording_job_is_reused_by_a_second_start(rec) -> None:
    first, reused = rec.start_job(underlyings=["NIFTY"])
    assert reused is False
    rec._mutate_job_on_disk(first, lambda job: job.update(worker_pid=os.getpid()) or True)
    # a process that never saw the job (empty memory) still finds it on disk and reuses it
    rec.RECORDING_JOBS.clear()
    rec._ACTIVE_BY_SCOPE.clear()
    second, reused = rec.start_job(underlyings=["BANKNIFTY"])
    assert (second, reused) == (first, True)


def test_stop_job_is_the_one_stop_path_for_a_waiting_and_a_running_recording(rec, monkeypatch) -> None:
    from src.trade import recording_wait_scheduler

    cancelled: list[str] = []
    monkeypatch.setattr(recording_wait_scheduler, "cancel_recording_wake", lambda recording_job_id: cancelled.append(recording_job_id))
    waiting, _ = rec.start_job(underlyings=["NIFTY"], wait_for_open=True)
    assert rec.set_waiting_for_open_at(waiting, next_open_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert rec.stop_job(waiting) == "wait cancelled"
    assert cancelled == [waiting] and rec.get_job(waiting)["status"] == "error"
    running, _ = rec.start_job(underlyings=["BANKNIFTY"])
    assert rec.stop_job(running) == "stop requested"
    assert not hasattr(rec, "stop_active_job_cooperatively")  # the unused second copy is gone


def test_ensure_worker_is_the_one_spawn_or_respawn_rule(rec) -> None:
    """Every kick path (index prediction, external predictions, forecast engine, recording) shares it."""
    job_id, reused = rec.start_job(underlyings=["NIFTY"])
    spawned: list[str] = []
    rec._STORE.ensure_worker(job_id, reused, spawned.append)
    assert spawned == [job_id]  # new job: spawn
    rec._STORE.ensure_worker(job_id, True, spawned.append)
    assert spawned == [job_id]  # reused, pid-less queued: its worker is still to come, no second one
    rec._mutate_job_on_disk(job_id, lambda job: job.update(worker_pid=2**22 + 12345, status="running") or True)
    rec._STORE.ensure_worker(job_id, True, spawned.append)
    assert spawned == [job_id, job_id]  # reused but its worker died: respawn
    rec._STORE.ensure_worker("0" * 32, True, spawned.append)
    assert len(spawned) == 2  # reused and unknown: nothing to respawn


def test_trade_source_roots_go_behind_project_entries_and_ahead_of_installed_packages(monkeypatch) -> None:
    """2026-10-14-trade-stack-path-shadows-top-level-names: <trade>/tradingagents holds `tests/`, `cli/`,
    `scripts/`; at sys.path[0] it shadowed the agent's own `tests`/`cli` (six child-dispatch tests failed)."""
    import sys
    import sysconfig

    from src.trade.hub_bridge import _first_installed_path_index

    stdlib = sysconfig.get_paths()["stdlib"]
    monkeypatch.setattr(sys, "path", ["", "/agent", stdlib, "/venv/lib/python3/site-packages"])
    assert _first_installed_path_index() == 2
    monkeypatch.setattr(sys, "path", ["", "/agent"])
    assert _first_installed_path_index() == 2


def test_a_ticker_typed_in_capitals_beats_an_ordinary_word_that_is_also_a_listing(monkeypatch) -> None:
    from src.trade import symbol_detect

    monkeypatch.setattr(symbol_detect, "_in_indices", lambda: frozenset({"NIFTY"}))
    monkeypatch.setattr(symbol_detect, "_filter_india_listed", lambda c: [x for x in c if x in {"IDEA", "HDFCBANK", "SAIL"}])
    assert symbol_detect.extract_primary_ticker("what is your idea for HDFCBANK today") == "HDFCBANK"
    assert symbol_detect.extract_primary_ticker("outlook for sail please") == "SAIL"  # lower case still works
    assert symbol_detect.extract_primary_ticker("Is HDFCBANK a sail or an idea") == "HDFCBANK"
    assert symbol_detect.extract_primary_ticker("nifty view") == "NIFTY"


def test_failed_wait_for_open_scheduling_fails_the_new_job_and_raises(rec, monkeypatch) -> None:
    import nautilus_openalgo_bridge.market_hours as market_hours
    from src.trade import recording_wait_scheduler

    monkeypatch.setattr(market_hours, "is_real_nse_market_open", lambda *a, **k: False)
    monkeypatch.setattr(market_hours, "next_real_nse_open_at", lambda **k: datetime(2099, 1, 1, tzinfo=timezone.utc))

    def _boom(**_):
        raise RuntimeError("scheduler store unreadable")

    monkeypatch.setattr(recording_wait_scheduler, "schedule_recording_wake", _boom)
    spawned: list[str] = []
    monkeypatch.setattr(rec, "spawn_worker", spawned.append)
    with pytest.raises(RuntimeError, match="scheduler store unreadable"):
        rec.kick_recording(underlyings=["NIFTY"], wait_for_open=True)
    assert spawned == []  # no worker against a closed market
    jobs = list(rec.RECORDING_JOBS.values())
    assert [j["status"] for j in jobs] == ["error"]
    assert "wait_for_open scheduling failed" in jobs[0]["error"]


def test_approve_route_refuses_a_widget_the_store_cannot_persist(monkeypatch) -> None:
    from fastapi import HTTPException

    from src.api import advisory_routes
    from trade_integrations.dataflows.options_research import widget_payload
    from trade_integrations.trade_widgets import store as widget_store

    monkeypatch.setattr(widget_payload, "build_options_trade_widget", lambda ticker, refresh=False: {"widget_id": "tp_M&M_abcdef123456"})
    monkeypatch.setattr(widget_store, "persist_trade_widget", lambda widget: None)
    body = advisory_routes.ApproveCandidateRequest(ticker="M&M", strategy_name="x")
    with pytest.raises(HTTPException) as exc:
        advisory_routes.prepare_advisory_widget(body)
    assert exc.value.status_code == 400


def test_first_fire_rule_is_one_function_for_every_create_surface() -> None:
    from src.scheduled_research.playbooks import get_playbook

    # interval and timezone-less jobs fire at creation; a timezone-carrying cron fires at its authored slot
    assert first_fire_ms("60000", NOW_MS, IST) == NOW_MS
    assert first_fire_ms(CRON, NOW_MS, None) == NOW_MS
    assert first_fire_ms(CRON, NOW_MS, IST) == next_due(CRON, NOW_MS, IST)
    # the playbook surface agrees (it used to carry its own copy of the rule)
    job = get_playbook("premarket-brief").to_job(schedule=CRON, timezone=IST, now_ms=NOW_MS)
    assert job.next_run_at == next_due(CRON, NOW_MS, IST)


def test_cancelling_a_recording_wake_loads_inside_the_store_transaction(store, monkeypatch) -> None:
    from src.scheduled_research import store as store_module
    from src.trade.recording_wait_scheduler import cancel_recording_wake, schedule_recording_wake

    schedule_recording_wake(
        recording_job_id="rec-1", next_open_at=datetime(2099, 1, 1, tzinfo=timezone.utc), store=store
    )
    store.upsert(_job("other", status=JobStatus.PENDING))
    load_depths: list[int] = []
    real_load = store.load

    def _load():
        load_depths.append(store_module._HELD.__dict__.get("depths", {}).get(str(store.path), 0))
        return real_load()

    monkeypatch.setattr(store, "load", _load)
    # a bare load..save overwrote whatever the executor wrote between the two (the lost-write class the
    # store's transaction() exists to stop): the snapshot must be read while the store is held
    assert cancel_recording_wake(recording_job_id="rec-1", store=store) is True
    assert load_depths and all(depth > 0 for depth in load_depths), load_depths
    assert cancel_recording_wake(recording_job_id="rec-1", store=store) is False
    assert set(real_load()) == {"other"}


def test_supplement_worker_starts_the_simulator_backfill_run(rec, monkeypatch) -> None:
    """The end-of-session gap-fill called `StockSimulatorClient.supplement_today`, which does not exist, so it
    never ran (the failure was only a log line in a job that is pruned an hour later). An autospec of the real
    client makes that a bind error; the run is the one the daily coverage sweep starts (D195)."""
    from unittest.mock import create_autospec

    from src.trade import recording_supplement_worker as worker
    from src.trade import stock_simulator_facade
    from trade_integrations.stock_simulator.client import StockSimulatorClient

    client = create_autospec(StockSimulatorClient, instance=True)
    client.start_backfill_run.return_value = {"run": {"run_id": "run-1"}}
    client.get_backfill_run.return_value = {
        "logs": [{"seq": 1, "message": "bucket macro_factors done"}],
        "run": {"status": "done", "summary": {
            "had_errors": False, "ok_count": 3, "failed_count": 0, "skipped_count": 1, "results": [],
        }},
    }
    monkeypatch.setattr(stock_simulator_facade, "sim_client", lambda: client)
    job_id, _ = rec.start_job(underlyings=["NIFTY"])

    worker.run_supplement(job_id, "2026-10-14")

    kwargs = client.start_backfill_run.call_args.kwargs
    assert (kwargs["week_start"], kwargs["weeks"], kwargs["include_optional"]) == ("2026-10-14", 1, True)
    messages = [entry["message"] for entry in rec.get_job(job_id)["logs"]]
    assert "bucket macro_factors done" in messages, "run progress reaches the job log"
    assert any(m.startswith("complete: had_errors=False ok=3 failed=0 skipped=1") for m in messages), messages
    assert not any(m.startswith("failed:") for m in messages)


def test_a_waiting_recording_is_found_by_another_process_however_old_its_file(rec) -> None:
    """The cross-process scan skips job files not written for 12 h (no live worker writes that rarely), but a
    `waiting_for_open` recording has no worker at all and its file sits untouched from Friday evening to Monday."""
    job_id, _ = rec.start_job(underlyings=["NIFTY"], wait_for_open=True)
    assert rec.set_waiting_for_open_at(job_id, next_open_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    three_days_ago = time.time() - 3 * 86400
    os.utime(rec._job_file(job_id), (three_days_ago, three_days_ago))
    rec.RECORDING_JOBS.clear()
    rec._ACTIVE_BY_SCOPE.clear()  # a script process that never saw the job
    assert rec.start_job(underlyings=["BANKNIFTY"]) == (job_id, True)


def test_one_failing_open_position_does_not_stop_the_other_thesis_checks(monkeypatch) -> None:
    """options_position_monitor: a raise on one ledger entry used to abort the tick for every other open
    position; now it is reported (had_errors) and the rest are still evaluated."""
    from src.scheduled_research import options_jobs
    from src.scheduled_research.run_outcome import JobRunHadErrorsError, finish_run

    seen: list[str] = []

    class _Service:
        def evaluate_position_thesis(self, widget_id):
            seen.append(widget_id)
            if widget_id == "w1":
                raise RuntimeError("ledger row unreadable")
            return None

    monkeypatch.setattr(options_jobs, "is_options_monitor_active", lambda: True)
    monkeypatch.setattr("trade_integrations.monitor.service.MonitorService", _Service)
    monkeypatch.setattr(
        "trade_integrations.monitor.execution_ledger.list_open_entries",
        lambda: [{"widget_id": "w1", "underlying": "NIFTY"}, {"widget_id": "w2", "underlying": "NIFTY"}],
    )
    result = options_jobs.run_options_position_monitor_job({})
    assert seen == ["w1", "w2"]
    assert result["had_errors"] and list(result["errors"]) == ["w1"]
    with pytest.raises(JobRunHadErrorsError, match="w1: ledger row unreadable"):
        finish_run(_job("opt", status=JobStatus.RUNNING), result, "options position monitor")


def test_every_pipeline_job_type_has_a_section_and_an_unclaimed_one_is_refused() -> None:
    """dst_eval / factor_health / execution_advisor jobs used to land in "general"; an unclaimed job_type
    used to fall through to the legacy LLM-agent prompt path with the job's stored prompt."""
    from src.scheduled_research.dst_eval_jobs import DST_EVAL_JOB_TYPES
    from src.scheduled_research.execution_advisor_jobs import EXECUTION_ADVISOR_JOB_TYPES
    from src.scheduled_research.factor_health_jobs import FACTOR_HEALTH_JOB_TYPES
    from src.scheduled_research.job_dispatch_registry import try_dispatch_pipeline_job
    from src.scheduled_research.sections import GENERAL_SECTION, job_section

    for job_type in DST_EVAL_JOB_TYPES | EXECUTION_ADVISOR_JOB_TYPES | FACTOR_HEALTH_JOB_TYPES:
        assert job_section(job_type) != GENERAL_SECTION, job_type
    retired = _job("old", status=JobStatus.PENDING, job_type="external_predictions_refresh")
    with pytest.raises(ValueError, match="no pipeline claims"):
        asyncio.run(try_dispatch_pipeline_job(retired))


def test_the_verdict_is_the_last_verdict_heading_and_a_restarted_stage_is_closed() -> None:
    from src.scheduled_research.stage_log import build_stage_log
    from src.scheduled_research.verdict import PARSE_OK, parse_verdict_section

    briefing = "Format example:\n## Verdict\nnot a line\n\nanalysis...\n\n## Verdict\n- NIFTY: LONG - breadth\n"
    parse, items = parse_verdict_section(briefing)
    assert parse == PARSE_OK and [(i.symbol, i.state) for i in items] == [("NIFTY", "LONG")]
    log = build_stage_log(
        [{"at": 1000, "message": "stage a: starting"}, {"at": 4000, "message": "stage a: starting"},
         {"at": 6000, "message": "stage a: done in 2s"}],
        ended_at_ms=9000,
    )
    assert [(r["started_at"], r["ended_at"], r["status"]) for r in log] == [(1000, 4000, "unfinished"), (4000, 6000, "ok")]


def test_an_unparsable_scheduler_env_value_is_named_in_the_log(caplog) -> None:
    """2026-10-14-vibe-scheduler-env-parse-silent: `45m` ran on the default with no trace."""
    from src.scheduled_research import staleness

    staleness._WARNED_ENV.clear()
    with caplog.at_level("WARNING"):
        assert staleness._env_int("45m", "SCHEDULED_RESEARCH_DISPATCH_TIMEOUT_MS", 7, 1) == 7
        assert staleness._env_int("45m", "SCHEDULED_RESEARCH_DISPATCH_TIMEOUT_MS", 7, 1) == 7  # once, not per tick
    assert [r.getMessage() for r in caplog.records].count(
        "SCHEDULED_RESEARCH_DISPATCH_TIMEOUT_MS='45m' is not an integer; using the default 7"
    ) == 1
    assert staleness._env_int("", "X", 7, 1) == 7 and not caplog.records[1:]
    assert staleness._env_int(" 3 ", "X", 7, 5) == 5  # parsed, then floored


def test_reregistering_an_agent_keeps_its_jobs_run_state(store, monkeypatch) -> None:
    """A restart/resume re-registers every agent job; an operator pause, failure counters and
    created_at must survive it (2026-10-14-vibe-register-agent-jobs-clobbers-run-state)."""
    from src.scheduled_research import autonomous_agent_jobs as aaj

    monkeypatch.setattr(aaj, "is_autonomous_scheduler_enabled", lambda: True)
    monkeypatch.setattr("src.scheduled_research.store.ScheduledResearchJobStore", lambda *a, **k: store)
    agent = {"id": "aa_k", "status": "running", "bootstrap_status": "done", "symbols": ["NIFTY"], "schedules": {}}
    aaj.register_agent_jobs(agent)
    ids = [j for j in store.load() if j.startswith("aa_k-")]
    assert len(ids) == 6
    for jid in ids:
        job = store.get(jid)
        job.paused, job.consecutive_failures, job.last_error, job.created_at = True, 2, "boom", 5
        store.upsert(job)
    aaj.register_agent_jobs(agent)
    for jid in ids:
        job = store.get(jid)
        assert (job.paused, job.consecutive_failures, job.last_error, job.created_at) == (True, 2, "boom", 5), jid
    # a job that is mid-run is not touched at all
    store.update_run_state("aa_k-news", status=JobStatus.RUNNING, next_run_at=7)
    aaj.register_agent_jobs(agent)
    assert store.get("aa_k-news").next_run_at == 7
    # paused only by the D80 switch: switching it on resumes the job
    monkeypatch.setattr(aaj, "is_autonomous_scheduler_enabled", lambda: False)
    aaj.register_agent_jobs({**agent, "id": "aa_d"})
    assert store.get("aa_d-watch").paused
    monkeypatch.setattr(aaj, "is_autonomous_scheduler_enabled", lambda: True)
    aaj.register_agent_jobs({**agent, "id": "aa_d"})
    assert not store.get("aa_d-watch").paused


def test_session_models_define_principal_once_and_service_stamps_utc():
    """D306: models.py had two Principal/AuthMethod copies (the later shadowed the first), and
    SessionService stamped session.updated_at in naive local time while every other timestamp is UTC,
    so on an IST host list_sessions' string sort ranked a session by local-time-as-if-UTC."""
    import ast

    from src.session import models, service

    tree = ast.parse(Path(models.__file__).read_text(encoding="utf-8"))
    for name in ("Principal", "AuthMethod"):
        assert sum(isinstance(n, ast.ClassDef) and n.name == name for n in tree.body) == 1, name
    assert "datetime.now()" not in Path(service.__file__).read_text(encoding="utf-8")
    assert models.Principal("s", models.AuthMethod.SHARED_KEY).attributable is False


# --- R2 seams: agent loop / grounding / skills / run state (review-seams-vibe-fork, agent+core) ---


def test_load_skill_by_path_cannot_escape_the_user_skills_dir(tmp_path) -> None:
    """get_content's on-disk fallback took the model-supplied name as a path: ``../evil`` loaded any SKILL.md."""
    from src.agent.skills import SkillsLoader

    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "SKILL.md").write_text("---\nname: evil\n---\nsecret body", encoding="utf-8")
    (tmp_path / "user" / "ok").mkdir(parents=True)
    (tmp_path / "user" / "ok" / "SKILL.md").write_text("---\nname: ok\n---\nfine", encoding="utf-8")
    loader = SkillsLoader(skills_dir=tmp_path / "none", user_skills_dir=tmp_path / "user")
    loader.skills.clear()  # force the mid-session disk fallback
    assert loader.get_content("../evil").startswith("Error:")
    assert loader.get_content(str(tmp_path / "evil")).startswith("Error:")
    assert "fine" in loader.get_content("ok")


def test_run_state_write_is_atomic(tmp_path, monkeypatch) -> None:
    """state.json was written in place: a crash mid-write left it truncated (comment claimed otherwise)."""
    from src.core.state import RunStateStore

    store = RunStateStore()
    store.mark_success(tmp_path)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("crash before rename")))
    with pytest.raises(OSError):
        store.mark_failure(tmp_path, "x")
    assert '"success"' in (tmp_path / "state.json").read_text(encoding="utf-8")


def test_inr_quote_with_rupee_sign_surfaces_currency() -> None:
    """An INR answer writes ₹; the alias table only knew 'inr'/卢比, so every .NS/.BO price answer was rejected."""
    from src.agent.grounding import GroundingLedger

    assert GroundingLedger._currency_is_surfaced("INR", "RELIANCE.NS closed at ₹2,950")
    assert not GroundingLedger._currency_is_surfaced("INR", "RELIANCE.NS closed at 2,950")


def test_huge_derivation_chain_is_not_a_crash() -> None:
    """A long ``1+1+...`` chain raised RecursionError out of the final-answer gate and failed the whole run."""
    from src.agent.grounding import GroundingLedger

    assert GroundingLedger._evaluate_formula("+".join(["1"] * 3000)) is None


def test_prompt_date_carries_the_ist_calendar_day() -> None:
    """19:00 UTC is already the next day in IST; Indian sessions follow the IST date."""
    from src.agent.context import _now_label

    label = _now_label(datetime(2026, 10, 14, 19, 0, tzinfo=timezone.utc))
    assert "Wednesday, October 14, 2026 19:00 UTC" in label
    assert "Thursday, October 15, 2026 00:30 IST" in label


def test_compaction_transcript_is_redacted_like_the_trace() -> None:
    from src.agent.loop import _redacted_for_transcript

    tool = {"role": "tool", "content": '{"status": "ok", "api_key": "sk-live-abcdefghijklmnop0123"}'}
    call = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "t", "arguments": '{"password": "hunter2hunter2"}'}}]}
    assert "sk-live-abcdefghijklmnop0123" not in _redacted_for_transcript(tool)["content"]
    assert "hunter2hunter2" not in _redacted_for_transcript(call)["tool_calls"][0]["function"]["arguments"]


def test_backtest_archive_does_not_follow_symlinks(tmp_path, monkeypatch) -> None:
    """Generated code can leave a symlink in artifacts/; copytree dereferenced it into the served run dir."""
    import json

    from src.agent import loop

    src_run, dst_run = tmp_path / "src", tmp_path / "dst"
    (src_run / "artifacts").mkdir(parents=True)
    (src_run / "artifacts" / "metrics.csv").write_text("a\n1\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    (src_run / "artifacts" / "leak.txt").symlink_to(secret)
    dst_run.mkdir()
    monkeypatch.setattr(loop, "safe_run_dir", lambda p: Path(p))
    assert loop._archive_backtest_result(json.dumps({"run_dir": str(src_run)}), str(dst_run))
    assert (dst_run / "artifacts" / "metrics.csv").is_file()
    assert not (dst_run / "artifacts" / "leak.txt").exists()


def test_tool_result_provenance_and_news_scenario_sync_are_wired(tmp_path, monkeypatch) -> None:
    """Both hooks were dropped by the upstream merge: tool provenance has no other writer (service_hooks
    skips tool_result) and active_draft_id is only set here or by the API route."""
    from types import SimpleNamespace

    from src.agent import loop as loop_mod
    from src.agent.context import ContextBuilder
    from src.agent.tools import ToolRegistry
    from src.agent.trace import TraceWriter
    from src.provenance import hook
    from src.session import news_scenario_session as nss

    synced = []
    monkeypatch.setattr(nss, "sync_news_scenario_session_from_tool_result", lambda *a: synced.append(a))
    monkeypatch.setattr(
        hook, "record_tool_result",
        lambda *a, **k: SimpleNamespace(ref_id="src_1", display_name="Quote", to_dict=lambda: {}),
    )
    agent = loop_mod.AgentLoop(ToolRegistry(), SimpleNamespace(model_name="m"))
    agent._session_id = "sess-1"
    messages: list = []
    tc = SimpleNamespace(id="call-1", name="get_quote", arguments={})
    agent._finalize_tool_result(tc, '{"status": "ok"}', 1, ContextBuilder, messages, TraceWriter(tmp_path), [], 1)
    assert synced and synced[0][:2] == ("sess-1", "get_quote")
    assert "[Source: [[src_1|Quote]]]" in messages[-1]["content"]
