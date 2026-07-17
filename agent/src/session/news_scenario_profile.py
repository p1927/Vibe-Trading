"""News scenario advisor session profile — tool allowlist and session-kind helpers."""

from __future__ import annotations

from typing import Any

from src.agent.tools import ToolRegistry

SESSION_KIND_NEWS_SCENARIO = "news_scenario_advisor"

_NEWS_SCENARIO_LOCAL_TOOLS = frozenset(
    {
        "load_skill",
        "search_india_symbol",
    }
)

_NEWS_SCENARIO_MCP_TOOL_SUBSTRINGS = (
    "get_pipeline_snapshot",
    "query_factor_explanation",
    "query_factor_sensitivity",
    "query_equation_coefficients",
    "query_constituent_drivers",
    "get_pipeline_news_items",
    "get_playground_context",
    "get_index_trade_plan",
    "simulate_pipeline_scenario",
    "save_news_scenario_draft",
    "run_news_event_scenario",
    "get_news_scenario_widget",
)


def is_news_scenario_session(session_config: dict[str, Any] | None) -> bool:
    if not session_config:
        return False
    kind = str(session_config.get("session_kind") or "").strip()
    return kind == SESSION_KIND_NEWS_SCENARIO


def filter_registry_for_news_scenario(registry: ToolRegistry) -> ToolRegistry:
    """Keep pipeline read/simulate tools; block execution and refresh paths."""
    filtered = ToolRegistry()
    for name, tool in registry._tools.items():
        if name in _NEWS_SCENARIO_LOCAL_TOOLS:
            filtered.register(tool)
            continue
        if not name.startswith("mcp_"):
            continue
        if any(sub in name for sub in _NEWS_SCENARIO_MCP_TOOL_SUBSTRINGS):
            filtered.register(tool)
    return filtered
