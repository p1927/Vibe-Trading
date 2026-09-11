"""The ``autonomous_agent.proposal`` card relay must fire for MCP-wrapped propose calls.

Regression for .claude/backlog/items/2026-09-07-orchestrator-turn-never-surfaces-an-answer.md
(Trade repo). Verified live on release 2026-09-11: ``mcp_openalgo_propose_autonomous_agent``
returned ``status=ok`` and saved a proposal, yet no card frame reached the SSE stream. The
relay only looked for the proposal id inside the ``tool_result`` event's ``preview``, which
is the first 200 characters of the result. An MCP-wrapped result nests the proposal under a
security envelope inside double-encoded JSON, so the id is either escaped (``\\"proposal_id\\"``,
the 2026-09-07 shape) or past the 200-character cut entirely (the 2026-09-11 shape). The
previews below reproduce both real shapes.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.sse_frames import _autonomous_agent_proposal_frame_from_tool_result
from trade_integrations.autonomous_agents.store import save_proposal

_SESSION = "sess_relay1"
_PID = "aap_" + "a" * 32


@pytest.fixture(autouse=True)
def _isolated_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    # Env var, not attribute-patching get_hub_dir: store.py binds the name at import.
    tmp = Path(tempfile.mkdtemp(prefix="proposal_relay_test_"))
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp))


def _seed(status: str = "incomplete", session_id: str = _SESSION) -> None:
    save_proposal(
        {
            "type": "autonomous_agent.proposal",
            "proposal_id": _PID,
            "status": status,
            "orchestrator_session_id": session_id,
            "session_id": session_id,
            "symbols": ["RELIANCE"],
            "expires_at_ms": int(time.time() * 1000) + 30 * 60 * 1000,
        }
    )


def _mcp_preview(inner: dict) -> str:
    """The loop's preview of an MCP-wrapped result: outer envelope, inner JSON text, 200 chars."""
    return json.dumps({"status": "ok", "data": {"result": json.dumps(inner, indent=2)}})[:200]


def _event(tool: str, preview: str, status: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        event_type="tool_result",
        session_id=_SESSION,
        data={"tool": tool, "status": status, "preview": preview, "call_id": "c1"},
    )


def _card(frame: str | None) -> dict:
    assert frame is not None, "no autonomous_agent.proposal frame was relayed"
    assert "event: autonomous_agent.proposal" in frame
    payload = next(line[5:].strip() for line in frame.splitlines() if line.startswith("data:"))
    return json.loads(payload)


def test_mcp_result_with_security_envelope_relays_card() -> None:
    """2026-09-11 shape: the id is past the 200-character preview cut."""
    _seed()
    preview = _mcp_preview(
        {
            "_openalgo_mcp_security": {
                "trust": "untrusted_tool_output",
                "tool": "propose_autonomous_agent",
                "risk": "broker_structured",
            },
            "data": {"status": "incomplete", "proposal_id": _PID},
        }
    )
    assert _PID not in preview  # the shape under test: no id anywhere in the preview

    card = _card(
        _autonomous_agent_proposal_frame_from_tool_result(
            _event("mcp_openalgo_propose_autonomous_agent", preview)
        )
    )
    assert card["proposal_id"] == _PID


def test_mcp_result_with_escaped_id_relays_card() -> None:
    """2026-09-07 shape: the id is inside the preview but JSON-escaped."""
    _seed(status="ready")
    preview = _mcp_preview({"status": "ready", "proposal_id": _PID})
    assert '\\"proposal_id\\"' in preview

    card = _card(
        _autonomous_agent_proposal_frame_from_tool_result(
            _event("mcp_openalgo_propose_autonomous_agent", preview)
        )
    )
    assert card["proposal_id"] == _PID


def test_local_tool_plain_preview_still_relays_card() -> None:
    _seed(status="ready")
    preview = json.dumps({"status": "ready", "proposal_id": _PID})[:200]

    card = _card(
        _autonomous_agent_proposal_frame_from_tool_result(
            _event("propose_autonomous_agent", preview)
        )
    )
    assert card["proposal_id"] == _PID


def test_failed_propose_call_relays_nothing() -> None:
    _seed()
    preview = json.dumps({"status": "error", "error": "validation error"})[:200]
    frame = _autonomous_agent_proposal_frame_from_tool_result(
        _event("mcp_openalgo_propose_autonomous_agent", preview, status="error")
    )
    assert frame is None


def test_mcp_result_without_a_proposal_for_this_session_relays_nothing() -> None:
    """A proposal saved under a different session must never surface here."""
    _seed(session_id="some_other_session")
    preview = _mcp_preview({"_openalgo_mcp_security": {"trust": "untrusted_tool_output"}})
    frame = _autonomous_agent_proposal_frame_from_tool_result(
        _event("mcp_openalgo_propose_autonomous_agent", preview)
    )
    assert frame is None
