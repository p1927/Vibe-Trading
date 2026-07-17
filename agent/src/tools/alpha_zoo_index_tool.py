"""Index Alpha Zoo bridge status — composite factors for NIFTY Ridge."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)


def run_alpha_zoo_index(**kwargs: Any) -> dict[str, Any]:
    """Return bridge config, promotion gate, and latest composite values."""
    action = str(kwargs.get("action") or "status").strip().lower()
    try:
        from trade_integrations.dataflows.index_research.alpha_bridge.config import (
            is_bridge_enabled,
            load_alpha_zoo_config,
        )
        from trade_integrations.dataflows.index_research.alpha_bridge.promotion import (
            load_alpha_promotion_decision,
            promoted_alpha_zoo_factor_keys,
        )
        from trade_integrations.dataflows.index_research.alpha_bridge.snapshot import (
            compute_alpha_zoo_snapshot,
        )
    except ImportError as exc:
        return {"status": "error", "error": f"alpha_bridge unavailable: {exc}"}

    if action == "snapshot":
        rows = compute_alpha_zoo_snapshot(as_of_day=kwargs.get("as_of_day"))
        return {"status": "ok", "action": action, "rows": rows}

    config = load_alpha_zoo_config()
    promotion = load_alpha_promotion_decision()
    return {
        "status": "ok",
        "action": "status",
        "bridge_enabled": is_bridge_enabled(),
        "config": config,
        "promotion": promotion,
        "promoted_keys": list(promoted_alpha_zoo_factor_keys()),
    }


class AlphaZooIndexTool(BaseTool):
    """Query NIFTY index Alpha Zoo bridge (config, promotion, snapshot)."""

    name = "alpha_zoo_index"
    description = (
        "NIFTY index Alpha Zoo bridge: action=status returns config + promotion gate; "
        "action=snapshot returns latest alpha_zoo_* composite factor rows."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "snapshot"],
                "description": "status (default) or snapshot for latest composites.",
            },
            "as_of_day": {
                "type": "string",
                "description": "Optional YYYY-MM-DD for snapshot action.",
            },
        },
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        envelope = run_alpha_zoo_index(**kwargs)
        return json.dumps(envelope, ensure_ascii=False)
