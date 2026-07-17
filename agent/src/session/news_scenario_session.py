"""News scenario session config helpers (Vibe runtime)."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.session.news_scenario_profile import SESSION_KIND_NEWS_SCENARIO

logger = logging.getLogger(__name__)

_SAVE_DRAFT_TOOL_MARKERS = ("save_news_scenario_draft",)
_RUN_SCENARIO_TOOL_MARKERS = ("run_news_event_scenario",)


def patch_news_scenario_session_fields(session_id: str, updates: dict[str, Any]) -> None:
    """Merge fields into a news-scenario advisor session config."""
    if not session_id or not updates:
        return
    try:
        from src.api.state import _get_session_service

        svc = _get_session_service()
        if svc is None:
            return
        session = svc.get_session(session_id)
        if session is None:
            return
        cfg = dict(session.config or {})
        if str(cfg.get("session_kind") or "") != SESSION_KIND_NEWS_SCENARIO:
            return
        cfg.update(updates)
        session.config = cfg
        svc.store.update_session(session)
    except Exception:
        logger.exception("news-scenario session patch failed for %s", session_id)


def sync_news_scenario_session_from_tool_result(
    session_id: str,
    tool_name: str,
    result: str,
) -> None:
    """Update active_draft_id / active_scenario_id after MCP draft or quant tools."""
    if not session_id:
        return
    name = str(tool_name or "")
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(data, dict) or data.get("status") != "ok":
        return

    updates: dict[str, Any] = {}
    if any(marker in name for marker in _SAVE_DRAFT_TOOL_MARKERS):
        draft = data.get("draft")
        if isinstance(draft, dict) and draft.get("draft_id"):
            updates["active_draft_id"] = draft["draft_id"]
    if any(marker in name for marker in _RUN_SCENARIO_TOOL_MARKERS):
        scenario = data.get("scenario")
        if isinstance(scenario, dict):
            if scenario.get("scenario_id"):
                updates["active_scenario_id"] = scenario["scenario_id"]
            if scenario.get("draft_id"):
                updates["active_draft_id"] = scenario["draft_id"]
    if updates:
        patch_news_scenario_session_fields(session_id, updates)
