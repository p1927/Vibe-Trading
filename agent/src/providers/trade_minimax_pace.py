"""Trade sidecar: every MiniMax request takes a slot on Trade's one account pace.

Inside the Trade monorepo the MiniMax account has one cross-process pace (Trade DECISIONS D271,
``trade_integrations.dataflows.model_adapters.minimax_queue.account_pace_key``) shared by every process and
endpoint spending it. The OpenAI-compatible clients built here (the agent chat model, vision
OCR) send their own HTTP, so a client pointed at MiniMax carries Trade's httpx request hook:
each request, SDK retries included, waits for its slot on that pace.

The same sidecar also carries Trade's LLM call log: ``trade_llm_span`` wraps a call so it writes one
``adapter_calls`` row (tokens included) next to the pace, whichever client made it.

A client pointed anywhere else, and every client outside Trade (standalone vibe-trading, where
there is no Trade stack to import), is left exactly as it was. Kept in its own module per
Trade's docs/FORK_CONVENTIONS.md sidecar pattern.
"""

from __future__ import annotations

import contextlib
from typing import Any


def in_trade_stack() -> bool:
    """True inside the Trade monorepo (trade_integrations importable after putting it on the path).

    Standalone vibe-trading has no Trade stack and is left as it was. Inside the monorepo a broken
    ``trade_integrations`` import is NOT standalone: it raises, so MiniMax's account pace and the LLM
    call log are never silently dropped (an ``except ImportError`` fallback did exactly that).
    """
    from src.trade.hub_bridge import ensure_trade_stack_path, trade_repo_root

    if trade_repo_root() is None:
        return False
    ensure_trade_stack_path()
    return True


def with_trade_minimax_pace(
    clients: tuple[Any, Any] | None, base_url: str | None
) -> tuple[Any, Any] | None:
    """Return ``(sync, async)`` httpx clients carrying the pace hook when ``base_url`` is a
    MiniMax host inside Trade, else ``clients`` unchanged. ``clients=None`` builds the OpenAI
    SDK's default clients to carry the hook."""
    if not in_trade_stack():
        return clients
    from trade_integrations.dataflows.model_adapters.minimax_queue import paced_httpx_clients

    return paced_httpx_clients(clients, base_url)


def trade_llm_span(provider: str, model: str, tier: str = "vibe"):
    """Trade's per-call LLM log (``adapter_calls`` row + ``llm_call_*`` event), or a no-op one
    outside the Trade stack. Set ``input_tok``/``output_tok`` on the yielded dict from the usage.

    The import is resolved before the call: a ``try/except ImportError`` around the call itself
    would re-run the LLM request whenever anything inside it raised an ImportError.
    """
    if not in_trade_stack():
        return contextlib.nullcontext({})
    from trade_integrations.observability.hooks import llm_call_span

    return llm_call_span(provider=provider, model=model, tier=tier)
