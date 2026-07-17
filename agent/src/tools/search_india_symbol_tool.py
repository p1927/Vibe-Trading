"""``search_india_symbol`` — resolve company names / fragments to NSE/BSE tickers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from src.agent.tools import BaseTool

_TRADE_ROOT = Path(__file__).resolve().parents[4]
_INTEGRATIONS = _TRADE_ROOT / "integrations"
if _INTEGRATIONS.is_dir() and str(_INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(_INTEGRATIONS))


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
        return json.dumps(
            {
                "ok": True,
                "query": query,
                "count": len(matches),
                "matches": matches,
            },
            ensure_ascii=False,
        )
