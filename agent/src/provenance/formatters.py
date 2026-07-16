"""Turn tool results and artifacts into user-readable provenance summaries."""

from __future__ import annotations

import json
import re
from typing import Any

_TOOL_LABELS: dict[str, str] = {
    "load_skill": "Strategy knowledge",
    "write_file": "Generated code",
    "edit_file": "Code edit",
    "read_file": "File read",
    "run_backtest": "Backtest run",
    "bash": "Shell command",
    "read_url": "Web page",
    "read_document": "Document",
    "web_search": "Web search",
    "trading_connections": "Trading connectors",
    "trading_select_connection": "Connector selection",
    "trading_check": "Connector check",
    "trading_account": "Account snapshot",
    "trading_positions": "Positions",
    "trading_orders": "Orders",
    "trading_quote": "Live quote",
    "trading_history": "Trade history",
    "get_market_data": "Market data",
    "get_options_chain": "Options chain",
    "get_fundamentals": "Fundamentals",
    "stock_news": "Stock news",
    "research_reports": "Research report",
    "add_goal_evidence": "Research evidence",
    "compact": "Conversation summary",
}


def _friendly_tool_name(tool: str) -> str:
    if tool in _TOOL_LABELS:
        return _TOOL_LABELS[tool]
    if tool.startswith("mcp_openalgo_"):
        slug = tool.removeprefix("mcp_openalgo_")
        return slug.replace("_", " ").strip().title()
    if tool.startswith("mcp_"):
        slug = tool.removeprefix("mcp_")
        return slug.replace("_", " ").strip().title()
    return tool.replace("_", " ").strip().title()


def _category_for_tool(tool: str) -> str:
    lower = tool.lower()
    if any(k in lower for k in ("option", "quote", "chain", "greek", "trading_", "market_data", "openalgo")):
        return "market_data"
    if any(k in lower for k in ("backtest", "run_backtest", "shadow")):
        return "backtest"
    if any(k in lower for k in ("read_url", "web_search", "read_document", "news")):
        return "web"
    if any(k in lower for k in ("research", "fundamental", "filings", "skill")):
        return "research"
    if "goal" in lower or "evidence" in lower:
        return "evidence"
    return "tool"


def _try_parse_json(raw: str) -> Any | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _first_str(data: Any, *keys: str) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in keys:
        val = data.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def _summarize_parsed(tool: str, data: Any, raw: str) -> str:
    if isinstance(data, dict):
        if data.get("error"):
            return f"Error: {str(data['error'])[:120]}"
        if data.get("message") and len(data) <= 3:
            return str(data["message"])[:160]

        symbol = _first_str(data, "symbol", "underlying", "ticker", "instrument")
        spot = _first_str(data, "spot", "ltp", "last_price", "close")
        expiry = _first_str(data, "expiry", "expiry_date", "expiration")
        strikes = data.get("strikes") or data.get("chain")
        if symbol and spot:
            parts = [f"{symbol} @ {spot}"]
            if expiry:
                parts.append(f"exp {expiry}")
            if isinstance(strikes, list):
                parts.append(f"{len(strikes)} strikes")
            return " · ".join(parts)[:180]

        strategies = data.get("ranked_strategies") or data.get("strategies")
        if isinstance(strategies, list) and strategies:
            top = strategies[0] if isinstance(strategies[0], dict) else {}
            name = _first_str(top, "name", "strategy") or "strategy"
            score = top.get("score")
            if score is not None:
                return f"Top: {name} (score {score})"[:180]
            return f"Top: {name}"[:180]

        prediction = data.get("prediction")
        if isinstance(prediction, dict):
            view = _first_str(prediction, "view", "direction")
            conf = prediction.get("confidence")
            if view and conf is not None:
                return f"View: {view}, confidence {conf}"[:180]
            if view:
                return f"View: {view}"[:180]

        metrics = data.get("metrics")
        if isinstance(metrics, dict) and metrics:
            sharpe = metrics.get("sharpe") or metrics.get("sharpe_ratio")
            ret = metrics.get("total_return") or metrics.get("return")
            bits = []
            if ret is not None:
                bits.append(f"return {ret}")
            if sharpe is not None:
                bits.append(f"Sharpe {sharpe}")
            if bits:
                return ", ".join(bits)[:180]

        results = data.get("results")
        if isinstance(results, list) and results:
            return f"{len(results)} results"[:180]

        status = _first_str(data, "status")
        if status:
            return f"Status: {status}"[:180]

    if isinstance(data, list):
        return f"{len(data)} items"[:180]

    cleaned = re.sub(r"\s+", " ", raw).strip()
    return cleaned[:160] if cleaned else "Data retrieved"


def summarize_tool_result(tool: str, raw: str, *, status: str = "ok") -> tuple[str, str, str]:
    """Return display_name, summary, category for a tool result."""
    display = _friendly_tool_name(tool)
    category = _category_for_tool(tool)
    if status != "ok":
        return display, f"Failed — {raw[:120] if raw else 'no details'}", category

    parsed = _try_parse_json(raw)
    summary = _summarize_parsed(tool, parsed, raw)
    return display, summary, category


def summarize_hub_artifact(ticker: str, artifact: dict[str, Any]) -> tuple[str, str]:
    """Return display_name and summary for a hub research artifact."""
    asset = str(artifact.get("asset_type") or "options")
    label = "Options research" if asset == "options" else "Stock research"
    display = f"{label} · {ticker.upper()}"

    pred = artifact.get("prediction") if isinstance(artifact.get("prediction"), dict) else {}
    view = _first_str(pred, "view", "direction") if pred else None
    conf = pred.get("confidence") if pred else None
    rec = _first_str(artifact, "recommended_name")
    parts: list[str] = []
    if view:
        parts.append(f"View: {view}")
    if conf is not None:
        parts.append(f"confidence {conf}")
    if rec:
        parts.append(f"pick: {rec}")
    if not parts:
        parts.append(str(artifact.get("plan_status") or "Hub plan loaded"))
    return display, " · ".join(parts)[:180]


def summarize_goal_evidence(evidence: dict[str, Any]) -> tuple[str, str]:
    """Return display_name and summary for goal evidence."""
    provider = str(evidence.get("source_provider") or "research").strip()
    text = str(evidence.get("text") or "").strip()
    display = provider.replace("_", " ").title()
    summary = text[:180] if text else "Evidence recorded"
    return display, summary


def summarize_debate_artifact(ticker: str, debate: dict[str, Any]) -> tuple[str, str]:
    """Return display_name and summary for agent debate."""
    display = f"Agent debate · {ticker.upper()}"
    verdict = str(debate.get("verdict") or debate.get("recommendation") or "").strip()
    bull = str(debate.get("bull_summary") or debate.get("bull_case") or "").strip()
    bear = str(debate.get("bear_summary") or debate.get("bear_case") or "").strip()
    if verdict:
        return display, verdict[:180]
    if bull and bear:
        return display, f"Bull vs bear — {bull[:60]}… / {bear[:60]}…"[:180]
    return display, "Multi-agent bull/bear debate"
