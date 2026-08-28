"""Sidecar for src/preflight.py — see docs/FORK_CONVENTIONS.md (Trade repo root).

Fork-only helper: run_preflight()'s checks are independent, synchronous
functions dominated by unrelated network round trips (LLM ping, OKX candle
fetch, yfinance quote) plus a couple of heavy cold imports (prediction ML
libraries). None of them depend on another's result, so running them one
after another pays every check's latency in series. Kept as a separate file
(rather than inlined in preflight.py, an upstream-owned file) so this stays a
one-import, one-call diff against upstream.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, TypeVar

T = TypeVar("T")


def run_checks_concurrently(checks: List[Callable[[], T]]) -> List[T]:
    """Run each zero-arg check callable in its own thread.

    Returns results in the same order as ``checks`` regardless of finish
    order (``ThreadPoolExecutor.map`` preserves input order), so callers that
    zip results back against the check list don't need to change.
    """
    if not checks:
        return []
    with ThreadPoolExecutor(max_workers=len(checks)) as executor:
        return list(executor.map(lambda fn: fn(), checks))
