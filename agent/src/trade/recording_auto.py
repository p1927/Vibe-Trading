"""Persisted "Auto Record" toggle: re-arm a wait_for_open recording daily.

When enabled, this does not introduce a new scheduling mechanism — it just
means "every time the recording finishes (or there's no job for the
session), start a fresh one with wait_for_open=True". Since
``next_real_nse_open_at`` already rolls forward to the next weekday session
when called after today's close, kicking a fresh wait_for_open job right
after one ends naturally re-arms for the following trading day. The
recording poller (:mod:`src.trade.recording_wait_scheduler`) checks this
flag once per tick and does the re-arm; this module only owns the
persisted enabled flag + the recording config template to reuse each day.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_runtime_root

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _store_path() -> Path:
    return get_runtime_root() / "trade" / "auto_record.json"


def load_auto_record() -> dict[str, Any]:
    """Return the persisted auto-record state.

    Shape: ``{"enabled": bool, "config": dict | None, "updated_at": str | None}``.
    ``config`` mirrors the recording-start fields (underlyings, equities,
    intervals, ...) captured at the moment auto-record was last enabled.
    Missing/corrupt file → disabled with no config (fail safe, never
    fail loud on a background-poller read path).
    """
    path = _store_path()
    with _LOCK:
        if not path.exists():
            return {"enabled": False, "config": None, "updated_at": None}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("failed to read auto_record.json; treating as disabled")
            return {"enabled": False, "config": None, "updated_at": None}
    if not isinstance(raw, dict):
        return {"enabled": False, "config": None, "updated_at": None}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "config": raw.get("config") if isinstance(raw.get("config"), dict) else None,
        "updated_at": raw.get("updated_at"),
    }


def save_auto_record(*, enabled: bool, config: dict[str, Any] | None) -> dict[str, Any]:
    """Persist the auto-record toggle + recording config template.

    ``config=None`` while ``enabled=True`` is invalid at the call site
    (the API layer always supplies the current recording form's config
    when turning auto-record on); this function stores whatever it's
    given so the caller decides.
    """
    path = _store_path()
    payload = {
        "enabled": bool(enabled),
        "config": config,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    return payload


def is_auto_record_enabled() -> bool:
    return load_auto_record().get("enabled", False)
