"""Regression for the missing session-injection wiring described in
`.claude/backlog/items/2026-09-07-propose-tool-null-session-id.md`:
`ProposeAutonomousAgentTool` was absent from `session_injected_classes`, so every
proposal it created was persisted with `orchestrator_session_id: null` and could never
be found again by `GET /autonomous-agents/proposals/latest` (the reload/hydrate path).

Nothing previously asserted which classes belong in `session_injected_classes` — this
is that missing wiring test, plus an end-to-end check that a proposal created through
the tool actually lands on disk with the session id and is findable by it.
"""

from __future__ import annotations

import pytest

from src.tools import build_registry, session_injected_classes
from src.tools.propose_autonomous_agent_tool import ProposeAutonomousAgentTool


@pytest.mark.unit
def test_propose_autonomous_agent_tool_is_session_injected():
    assert ProposeAutonomousAgentTool in session_injected_classes()


@pytest.mark.unit
def test_registry_wires_session_id_into_propose_tool():
    registry = build_registry(session_id="sess_wiring_check")
    tool = registry.get("propose_autonomous_agent")
    assert tool is not None
    assert tool._default_session_id == "sess_wiring_check"


@pytest.mark.unit
def test_every_default_session_id_tool_is_session_injected():
    """Guard against the next tool silently missing this wiring: any local tool class
    whose __init__ accepts default_session_id must be in session_injected_classes, or
    build_registry's else-branch (`cls()`) leaves it permanently None."""
    import inspect

    from src.tools import _discover_subclasses

    injected = session_injected_classes()
    for cls in _discover_subclasses():
        try:
            params = inspect.signature(cls.__init__).parameters
        except (TypeError, ValueError):
            continue
        if "default_session_id" in params and cls not in injected:
            pytest.fail(
                f"{cls.__name__} accepts default_session_id but is missing from "
                "session_injected_classes in src/tools/__init__.py — it will always "
                "receive None."
            )


@pytest.mark.unit
def test_propose_creates_proposal_findable_by_session_id(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: hub)

    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import load_latest_proposal_for_orchestrator

    monkeypatch.setattr(proposals, "build_stack_health", lambda: {"vibe_scheduler": "ok"})

    registry = build_registry(session_id="sess_e2e_check")
    tool = registry.get("propose_autonomous_agent")
    assert tool is not None

    tool.execute(symbols=["NIFTY"], name="wiring check", mandate="paper only")

    latest = load_latest_proposal_for_orchestrator("sess_e2e_check")
    assert latest is not None
    assert latest.get("orchestrator_session_id") == "sess_e2e_check"
