"""Tests for news scenario session profile."""

from __future__ import annotations

from src.agent.tools import BaseTool, ToolRegistry
from src.session.news_scenario_profile import (
    filter_registry_for_news_scenario,
    is_news_scenario_session,
)


class _LoadSkillTool(BaseTool):
    name = "load_skill"
    description = "load"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "ok"


class _MandateTool(BaseTool):
    name = "propose_mandate_profiles"
    description = "mandate"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "{}"


class _McpPipelineTool(BaseTool):
    name = "mcp_openalgo_get_pipeline_snapshot"
    description = "snapshot"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "{}"


class _McpRunTool(BaseTool):
    name = "mcp_openalgo_run_news_event_scenario"
    description = "run"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "{}"


class _DummyTool(BaseTool):
    name = "dummy"
    description = "dummy"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs):
        return "ok"


def test_is_news_scenario_session() -> None:
    assert is_news_scenario_session({"session_kind": "news_scenario_advisor"})
    assert not is_news_scenario_session({"session_kind": "autonomous_orchestrator"})
    assert not is_news_scenario_session(None)


def test_filter_registry_for_news_scenario() -> None:
    reg = ToolRegistry()
    reg.register(_LoadSkillTool())
    reg.register(_MandateTool())
    reg.register(_McpPipelineTool())
    reg.register(_McpRunTool())
    reg.register(_DummyTool())

    filtered = filter_registry_for_news_scenario(reg)
    names = set(filtered._tools.keys())
    assert "load_skill" in names
    assert "mcp_openalgo_get_pipeline_snapshot" in names
    assert "mcp_openalgo_run_news_event_scenario" in names
    assert "propose_mandate_profiles" not in names
    assert "dummy" not in names
