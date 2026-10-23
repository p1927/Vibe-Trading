"""`run_nse_macro_refresh_job` must not end `completed` when the repository mirror ingested nothing.

The scheduler fails a run only on `had_errors` or `status == "error"` (`run_outcome.raise_if_run_had_errors`), and the
job's summary used to be `{"seed_layers", "repository"}` with neither, so an empty repository was a green run
(Trade backlog 2026-10-23-r9-false-success-leftovers item 6). Mirrors Trade's `oi_bhavcopy_history` `no_data` + `had_errors`.
"""
from __future__ import annotations

import sys
import types

import pytest

from src.scheduled_research import trade_data_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from src.scheduled_research.run_outcome import JobRunHadErrorsError, finish_run


@pytest.fixture
def repo(monkeypatch):
    """A fake `trade_integrations.nse_browser.repository` whose repository counts the test sets."""
    state = {"repo_counts": {}, "seed": {"fii_dii": 3}}
    module = types.ModuleType("trade_integrations.nse_browser.repository")
    module.sync_light_repo_seed_layers = lambda **_kw: dict(state["seed"])
    module.ingest_repository_to_hub = lambda **_kw: dict(state["repo_counts"])
    for name in ("trade_integrations", "trade_integrations.nse_browser"):
        monkeypatch.setitem(sys.modules, name, sys.modules.get(name) or types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "trade_integrations.nse_browser.repository", module)
    monkeypatch.setattr(trade_data_jobs, "_ensure_trade_integrations_on_path", lambda: None)
    return state


def _job() -> ScheduledResearchJob:
    return ScheduledResearchJob(
        id="nse-macro-refresh", prompt="p", schedule="1000", next_run_at=0, status=JobStatus.PENDING, created_at=0,
    )


def test_nothing_ingested_is_an_error_not_a_completed_run(repo):
    repo["repo_counts"] = {}
    out = trade_data_jobs.run_nse_macro_refresh_job({})
    assert out["status"] == "no_data" and out["had_errors"] is True and out["error"]
    with pytest.raises(JobRunHadErrorsError):
        finish_run(_job(), out, "nse macro refresh")


@pytest.mark.parametrize("counts", [{"fii_dii": 0, "delivery": 0}, {"hub_mirror_skipped": 0}])
def test_all_zero_counts_are_nothing_ingested_too(repo, counts):
    repo["repo_counts"] = counts
    assert trade_data_jobs.run_nse_macro_refresh_job({})["had_errors"] is True


def test_ingested_rows_are_a_clean_ok(repo):
    repo["repo_counts"] = {"fii_dii": 120, "delivery": 0}
    out = trade_data_jobs.run_nse_macro_refresh_job({})
    assert out["status"] == "ok" and "had_errors" not in out and out["repository"] == {"fii_dii": 120, "delivery": 0}
    finish_run(_job(), out, "nse macro refresh")


def test_an_unchanged_mirror_skip_is_not_an_outage(repo):
    repo["repo_counts"] = {"hub_mirror_skipped": 1}
    out = trade_data_jobs.run_nse_macro_refresh_job({})
    assert out["status"] == "ok" and "had_errors" not in out
