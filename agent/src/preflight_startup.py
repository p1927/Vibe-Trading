"""Sidecar for src/preflight.py — see docs/FORK_CONVENTIONS.md (Trade repo root).

The API server's startup preflight, split in two:

* ``run_boot_gate`` — the checks that decide whether the server may start. Local and cheap only:
  env bootstrap, LLM provider *configuration* (no ping), prediction-ML packages *present* (no
  import), and presence checks. A critical failure here still refuses boot (``critical_failures``).
* ``start_background_probes`` — network reachability (LLM ping, OKX, yfinance) and heavy imports
  (the prediction-ML libraries, ccxt). They run in a daemon thread once startup has finished and
  are reported as health info: the same table in the log, plus a WARNING per degraded probe (ERROR
  for a critical one, e.g. the LLM provider unreachable or an ML library that fails to import).

Why: on 2026-09-23 a release promotion failed because the Vibe API took ~3 min to start under
load, and about a minute of that sat inside these probes, past the stack's health wait
(.claude/backlog/items/2026-09-23-vibe-api-slow-startup.md). A startup path does no network I/O;
a vendor being slow or down at boot must not keep the API (and the scheduled collectors it runs)
from serving. The CLI keeps calling ``run_preflight`` unchanged.
"""

from __future__ import annotations

import logging
import threading
from typing import List

from rich.console import Console

from src.preflight import (
    CheckResult,
    _check_akshare,
    _check_ccxt,
    _check_content_filter_threshold,
    _check_llm_provider,
    _check_okx,
    _check_tushare,
    _check_yfinance,
    print_preflight,
)
from src.preflight_checks import check_environment, check_prediction_ml, check_prediction_ml_installed
from src.preflight_parallel import run_checks_concurrently

logger = logging.getLogger(__name__)


def run_boot_gate(console: Console) -> List[CheckResult]:
    """Run the local startup checks, print them, and return the results for the caller to gate on."""
    # check_environment first and alone: it is the env bootstrap the other checks read (see the
    # comment in run_preflight).
    results = [check_environment()] + run_checks_concurrently([
        lambda: _check_llm_provider(ping=False),
        check_prediction_ml_installed,
        _check_tushare,
        _check_akshare,
        _check_content_filter_threshold,
    ])
    print_preflight(console, results)
    return results


def _run_probes(console: Console) -> List[CheckResult]:
    try:
        results = run_checks_concurrently(
            [_check_llm_provider, check_prediction_ml, _check_okx, _check_yfinance, _check_ccxt]
        )
    except Exception:
        logger.exception("background preflight probes crashed")
        return []
    print_preflight(console, results, title="Preflight probes (after startup)")
    for r in results:
        if r.status == "ready":
            continue
        log = logger.error if r.critical else logger.warning
        log("preflight probe %s: %s — %s (%s)", r.name, r.status, r.message, r.impact or "degraded")
    return results


def start_background_probes(console: Console) -> threading.Thread:
    """Start the network/heavy-import probes in a daemon thread; never blocks startup."""
    thread = threading.Thread(target=_run_probes, args=(console,), daemon=True, name="preflight-probes")
    thread.start()
    return thread
