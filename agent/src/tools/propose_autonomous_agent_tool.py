"""``propose_autonomous_agent`` — read-only PROPOSE half for autonomous agent creation."""

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


class ProposeAutonomousAgentTool(BaseTool):
    name = "propose_autonomous_agent"
    description = (
        "Propose an autonomous trading agent for the user to confirm. "
        "READ-ONLY: returns a proposal card; user must confirm in the UI. "
        "Call when the user wants a persistent agent that watches symbols on a schedule."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Symbols to watch/trade, e.g. ['NIFTY','BANKNIFTY'].",
            },
            "name": {"type": "string", "description": "Display name for the agent."},
            "mandate": {"type": "string", "description": "Trading mandate / goal."},
            "budget_inr": {"type": "number"},
            "max_daily_loss_inr": {"type": "number"},
            "confidence_threshold": {"type": "integer", "description": "0-100, default 75"},
            "watch_interval_min": {"type": "integer", "description": "Lightweight watch cadence, default 7"},
            "research_interval_min": {"type": "integer", "description": "Full reasoning cadence, default 90"},
            "mode": {"type": "string", "description": "paper (v1 only)"},
            "session_id": {"type": "string", "description": "Orchestrator vibe session id."},
        },
        "required": ["symbols"],
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent

        symbols = kwargs.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            return json.dumps({"status": "error", "error": "symbols (array) is required"})

        result = propose_autonomous_agent(
            symbols=symbols,
            name=kwargs.get("name"),
            mandate=kwargs.get("mandate"),
            budget_inr=kwargs.get("budget_inr"),
            max_daily_loss_inr=kwargs.get("max_daily_loss_inr"),
            confidence_threshold=kwargs.get("confidence_threshold"),
            watch_interval_min=kwargs.get("watch_interval_min"),
            research_interval_min=kwargs.get("research_interval_min"),
            mode=kwargs.get("mode"),
            orchestrator_session_id=kwargs.get("session_id"),
        )
        if result.get("status") == "ready" and isinstance(result.get("proposal"), dict):
            proposal = result["proposal"]
            proposal["session_id"] = kwargs.get("session_id")
            result["proposal"] = proposal
        return json.dumps(result, ensure_ascii=False, default=str)
