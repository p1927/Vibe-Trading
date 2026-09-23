"""Trade sidecar: every MiniMax request takes a slot on Trade's one account pace.

Inside the Trade monorepo the MiniMax account has one cross-process pace (Trade DECISIONS D271,
``trade_integrations.nse_browser.minimax_queue.account_pace_key``) shared by every process and
endpoint spending it. The OpenAI-compatible clients built here (the agent chat model, vision
OCR) send their own HTTP, so a client pointed at MiniMax carries Trade's httpx request hook:
each request, SDK retries included, waits for its slot on that pace.

A client pointed anywhere else, and every client outside Trade (standalone vibe-trading, where
``trade_integrations`` is not importable), is left exactly as it was. Kept in its own module per
Trade's docs/FORK_CONVENTIONS.md sidecar pattern.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def with_trade_minimax_pace(
    clients: tuple[Any, Any] | None, base_url: str | None
) -> tuple[Any, Any] | None:
    """Return ``(sync, async)`` httpx clients carrying the pace hook when ``base_url`` is a
    MiniMax host inside Trade, else ``clients`` unchanged. ``clients=None`` builds the OpenAI
    SDK's default clients to carry the hook."""
    try:
        from trade_integrations.nse_browser.minimax_queue import (
            MINIMAX_HOST_SUFFIXES,
            apace_httpx_request,
            pace_httpx_request,
        )
    except ImportError:
        return clients
    if not (urlparse(base_url or "").hostname or "").lower().endswith(MINIMAX_HOST_SUFFIXES):
        return clients
    if clients is None:
        from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

        clients = (DefaultHttpxClient(), DefaultAsyncHttpxClient())
    sync_client, async_client = clients
    for client, hook in ((sync_client, pace_httpx_request), (async_client, apace_httpx_request)):
        hooks = client.event_hooks
        client.event_hooks = {**hooks, "request": [*hooks.get("request", []), hook]}
    return clients
