"""register_default_index_jobs must reconcile drift into already-registered jobs.

Before this fix, `register_default_index_jobs` only inserted a default job when it was
missing from the store (`if store.get(job.id) is not None: continue`) — any later code
change to a job's `schedule`/`config`/`prompt` was silently inert for a deployment where
the job id was already registered. Live-verified 2026-08-31: two landed fixes for
`nifty-hub-news-ingest-tight`/`-light` (a `dispatch_timeout_ms` override, a `*/5`->`*/15`
cadence widen) each reached the dev job store inconsistently or not at all.
See .claude/backlog/items/2026-08-31-default-job-registration-never-updates-existing-jobs.md.
"""

from __future__ import annotations

import dataclasses

import pytest

from src.scheduled_research import index_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.store import ScheduledResearchJobStore


@pytest.mark.unit
def test_reconciles_schedule_and_config_drift_on_existing_job(tmp_path):
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    stale = store.get("us-hub-news-ingest-tight")
    assert stale is not None
    stale = dataclasses.replace(
        stale,
        schedule="*/5 * * * *",
        config={**stale.config, "dispatch_timeout_ms": 600_000},
    )
    store.upsert(stale)

    index_jobs.register_default_index_jobs(store)

    reconciled = store.get("us-hub-news-ingest-tight")
    assert reconciled is not None
    assert reconciled.schedule == "*/15 * * * *"
    assert reconciled.config["dispatch_timeout_ms"] == 20 * 60 * 1000


@pytest.mark.unit
def test_reconcile_preserves_runtime_state(tmp_path):
    """Reconciling code-owned fields must never clobber executor-owned runtime state."""
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    running = store.get("us-hub-news-ingest-light")
    assert running is not None
    running = dataclasses.replace(
        running,
        schedule="0 */99 * * *",  # deliberately stale/wrong, must be corrected
        status=JobStatus.RUNNING,
        last_run_at=12345,
        consecutive_failures=2,
        last_error="TimeoutError: dispatch timed out after 600000ms",
        failure_kind="dispatch",
        paused=True,
        auto_paused_reason="stale-running recovery",
    )
    store.upsert(running, validate=False)

    index_jobs.register_default_index_jobs(store)

    reconciled = store.get("us-hub-news-ingest-light")
    assert reconciled is not None
    # Code-owned fields corrected back to the current default.
    assert reconciled.schedule != "0 */99 * * *"
    # Executor-owned runtime state left untouched.
    assert reconciled.status == JobStatus.RUNNING
    assert reconciled.last_run_at == 12345
    assert reconciled.consecutive_failures == 2
    assert reconciled.last_error == "TimeoutError: dispatch timed out after 600000ms"
    assert reconciled.failure_kind == "dispatch"
    assert reconciled.paused is True
    assert reconciled.auto_paused_reason == "stale-running recovery"


@pytest.mark.unit
def test_reconcile_preserves_executor_injected_config_scratch_keys(tmp_path):
    """Runtime scratch keys the executor writes into `config` must survive reconciliation."""
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    tight = store.get("us-hub-news-ingest-tight")
    assert tight is not None
    tight = dataclasses.replace(
        tight, config={**tight.config, "_timed_out": True, "_last_result_summary": {"n": 1}}
    )
    store.upsert(tight)

    index_jobs.register_default_index_jobs(store)

    reconciled = store.get("us-hub-news-ingest-tight")
    assert reconciled is not None
    assert reconciled.config["_timed_out"] is True
    assert reconciled.config["_last_result_summary"] == {"n": 1}
    # Code-defined keys are still correct.
    assert reconciled.config["dispatch_timeout_ms"] == 20 * 60 * 1000


@pytest.mark.unit
def test_reconcile_is_noop_when_nothing_drifted(tmp_path, monkeypatch):
    """A second registration pass with no code changes must not rewrite the store.

    Scoped to this test's own store *instance* (not the class) — a global
    class-level patch would also intercept the unrelated internal store
    ``sync_scheduled_jobs_from_config()`` opens against the sandboxed test
    HOME (see agent/tests/conftest.py), which legitimately re-upserts its own
    handful of job ids on every call and would make this test flaky for
    reasons that have nothing to do with the reconciliation logic under test.
    """
    store = ScheduledResearchJobStore(tmp_path / "jobs.json")
    index_jobs.register_default_index_jobs(store)

    calls = []
    original_upsert = store.upsert

    def _tracking_upsert(job: ScheduledResearchJob, *, validate: bool = True) -> None:
        calls.append(job.id)
        return original_upsert(job, validate=validate)

    monkeypatch.setattr(store, "upsert", _tracking_upsert)

    created = index_jobs.register_default_index_jobs(store)

    assert created == 0
    assert calls == []
