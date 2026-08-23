"""Knowledge-engine browse tool: query_strategies / query_factor / query_event_factors /
query_track_record.

A single agent-facing tool with an ``action`` discriminator, mirroring
alpha_zoo_tool.py's shape. Queries the module 8 knowledge engine
(trade_integrations.knowledge_engine) — strategy/factor theory, module 1's event-factor
taxonomy (plus an interim calibration signal until module 1's learned impact weights
ship), and module 6's prediction-ledger track record.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)


def _err(msg: str) -> str:
    return json.dumps({"status": "error", "error": msg}, ensure_ascii=False)


def _ok(result: Any) -> str:
    return json.dumps({"status": "ok", "result": result}, ensure_ascii=False)


def run_knowledge_engine(**kwargs: Any) -> dict[str, Any]:
    """Module-level entry returning a parsed envelope (dict, not JSON string)."""
    action = kwargs.get("action")
    valid_actions = {"query_strategies", "query_factor", "query_event_factors", "query_track_record"}
    if action not in valid_actions:
        return {
            "status": "error",
            "error": f"action must be one of {sorted(valid_actions)}, got {action!r}",
        }

    try:
        from trade_integrations.knowledge_engine import query as knowledge_query
    except Exception as exc:
        logger.exception("knowledge_engine import failed")
        return {"status": "error", "error": f"knowledge_engine import failed: {exc}"}

    try:
        if action == "query_strategies":
            result = knowledge_query.query_strategies(
                market_view=kwargs.get("market_view"),
                risk_profile=kwargs.get("risk_profile"),
                horizon=kwargs.get("horizon"),
                tags=kwargs.get("tags"),
                limit=int(kwargs.get("limit", 5)),
            )
        elif action == "query_factor":
            factor_key = kwargs.get("factor_key")
            if not factor_key or not isinstance(factor_key, str):
                return {"status": "error", "error": "query_factor requires factor_key (string)"}
            result = knowledge_query.query_factor(
                factor_key, market=kwargs.get("market", "IN")
            )
        elif action == "query_event_factors":
            result = knowledge_query.query_event_factors(category=kwargs.get("category"))
        else:  # query_track_record
            result = knowledge_query.query_track_record(
                window=int(kwargs.get("window", 14)),
                ticker=kwargs.get("ticker", "NIFTY"),
            )
    except Exception as exc:
        logger.exception("knowledge_engine action %s failed", action)
        return {"status": "error", "error": f"{action} failed: {exc}"}

    return {"status": "ok", "result": result}


class KnowledgeEngineTool(BaseTool):
    """Query the financial knowledge engine: strategies, factor theory, track record."""

    name = "knowledge_engine"
    description = (
        "Query the platform's knowledge engine. action=query_strategies ranks strategy/"
        "options-structure catalog entries by market_view/risk_profile/horizon/tags match; "
        "action=query_factor returns pedagogy + module 1's event-factor taxonomy entry + "
        "interim calibration for one factor key; action=query_event_factors lists/browses "
        "module 1's event-factor taxonomy, optionally filtered by category; "
        "action=query_track_record returns the prediction ledger's system-wide calibration/"
        "accuracy metrics (not yet filterable per-strategy)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["query_strategies", "query_factor", "query_event_factors", "query_track_record"],
                "description": "Which knowledge-engine query to run.",
            },
            "market_view": {
                "type": "string",
                "description": "Optional filter for query_strategies (bullish/bearish/neutral/volatile/range_bound/any).",
            },
            "risk_profile": {
                "type": "string",
                "description": "Optional filter for query_strategies (defined_risk/undefined_risk/hedge/capital_preservation).",
            },
            "horizon": {
                "type": "string",
                "description": "Optional horizon_fit filter for query_strategies (e.g. A, B, C).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional keyword/indicator tags for query_strategies.",
            },
            "limit": {
                "type": "integer",
                "default": 5,
                "description": "Cap on returned strategies for query_strategies (default 5).",
            },
            "factor_key": {
                "type": "string",
                "description": "Required when action=query_factor (e.g. india_vix, fii_net_5d).",
            },
            "market": {
                "type": "string",
                "default": "IN",
                "description": "Market scope for query_factor (passed to news_hub_bridge.get_market_analysis_state).",
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional taxonomy category filter for query_event_factors (e.g. "
                    "monetary_policy, fiscal, geopolitical, global_spillover_us, "
                    "global_spillover_other_cb, ma_deal, sector_regulatory, political_stability)."
                ),
            },
            "ticker": {
                "type": "string",
                "default": "NIFTY",
                "description": "Ticker scope for query_track_record.",
            },
            "window": {
                "type": "integer",
                "default": 14,
                "description": "Rolling window (days) for query_track_record.",
            },
        },
        "required": ["action"],
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        envelope = run_knowledge_engine(**kwargs)
        return json.dumps(envelope, ensure_ascii=False)
