"""The per-stage trail of one scheduled run, kept on the run record.

Fork-only sidecar for ``executor.py`` (an upstream file), per docs/FORK_CONVENTIONS.md.

A stage-aware job reports ``stage <name>: starting`` / ``done in <N>s|ms`` / ``failed after <N>s|ms[: err]``
lines through ``pipeline_cancel.emit_stage_event`` into the job's in-memory log buffer
(``run_log_buffer``, 500 lines, gone with the process). That is how a run that hit its dispatch timeout
left nothing readable: ``nifty-index-research`` timed out 4 of its last 5 runs and which stage ate the
30 minutes had to be derived from budget constants. The executor now turns the buffer into structured
records at the end of every run, whatever the outcome, and stores them in ``last_stage_log`` on the
run record (the existing run-state store, no new store).

One record per stage occurrence: ``stage``, ``started_at``/``ended_at`` (epoch ms), ``duration_ms``,
``status`` (``ok`` | ``failed`` | ``unfinished``) and ``error``. A stage with no end line is
``unfinished``: its ``ended_at`` is the run's end, so on a timeout the last such record is the stage
the time went into.
"""

from __future__ import annotations

import re
from typing import Any

from src.scheduled_research.run_log_buffer import get_logs_since

#: Most stage records kept on a run record; the newest are kept (a timed-out run's last stages matter).
MAX_STAGE_RECORDS = 60
_MAX_ERROR_CHARS = 300

_LINE = re.compile(
    r"^stage (?P<name>[^:]+): (?:(?P<start>starting)"
    r"|done in (?P<dn>[\d.]+)(?P<du>ms|s)"
    r"|failed after (?P<fn>[\d.]+)(?P<fu>ms|s)(?:: (?P<err>.*))?)$"
)


def _ms(number: str, unit: str) -> int:
    return int(float(number) * (1.0 if unit == "ms" else 1000.0))


def build_stage_log(entries: list[dict[str, Any]], *, ended_at_ms: int) -> list[dict[str, Any]]:
    """Stage records from a job's log-buffer ``entries`` (``{"at": epoch ms, "message": str}``)."""
    records: list[dict[str, Any]] = []
    open_by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        match = _LINE.match(str(entry.get("message") or "").strip())
        if match is None:
            continue
        name, at = match["name"], int(entry.get("at") or 0)
        if match["start"]:
            earlier = open_by_name.get(name)
            if earlier is not None:  # started again with no end line: the first one ended where this began
                earlier.update(ended_at=at, duration_ms=max(0, at - int(earlier["started_at"])))
            record = {
                "stage": name, "started_at": at, "ended_at": None,
                "duration_ms": None, "status": "unfinished", "error": None,
            }
            records.append(record)
            open_by_name[name] = record
            continue
        failed = match["fn"] is not None
        duration = _ms(match["fn"], match["fu"]) if failed else _ms(match["dn"], match["du"])
        record = open_by_name.pop(name, None)
        if record is None:  # an end line with no start line: the duration places its start
            record = {"stage": name, "started_at": at - duration, "error": None}
            records.append(record)
        record.update(
            ended_at=at, duration_ms=duration, status="failed" if failed else "ok",
            error=(match["err"] or None) and match["err"][:_MAX_ERROR_CHARS],
        )
    for record in open_by_name.values():
        record["ended_at"] = ended_at_ms
        record["duration_ms"] = max(0, ended_at_ms - int(record["started_at"]))
    return records[-MAX_STAGE_RECORDS:]


def stage_log_for_run(job_id: str, *, ended_at_ms: int) -> list[dict[str, Any]] | None:
    """The run's stage records from the job's live log buffer, or ``None`` when it reported no stages."""
    return build_stage_log(get_logs_since(job_id), ended_at_ms=ended_at_ms) or None
