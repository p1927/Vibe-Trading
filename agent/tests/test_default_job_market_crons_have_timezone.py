"""Every default job carries an explicit timezone: `timezone=None` means UTC, and the default crons are IST.

`HUB_EVENING_MAINTENANCE_CRON 35 18 * * *` is 18:35 IST (after the 17:30 bhavcopy), not 00:05 IST; a Mon-Fri
`*/15 9-16` is the NSE session, not 14:30-22:30 IST. A default job whose intent really is UTC must say so with
an explicit `timezone="UTC"` and a comment (`UTC_BY_INTENT` below; none today).
Run: python -m pytest tests/test_default_job_market_crons_have_timezone.py
"""
import dataclasses
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.scheduled_research.default_jobs import register_all_default_jobs
from src.scheduled_research.models import DEFAULT_JOB_TIMEZONE
from src.scheduled_research.store import ScheduledResearchJobStore

UTC_BY_INTENT: frozenset[str] = frozenset()


def _store() -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(Path(tempfile.mkdtemp()) / "jobs.json")


def test_every_default_job_has_an_explicit_timezone():
    store = _store()
    register_all_default_jobs(store)
    jobs = list(store.load().values())  # not list_jobs(): it returns at most 50 and there are ~67 defaults
    assert len(jobs) > 50, "defaults went missing; the check would be vacuous"
    bad = [j.id for j in jobs if not j.timezone]
    assert not bad, f"default jobs with null timezone (run in UTC): {bad}"
    wrong = [j.id for j in jobs if j.id not in UTC_BY_INTENT and j.timezone != DEFAULT_JOB_TIMEZONE]
    assert not wrong, f"default jobs authored in IST but carrying another timezone: {wrong}"


def test_a_stored_default_without_a_timezone_is_carried_to_ist_and_retimed():
    store = _store()
    register_all_default_jobs(store)
    legacy = store.get("hub-evening-maintenance")
    in_a_year = legacy.next_run_at + 365 * 86_400_000
    store.upsert(dataclasses.replace(legacy, timezone=None, next_run_at=in_a_year))

    register_all_default_jobs(store)

    fixed = store.get("hub-evening-maintenance")
    assert fixed.timezone == DEFAULT_JOB_TIMEZONE
    when = datetime.fromtimestamp(fixed.next_run_at / 1000, ZoneInfo("Asia/Kolkata"))
    assert (when.hour, when.minute) == (18, 35), when  # fires at 18:35 IST, within a day, not a year out
    assert fixed.next_run_at - time.time() * 1000 <= 86_400_000
    # an unrelated stored job with no timezone (user-authored, UTC on purpose) is left alone
    mine = dataclasses.replace(fixed, id="my-own-job", timezone=None)
    store.upsert(mine)
    register_all_default_jobs(store)
    assert store.get("my-own-job").timezone is None
