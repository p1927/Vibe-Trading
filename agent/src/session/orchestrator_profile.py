"""Orchestrator session profile — tool allowlist and session-kind helpers."""

from __future__ import annotations

from typing import Any

from src.agent.tools import ToolRegistry

SESSION_KIND_ORCHESTRATOR = "autonomous_orchestrator"
SESSION_KIND_AGENT = "autonomous_agent"

_ORCHESTRATOR_LOCAL_TOOLS = frozenset(
    {
        "propose_autonomous_agent",
        "load_skill",
    }
)

_ORCHESTRATOR_MCP_TOOL_SUBSTRINGS = (
    "get_stock_browse",
    "get_options_browse",
    "get_autonomous_agent_status",
    "get_us_quote",
    "propose_autonomous_agent",
)


def session_kind(session_config: dict[str, Any] | None) -> str | None:
    if not session_config:
        return None
    kind = str(session_config.get("session_kind") or "").strip()
    return kind or None


def is_orchestrator_session(session_config: dict[str, Any] | None) -> bool:
    return session_kind(session_config) == SESSION_KIND_ORCHESTRATOR


def filter_registry_for_orchestrator(registry: ToolRegistry) -> ToolRegistry:
    """Keep only tools needed to propose autonomous agents (no execution/mandate paths)."""
    filtered = ToolRegistry()
    for name, tool in registry._tools.items():
        if name in _ORCHESTRATOR_LOCAL_TOOLS:
            filtered.register(tool)
            continue
        if name.startswith("mcp_") and any(sub in name for sub in _ORCHESTRATOR_MCP_TOOL_SUBSTRINGS):
            filtered.register(tool)
    return filtered
