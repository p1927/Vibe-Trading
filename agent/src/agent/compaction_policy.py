"""Fork-only compaction-policy helpers used by ``src/agent/loop.py`` (an upstream file).

Kept in a sidecar module — pure, self-contained functions/constants only —
so upstream's loop.py only carries single-line calls into here instead of
this logic being spliced into its compaction machinery. Functions that are
entangled with loop.py's own module-level lazy-constant system
(``_override``/``_token_threshold``, both upstream's own mechanism) stay in
loop.py rather than being awkwardly injected across this boundary.
"""

from __future__ import annotations

#: Tools whose results must survive microcompact even past KEEP_RECENT —
#: e.g. autonomous-agent status/decision tools the model needs to keep
#: referencing across many iterations of a long-running run.
PROTECTED_TOOL_NAMES = frozenset(
    {
        "get_autonomous_agent_status",
        "get_autonomous_market_feedback",
        "get_research_status",
        "get_options_trade_widget",
        "get_stock_trade_widget",
        "record_autonomous_decision",
        "execute_autonomous_basket",
        "stop_autonomous_agents",
        "get_us_quote",
        "get_stock_browse",
        "place_order",
        "set_agent_watch_spec",
    }
)

COMPACT_POLICY_NORMAL = "normal"
COMPACT_POLICY_DEFER = "defer"


def is_protected_tool(name: str | None) -> bool:
    if not name:
        return False
    if name in PROTECTED_TOOL_NAMES:
        return True
    return any(name.endswith(f"_{protected}") or name == protected for protected in PROTECTED_TOOL_NAMES)


def assistant_tool_batch_end(body: list, assistant_idx: int) -> int:
    """Return index after the last tool result following an assistant tool_call message."""
    j = assistant_idx + 1
    while j < len(body) and body[j].get("role") == "tool":
        j += 1
    return j


def adjust_cut_idx_for_tool_batches(body: list, cut_idx: int) -> int:
    """Keep assistant tool_call batches intact in the preserved tail when compacting."""
    if not body:
        return cut_idx
    cut_idx = max(0, min(cut_idx, len(body)))
    for i in range(cut_idx - 1, -1, -1):
        msg = body[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            batch_end = assistant_tool_batch_end(body, i)
            if i < cut_idx < batch_end:
                cut_idx = i
            break
        if msg.get("role") != "tool":
            break
    while 0 < cut_idx < len(body) and body[cut_idx].get("role") == "tool":
        cut_idx += 1
    return cut_idx


def compute_tail_cut_idx(body: list, tail_budget: int) -> int:
    """Choose head/tail split index honoring a token budget on the preserved tail."""
    accumulated = 0
    cut_idx = len(body)
    for i in range(len(body) - 1, -1, -1):
        content = body[i].get("content", "")
        msg_tokens = (len(str(content)) // 4) + 10
        if accumulated + msg_tokens > tail_budget:
            cut_idx = i + 1
            break
        accumulated += msg_tokens
        cut_idx = i
    return adjust_cut_idx_for_tool_batches(body, cut_idx)
