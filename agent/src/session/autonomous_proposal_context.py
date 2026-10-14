"""Bind the orchestrator chat session onto autonomous-agent proposal tool calls.

Fork-owned sidecar (see ``docs/FORK_CONVENTIONS.md``): the logic lives here and
``src/agent/loop.py`` only calls it, mirroring
``news_scenario_profile.inject_news_scenario_session_context``.

Why this exists: ``mcp_openalgo_propose_autonomous_agent``
(``openalgo/mcp/custom_tools.py``) declares ``vibe_session_id`` as an ordinary
model-supplied parameter, and **the model has no way to know the vibe session
id**, so it is never passed. Every proposal created through the MCP tool is then
persisted with ``orchestrator_session_id: null``, and
``GET /autonomous-agents/proposals/latest?orchestrator_session_id=...`` — the
only path the UI has for hydrating its confirmation card — can never find it.

The native ``ProposeAutonomousAgentTool`` had the identical defect and was fixed
by adding it to ``tools/__init__.py``'s ``session_injected_classes``
(.claude/backlog/items/2026-09-07-propose-tool-null-session-id.md, closed). That
fix cannot reach the MCP tool, which is a different code path in an
upstream-owned submodule. Injecting caller-side fixes it without editing
upstream, and covers any future MCP tool that needs the same binding.

Confirmed live 2026-09-07 on release `b7f3639e6`: proposal
`aap_87a23e94951e40bb8b56c2b56f5915ac` was created successfully and stored a
null orchestrator session id.
"""

from __future__ import annotations

from typing import Any

#: MCP tools that take an orchestrator session id under this argument name.
#: Substring-matched because the MCP client prefixes tool names with the server
#: (``mcp_openalgo_propose_autonomous_agent``).
_SESSION_BOUND_MCP_TOOLS: dict[str, str] = {
    "propose_autonomous_agent": "vibe_session_id",
}


def inject_autonomous_proposal_session_context(
    args: dict[str, Any],
    *,
    session_id: str,
    tool_name: str,
) -> dict[str, Any]:
    """Bind the orchestrator session id into a proposal tool call.

    The loop's own session id always wins over a model-supplied value, the same precedence the
    native ``ProposeAutonomousAgentTool`` applies (``default_session_id`` before ``session_id``).
    The model cannot know the id, so whatever it wrote there is a guess; honouring it persisted
    the proposal under an id no UI session owns (or under another session's), and the
    confirmation card was never found.
    """
    name = str(tool_name or "")
    if not name.startswith("mcp_") or not session_id:
        return args
    for needle, arg_name in _SESSION_BOUND_MCP_TOOLS.items():
        if needle not in name:
            continue
        normalized = dict(args)
        normalized[arg_name] = session_id
        return normalized
    return args
