"""``search_india_symbol`` — resolve company names / fragments to NSE/BSE tickers."""

from __future__ import annotations

import json
from typing import Any

from src.agent.tools import BaseTool
from src.trade.hub_bridge import ensure_trade_stack_path

try:
    ensure_trade_stack_path()
except Exception:
    pass


class SearchIndiaSymbolTool(BaseTool):
    name = "search_india_symbol"
    description = (
        "Search India (NSE/BSE) symbols by company name or ticker fragment. "
        "Use when the user says a company name (e.g. Reliance, TCS) instead of a ticker. "
        "Returns up to 5 matches with symbol, name, and exchange."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Company name or ticker fragment, e.g. 'reliance', 'TATA', 'SBIN'.",
            },
            "limit": {
                "type": "integer",
                "description": "Max matches to return (1-10, default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        from trade_integrations.dataflows.symbol_registry.openalgo_registry import (
            search_india_symbols,
        )

        query = str(kwargs.get("query") or "").strip()
        if not query:
            return json.dumps({"ok": False, "error": "query is required"})

        try:
            limit = int(kwargs.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 10))

        matches = search_india_symbols(query, limit=limit)
        # Same envelope shape as search_symbol's resolver output (candidates
        # under data.candidates, per-provider status under data.sources) so
        # the grounding ledger's identity resolver can consume either tool's
        # result identically — see grounding.py's _RESOLVER_TOOLS.
        return json.dumps(
            {
                "ok": True,
                "market": "IN",
                "source": "search_india_symbol",
                "data": {
                    "query": query,
                    "count": len(matches),
                    "candidates": matches,
                    "sources": {"openalgo": "ok"},
                },
            },
            ensure_ascii=False,
        )
