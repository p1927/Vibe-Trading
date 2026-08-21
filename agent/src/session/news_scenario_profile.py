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


def is_news_scenario_pipeline_tool(tool_name: str) -> bool:
    """True when tool_name is an allowed news-scenario MCP pipeline tool."""
    name = str(tool_name or "")
    if not name.startswith("mcp_"):
        return False
    return any(sub in name for sub in _NEWS_SCENARIO_MCP_TOOL_SUBSTRINGS)


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


def inject_news_scenario_session_context(
    args: dict[str, Any],
    *,
    session_id: str,
    tool_name: str,
    session_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Inject bound pipeline_as_of / session_id into news-scenario MCP tools."""
    if not session_config or session_config.get("session_kind") != SESSION_KIND_NEWS_SCENARIO:
        return args
    if not is_news_scenario_pipeline_tool(tool_name):
        return args
    normalized = dict(args)
    if not normalized.get("pipeline_as_of"):
        bound = session_config.get("pipeline_as_of")
        if bound:
            normalized["pipeline_as_of"] = bound
    if not normalized.get("ticker"):
        normalized["ticker"] = session_config.get("pipeline_ticker") or "NIFTY"
    if "run_news_event_scenario" in tool_name and session_id and not normalized.get("session_id"):
        normalized["session_id"] = session_id
    return normalized
