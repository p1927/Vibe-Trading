"""Autonomous agent session profile — capability-driven tool filtering."""

from __future__ import annotations

from typing import Any

from src.agent.tools import ToolRegistry
from src.session.orchestrator_profile import SESSION_KIND_AGENT


def is_autonomous_agent_session(session_config: dict[str, Any] | None) -> bool:
    return str((session_config or {}).get("session_kind") or "") == SESSION_KIND_AGENT


def filter_registry_for_autonomous_agent(
    registry: ToolRegistry,
    session_config: dict[str, Any] | None,
) -> ToolRegistry:
    """Strip widget/execute tools when intent.capabilities disallow them."""
    if not is_autonomous_agent_session(session_config):
        return registry
    try:
        import sys
        from pathlib import Path

        trade_root = Path(__file__).resolve().parents[4]
        integrations = trade_root / "integrations"
        if integrations.is_dir() and str(integrations) not in sys.path:
            sys.path.insert(0, str(integrations))
        from trade_integrations.autonomous_agents.intent_capabilities import (
            is_tool_allowed_for_capabilities,
            resolve_capabilities,
        )
    except Exception:
        return registry

    caps = resolve_capabilities(session_config=session_config)
    filtered = ToolRegistry()
    for name, tool in registry._tools.items():
        if is_tool_allowed_for_capabilities(name, caps):
            filtered.register(tool)
    return filtered
