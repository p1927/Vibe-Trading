"""index_jobs' call to `backfill_participant_oi` must match its real signature.

The factor-snapshot stage passed `sleep_seconds=` after the function dropped it (the binding sleeps
itself), so `stage backfill_participant_oi` failed on every run of the weekly index-research job
("unexpected keyword argument 'sleep_seconds'", release 2026-10-13).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


def test_backfill_participant_oi_call_binds_to_signature():
    from trade_integrations.dataflows.index_research.participant_oi_backfill import backfill_participant_oi

    src = (Path(__file__).resolve().parents[1] / "src/scheduled_research/index_jobs.py").read_text()
    calls = [
        n
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "backfill_participant_oi"
    ]
    assert calls, "index_jobs no longer calls backfill_participant_oi"
    for call in calls:
        inspect.signature(backfill_participant_oi).bind(
            *[None] * len(call.args), **{kw.arg: None for kw in call.keywords}
        )
