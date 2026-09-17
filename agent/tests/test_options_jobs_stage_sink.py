"""dispatch_options_job_sync binds a stage-progress sink around dispatch, same pattern as
index_jobs.dispatch_index_job_sync -- options jobs have the clearest documented dispatch-timeout
history of any scheduled module (2026-08-27-scheduler-dispatch-timeouts), so seeing which ticker
a stuck run was on matters here specifically.
"""

from __future__ import annotations

from src.scheduled_research import options_jobs
from src.scheduled_research.models import JobStatus, ScheduledResearchJob
from trade_integrations.dataflows.index_research.pipeline_cancel import emit_stage_event


def _job(job_id: str = "job-1", **fields) -> ScheduledResearchJob:
    fields.setdefault("status", JobStatus.PENDING)
    fields.setdefault("schedule", "1000")
    fields.setdefault("next_run_at", 0)
    fields.setdefault("config", {"job_type": "options_plan_refresh"})
    return ScheduledResearchJob(id=job_id, prompt="p", created_at=0, **fields)


def test_dispatch_binds_and_clears_stage_sink(monkeypatch) -> None:
    def fake_body(job: ScheduledResearchJob) -> None:
        emit_stage_event("stage inside dispatch")

    monkeypatch.setattr(options_jobs, "_dispatch_options_job_body", fake_body)
    job = _job()

    options_jobs.dispatch_options_job_sync(job)

    from src.scheduled_research.run_log_buffer import get_logs_since

    seen = [entry["message"] for entry in get_logs_since(job.id)]
    assert "stage inside dispatch" in seen

    # Sink must be cleared after dispatch -- a later, unrelated emit must go nowhere.
    emit_stage_event("must not be captured")
    seen_after = [entry["message"] for entry in get_logs_since(job.id)]
    assert "must not be captured" not in seen_after


def test_dispatch_clears_stage_sink_even_on_failure(monkeypatch) -> None:
    def fake_body(job: ScheduledResearchJob) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(options_jobs, "_dispatch_options_job_body", fake_body)
    job = _job("job-2")

    try:
        options_jobs.dispatch_options_job_sync(job)
    except RuntimeError:
        pass

    emit_stage_event("must not be captured either")
    from src.scheduled_research.run_log_buffer import get_logs_since

    seen = [entry["message"] for entry in get_logs_since("job-2")]
    assert "must not be captured either" not in seen
