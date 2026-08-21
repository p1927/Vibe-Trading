"""Read-only wrapper around the India options-research pipeline (ranked
multi-leg strategy suggestions), for Options Lab's India mode.

``trade_integrations.dataflows.options_research.aggregator.run_options_research``
already runs the full candidate-generation + ranking pipeline against real
broker-backed India options data (see its own module docs); this tool is the
first HTTP-reachable wrapper around it — previously it was only invoked from
scheduled background jobs (``agent/src/scheduled_research/options_jobs.py``).

This intentionally returns the ranked-strategy list, not the flattened
single-payoff-curve shape ``OptionsPayoffTool`` produces — a ranked strategy is
a genuinely different product (auto-selected, possibly multi-leg, scored) from
a user-built payoff, so no shape-forcing is attempted here. The frontend
renders it as its own panel.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from src.agent.tools import BaseTool


class IndiaOptionsResearchTool(BaseTool):
    """Fetch auto-ranked options strategy suggestions for an India underlying."""

    name = "get_india_options_research"
    description = (
        "Run the India options-research pipeline for one underlying (index or "
        "NSE stock) and return its auto-ranked multi-leg strategy suggestions "
        "— each with a score, probability of profit, max profit/loss, "
        "breakevens, and legs. Read-only. Example: "
        'get_india_options_research(ticker="NIFTY").'
    )
    parameters = {
        "type": "object",
        "properties": {
            "ticker": {
                "type": "string",
                "description": (
                    "India underlying symbol: an index name (NIFTY, "
                    "BANKNIFTY) or an NSE/BSE equity, e.g. 'RELIANCE.NS'. "
                    "Required."
                ),
            },
            "expiry_date": {
                "type": "string",
                "description": "Optional expiry as YYYY-MM-DD. Omit for the nearest expiry.",
            },
        },
        "required": ["ticker"],
    }

    def execute(self, **kwargs: Any) -> str:
        """Return a JSON-string envelope with ranked strategies.

        Returns:
            On success: ``{"ok": true, "data": {...}}`` — see ``_to_payload``
            for the trimmed field set. On failure: ``{"ok": false, "error":
            str}``.
        """
        ticker = str(kwargs.get("ticker") or "").strip()
        if not ticker:
            return _error("ticker is required")
        expiry_date = kwargs.get("expiry_date")
        expiry_date = str(expiry_date).strip() or None if expiry_date is not None else None

        try:
            from trade_integrations.dataflows.options_research.aggregator import (
                run_options_research,
            )

            doc = run_options_research(ticker, expiry_date=expiry_date)
        except Exception as exc:  # noqa: BLE001 — surface as error envelope
            return _error(f"options research pipeline failed: {exc}")

        return json.dumps({"ok": True, "data": _to_payload(doc)}, ensure_ascii=False, default=str)


def _to_payload(doc: Any) -> Dict[str, Any]:
    """Project ``OptionsResearchDoc`` onto the fields Options Lab's ranked-
    strategies panel needs — skips ``stages``/``events``/``prediction``
    (pipeline-diagnostic fields with no UI consumer yet)."""
    chain = doc.chain_snapshot or {}
    return {
        "underlying": doc.underlying,
        "as_of": doc.as_of,
        "instrument_type": doc.instrument_type,
        "expiry": doc.expiry,
        "spot": doc.spot,
        "atm_strike": chain.get("atm_strike"),
        "pcr": chain.get("pcr"),
        "ranked_strategies": doc.ranked_strategies,
        "recommended": doc.recommended,
        "charges": doc.charges,
    }


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
