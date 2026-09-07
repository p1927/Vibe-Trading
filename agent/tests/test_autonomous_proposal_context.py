"""Orchestrator session binding for MCP autonomous-agent proposal calls.

Regression cover for
.claude/backlog/items/2026-09-07-mcp-propose-tool-has-no-session-injection.md:
`mcp_openalgo_propose_autonomous_agent` takes `vibe_session_id` as an ordinary
model-supplied parameter, and the model cannot know the vibe session id, so
every MCP-created proposal persisted `orchestrator_session_id: null` and the
UI's only hydrate path could never find it.
"""
from __future__ import annotations

from src.session.autonomous_proposal_context import (
    inject_autonomous_proposal_session_context as inject,
)

MCP_PROPOSE = "mcp_openalgo_propose_autonomous_agent"


def test_fills_the_session_id_when_the_model_omits_it():
    out = inject({"symbols": ["NIFTY"]}, session_id="a89ca2c6592a", tool_name=MCP_PROPOSE)
    assert out["vibe_session_id"] == "a89ca2c6592a"
    assert out["symbols"] == ["NIFTY"], "existing args must be preserved"


def test_does_not_overwrite_a_value_the_model_supplied():
    """Only fills a gap — the same contract _normalize_tool_run_dir uses."""
    out = inject(
        {"symbols": ["NIFTY"], "vibe_session_id": "explicit"},
        session_id="injected",
        tool_name=MCP_PROPOSE,
    )
    assert out["vibe_session_id"] == "explicit"


def test_treats_a_blank_supplied_value_as_absent():
    out = inject(
        {"vibe_session_id": "   "}, session_id="a89ca2c6592a", tool_name=MCP_PROPOSE
    )
    assert out["vibe_session_id"] == "a89ca2c6592a"


def test_does_not_mutate_the_caller_dict():
    args = {"symbols": ["NIFTY"]}
    inject(args, session_id="a89ca2c6592a", tool_name=MCP_PROPOSE)
    assert "vibe_session_id" not in args


def test_ignores_unrelated_mcp_tools():
    args = {"ticker": "NIFTY"}
    assert inject(args, session_id="s", tool_name="mcp_openalgo_get_market_data") == args


def test_ignores_local_non_mcp_tools():
    """The native ProposeAutonomousAgentTool has its own injection path."""
    args = {"symbols": ["NIFTY"]}
    assert inject(args, session_id="s", tool_name="propose_autonomous_agent") == args


def test_no_session_id_available_is_a_no_op():
    args = {"symbols": ["NIFTY"]}
    assert inject(args, session_id="", tool_name=MCP_PROPOSE) == args
