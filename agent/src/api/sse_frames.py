"""SSE frame builders for session tool_result events.

Each ``*_frame_from_tool_result`` function inspects a session event and, if it
matches a specific MCP tool_result shape, reloads the full persisted record
and returns a ready-to-send SSE frame string (or ``None`` if the event
doesn't match). Extracted from ``sessions_routes.py`` — pure functions of
``event``/``session_id``, no dependency on route registration.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_PROPOSAL_TOOL_NAME = "propose_mandate_profiles"
_PROPOSAL_ID_RE = re.compile(r'"proposal_id"\s*:\s*"(mp_[0-9a-f]{32})"')
_AUTONOMOUS_PROPOSAL_TOOL_NAME = "propose_autonomous_agent"
_AUTONOMOUS_PROPOSAL_ID_RE = re.compile(r'"proposal_id"\s*:\s*"(aap_[0-9a-f]{32})"')


def _load_full_proposal(proposal_id: str) -> Optional[Dict[str, Any]]:
    """Reload a persisted mandate proposal by id, broker-agnostic."""
    try:
        from src.live.paths import live_root

        for proposal_path in live_root().glob(f"*/proposals/{proposal_id}.json"):
            try:
                data = json.loads(proposal_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("type") == "mandate.proposal":
                return data
    except Exception:  # pragma: no cover - relay must never break the stream
        logger.debug("mandate.proposal reload failed for %s", proposal_id, exc_info=True)
    return None


def _mandate_proposal_frame_from_tool_result(event: Any) -> Optional[str]:
    """Build a mandate.proposal SSE frame from a propose-tool tool_result."""
    data = getattr(event, "data", None)
    if getattr(event, "event_type", None) != "tool_result" or not isinstance(data, dict):
        return None
    if data.get("tool") != _PROPOSAL_TOOL_NAME or data.get("status") != "ok":
        return None
    match = _PROPOSAL_ID_RE.search(str(data.get("preview") or ""))
    if not match:
        return None
    proposal = _load_full_proposal(match.group(1))
    if proposal is None:
        return None

    from src.session.events import SSEEvent

    frame = SSEEvent(
        event_type="mandate.proposal",
        data=proposal,
        session_id=getattr(event, "session_id", "") or "",
    )
    return frame.to_sse()


def _load_autonomous_proposal(proposal_id: str) -> Optional[Dict[str, Any]]:
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.store import load_proposal

        data = load_proposal(proposal_id)
        if isinstance(data, dict) and data.get("type") == "autonomous_agent.proposal":
            return data
    except Exception:
        logger.debug("autonomous_agent.proposal reload failed for %s", proposal_id, exc_info=True)
    return None


def _is_autonomous_propose_tool(tool_name: str | None) -> bool:
    """Match local and MCP-wrapped propose_autonomous_agent tool names."""
    name = str(tool_name or "").strip()
    if not name:
        return False
    return name == _AUTONOMOUS_PROPOSAL_TOOL_NAME or "propose_autonomous_agent" in name


def _extract_autonomous_proposal_id_from_tool_result(data: dict[str, Any]) -> str | None:
    preview = str(data.get("preview") or "")
    match = _AUTONOMOUS_PROPOSAL_ID_RE.search(preview)
    if match:
        return match.group(1)
    try:
        parsed = json.loads(preview)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, dict):
        pid = parsed.get("proposal_id")
        if isinstance(pid, str) and pid.startswith("aap_"):
            return pid
        nested = parsed.get("proposal")
        if isinstance(nested, dict):
            pid = nested.get("proposal_id")
            if isinstance(pid, str) and pid.startswith("aap_"):
                return pid
    return None


def _autonomous_agent_proposal_frame_from_tool_result(event: Any) -> Optional[str]:
    """Build autonomous_agent.proposal SSE frame from propose tool_result."""
    data = getattr(event, "data", None)
    if getattr(event, "event_type", None) != "tool_result" or not isinstance(data, dict):
        return None
    if not _is_autonomous_propose_tool(data.get("tool")) or data.get("status") != "ok":
        return None
    proposal_id = _extract_autonomous_proposal_id_from_tool_result(data)
    if not proposal_id:
        return None
    proposal = _load_autonomous_proposal(proposal_id)
    if proposal is None:
        return None
    if str(proposal.get("status") or "") not in {"ready", "incomplete"}:
        return None

    from src.session.events import SSEEvent

    frame = SSEEvent(
        event_type="autonomous_agent.proposal",
        data=proposal,
        session_id=getattr(event, "session_id", "") or "",
    )
    return frame.to_sse()


_WIDGET_DEDUP_WINDOW_MS = 15 * 60 * 1000
_recent_trade_plan_widgets: dict[str, list[tuple[int, str, str]]] = {}


def _trade_widget_strategy_key(widget: dict[str, Any]) -> str:
    rec = widget.get("recommended") if isinstance(widget.get("recommended"), dict) else {}
    return str(
        rec.get("strategy")
        or rec.get("name")
        or widget.get("strategy")
        or widget.get("widget_kind")
        or ""
    ).strip().lower()


def _should_skip_duplicate_trade_widget(session_id: str, widget: dict[str, Any]) -> bool:
    """Suppress duplicate trade_plan.widget SSE within 15m for same underlying+strategy."""
    if not session_id:
        return False
    underlying = str(widget.get("underlying") or "").upper()
    strategy = _trade_widget_strategy_key(widget)
    if not underlying:
        return False
    now_ms = int(time.time() * 1000)
    recent = [
        row
        for row in _recent_trade_plan_widgets.get(session_id, [])
        if now_ms - row[0] < _WIDGET_DEDUP_WINDOW_MS
    ]
    for ts, u, s in recent:
        if u == underlying and s == strategy:
            return True
    recent.append((now_ms, underlying, strategy))
    _recent_trade_plan_widgets[session_id] = recent[-30:]
    return False


def _trade_plan_widget_frame_from_tool_result(event: Any) -> Optional[str]:
    """Build trade_plan.widget SSE frame from options widget MCP tool_result."""
    from src.api.trade_routes import trade_plan_widget_frame_from_tool_result

    frame = trade_plan_widget_frame_from_tool_result(event)
    if frame is None:
        return None
    session_id = str(getattr(event, "session_id", "") or "")
    try:
        payload_line = frame.split("\n", 1)[-1]
        if payload_line.startswith("data:"):
            payload_line = payload_line[5:].strip()
        widget = json.loads(payload_line)
    except (json.JSONDecodeError, IndexError, TypeError):
        return frame
    if isinstance(widget, dict) and _should_skip_duplicate_trade_widget(session_id, widget):
        return None
    try:
        from src.trade.plan_widget_hook import notify_trade_plan_widget

        notify_trade_plan_widget(session_id, widget)
    except Exception:
        logger.debug("plan widget hook failed for session %s", session_id, exc_info=True)
    return frame


_LIVE_ACTION_ID_RE = re.compile(r'"audit_id"\s*:\s*"(la_[0-9a-zA-Z]+)"')
_PAPER_ACTION_ID_RE = re.compile(r'"audit_id"\s*:\s*"(pa_[0-9a-f]+)"')


def _load_live_action_record(audit_id: str) -> Optional[Dict[str, Any]]:
    """Reload a redacted live-action record from the ledger by audit_id."""
    try:
        from src.live.paths import live_root

        ledger = live_root() / "audit.jsonl"
        if not ledger.exists():
            return None
        for line in reversed(ledger.read_text(encoding="utf-8").splitlines()):
            if audit_id not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and record.get("audit_id") == audit_id:
                return record
    except Exception:  # pragma: no cover - relay must never break the stream
        logger.debug("live.action reload failed for %s", audit_id, exc_info=True)
    return None


def _load_agent_audit_record(audit_id: str) -> Optional[Dict[str, Any]]:
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.autonomous_agents.audit import load_agent_audit

        return load_agent_audit(audit_id)
    except Exception:
        logger.debug("agent.action reload failed for %s", audit_id, exc_info=True)
    return None


def _agent_audit_frame_from_tool_result(event: Any) -> Optional[str]:
    """Build an agent.action SSE frame from autonomous-agent MCP tool_result."""
    data = getattr(event, "data", None)
    if getattr(event, "event_type", None) != "tool_result" or not isinstance(data, dict):
        return None
    preview = str(data.get("preview") or "")
    if (
        '"agent_audit"' not in preview
        and '"paper_action"' not in preview
        and '"audit_id"' not in preview
    ):
        return None
    match = _PAPER_ACTION_ID_RE.search(preview)
    if not match:
        return None
    record = _load_agent_audit_record(match.group(1))
    if record is None:
        return None

    from src.session.events import SSEEvent

    frame = SSEEvent(
        event_type="agent.action",
        data=record,
        session_id=getattr(event, "session_id", "") or "",
    )
    return frame.to_sse()


def _live_action_frame_from_tool_result(event: Any) -> Optional[str]:
    """Build a live.action SSE frame from an order-guard tool_result."""
    data = getattr(event, "data", None)
    if getattr(event, "event_type", None) != "tool_result" or not isinstance(data, dict):
        return None
    preview = str(data.get("preview") or "")
    if '"live_action"' not in preview:
        return None
    match = _LIVE_ACTION_ID_RE.search(preview)
    if not match:
        return None
    record = _load_live_action_record(match.group(1))
    if record is None:
        return None

    from src.session.events import SSEEvent

    frame = SSEEvent(
        event_type="live.action",
        data=record,
        session_id=getattr(event, "session_id", "") or "",
    )
    return frame.to_sse()
