"""Recover Vibe session attempts left in ``running`` after API restart."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.session.models import Attempt, AttemptStatus

logger = logging.getLogger(__name__)

_STALE_RUNNING_SECONDS = 120


def recover_stale_running_attempts(
    store,
    *,
    stale_after_seconds: int = _STALE_RUNNING_SECONDS,
) -> list[dict[str, Any]]:
    """Mark orphaned ``running`` attempts as failed after restart."""
    base_dir: Path = store.base_dir
    if not base_dir.is_dir():
        return []

    now = datetime.now(timezone.utc)
    recovered: list[dict[str, Any]] = []

    for session_dir in base_dir.iterdir():
        if not session_dir.is_dir():
            continue
        attempts_dir = session_dir / "attempts"
        if not attempts_dir.is_dir():
            continue
        session_id = session_dir.name
        for attempt_dir in attempts_dir.iterdir():
            if not attempt_dir.is_dir():
                continue
            attempt_file = attempt_dir / "attempt.json"
            if not attempt_file.is_file():
                continue
            try:
                data = json.loads(attempt_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            attempt = Attempt.from_dict(data)
            if attempt.status != AttemptStatus.RUNNING:
                continue

            age_seconds = stale_after_seconds + 1
            try:
                created = datetime.fromisoformat(attempt.created_at.replace("Z", "+00:00"))
                age_seconds = (now - created).total_seconds()
            except (TypeError, ValueError):
                pass

            if age_seconds < stale_after_seconds:
                continue

            attempt.mark_failed("Recovered after API restart — prior turn did not complete")
            store.update_attempt(attempt)
            entry = {
                "session_id": session_id,
                "attempt_id": attempt.attempt_id,
                "age_seconds": age_seconds,
            }
            recovered.append(entry)
            logger.warning(
                "Recovered stale running attempt %s in session %s (%.0fs old)",
                attempt.attempt_id,
                session_id,
                age_seconds,
            )

    return recovered
