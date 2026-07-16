"""Tests for orchestrator session profile."""

from __future__ import annotations

from src.agent.tools import BaseTool, ToolRegistry
from src.session.orchestrator_profile import (
    filter_registry_for_orchestrator,
    is_orchestrator_session,
)


class _DummyTool(BaseTool):
    name = "dummy"
    description = "dummy"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "ok"


class _ProposeTool(BaseTool):
    name = "propose_autonomous_agent"
    description = "propose"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "{}"


class _MandateTool(BaseTool):
    name = "propose_mandate_profiles"
    description = "mandate"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "{}"


class _McpBrowseTool(BaseTool):
    name = "mcp_openalgo_get_stock_browse"
    description = "browse"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "{}"


def test_is_orchestrator_session() -> None:
    assert is_orchestrator_session({"session_kind": "autonomous_orchestrator"})
    assert not is_orchestrator_session({"session_kind": "autonomous_agent"})
    assert not is_orchestrator_session(None)


def test_filter_registry_for_orchestrator() -> None:
    reg = ToolRegistry()
    reg.register(_ProposeTool())
    reg.register(_MandateTool())
    reg.register(_McpBrowseTool())
    reg.register(_DummyTool())

    filtered = filter_registry_for_orchestrator(reg)
    names = set(filtered._tools.keys())
    assert "propose_autonomous_agent" in names
    assert "mcp_openalgo_get_stock_browse" in names
    assert "propose_mandate_profiles" not in names
    assert "dummy" not in names
