"""A code edit to a default job's definition reaches an already-stored job in EVERY family, not only the index jobs."""

from __future__ import annotations

import dataclasses
import tempfile
import time
from pathlib import Path

from src.scheduled_research.default_jobs import register_all_default_jobs
from src.scheduled_research.store import ScheduledResearchJobStore

NON_INDEX = "decision-grading-sweep"


def _store() -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(Path(tempfile.mkdtemp()) / "jobs.json")


def test_a_stale_cron_prompt_and_config_on_a_non_index_family_job_are_carried_and_state_is_kept():
    store = _store()
    register_all_default_jobs(store)
    code = store.get(NON_INDEX)
    far = int(time.time() * 1000) + 365 * 86_400_000
    store.upsert(dataclasses.replace(
        code, schedule="0 3 * * 1", timezone=None, prompt="old prompt", next_run_at=far,
        config={**code.config, "_timed_out": True, "stale_key": 1}, paused=True, consecutive_failures=2,
    ))

    register_all_default_jobs(store)

    fixed = store.get(NON_INDEX)
    assert (fixed.schedule, fixed.timezone, fixed.prompt) == (code.schedule, code.timezone, code.prompt)
    assert fixed.next_run_at < far  # not-yet-due run retimed on the corrected schedule
    assert fixed.config["_timed_out"] is True and fixed.config["stale_key"] == 1  # executor scratch survives (merge)
    assert fixed.paused is True and fixed.consecutive_failures == 2  # runtime state untouched


def test_reconcile_is_idempotent_leaves_overdue_runs_and_user_jobs_alone():
    store = _store()
    register_all_default_jobs(store)
    before = {j.id: j for j in store.load().values()}
    register_all_default_jobs(store)
    assert {j.id: j for j in store.load().values()} == before  # nothing to carry: nothing rewritten

    code = store.get(NON_INDEX)
    overdue = 1_000
    store.upsert(dataclasses.replace(code, schedule="0 3 * * 1", next_run_at=overdue))
    mine = dataclasses.replace(code, id="my-own-job", schedule="0 4 * * *", timezone=None)
    store.upsert(mine)
    register_all_default_jobs(store)
    assert store.get(NON_INDEX).next_run_at == overdue  # overdue still fires at once
    assert store.get("my-own-job") == mine  # not a code default: left alone
