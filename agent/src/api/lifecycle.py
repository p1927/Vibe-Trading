"""Server lifecycle: process logging, startup preflight, shutdown.

Split out of `api_server.py` so the assembler stays an assembler. These are real orchestration
-- root logging configuration, legacy-state migration, preflight, main-loop registration, the
scheduled-research executor, the job watchdog and the channel runtime -- not router wiring, and
`tests/test_api_infrastructure.py::test_api_server_is_thin_assembler` exists to keep the two
apart. See the backlog item 2026-09-07-vibe-api-server-exceeds-thin-assembler-limit.

`api_server` re-exports every name here, so existing imports and monkeypatch targets that
addressed them through `api_server` keep working.
"""

from __future__ import annotations

import logging

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


def _configure_process_logging() -> None:
    """Attach a formatted root log handler so app loggers are not silently dropped.

    Without this the root logger has no handler at all: uvicorn's ``log_level``
    (``server_main.py``) configures only its own ``uvicorn.*`` loggers and leaves the
    root untouched, so every ``logging.getLogger(__name__)`` in ``trade_integrations.*``
    and ``src.*`` falls through to CPython's ``logging.lastResort`` — level WARNING, no
    formatter. That made ``logger.info``/``logger.debug`` equivalent to ``pass`` process-wide
    and emitted warnings as bare, prefix-less lines.

    The cost of that was not theoretical: the 2026-09-06 audit's claim that restart
    recovery never reconciles positions outlived its own fix purely because the fix's
    success log was INFO (``scheduled_startup.py``'s "autonomous agent recovery: %s") and
    therefore invisible. See ``docs/add/autonomous_agents.md`` § Honesty.

    ``configure_trade_logging`` is idempotent, so calling it here (per worker, including
    each ``--reload`` respawn) is safe.
    """
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.observability.logging_config import configure_trade_logging

        configure_trade_logging()
    except Exception:  # pragma: no cover — logging must never block startup
        logging.getLogger(__name__).warning("root logging configuration failed", exc_info=True)


# Modules a route imports on its first request. Cold, that import is 4 to 23 s (measured on the release
# API: /knowledge/wiki 23 s, /swarm/presets 10 s, /channels/status 3 s, then ~0.2 s), past the UI's 25 s
# client timeout, and /channels/status imports on the event loop thread, so it stalled every request.
_WARM_ROUTE_IMPORTS = (
    "src.swarm",
    "src.channels.runtime",
    "src.channels.manager",
    "trade_integrations.knowledge_engine.query",
)


def _warm_route_imports() -> None:
    import importlib

    for name in _WARM_ROUTE_IMPORTS:
        try:
            importlib.import_module(name)
        except Exception:  # the route reports its own import error when it is hit
            logger.warning("route import warm-up failed for %s", name, exc_info=True)
    # The first /knowledge/wiki call also parsed ~2k wiki pages (cached per mtime signature afterwards):
    # still 30 s+ cold on the release API once the imports were warm. Parse them here, off the request.
    try:
        from trade_integrations.knowledge_engine.query import load_wiki_pages

        load_wiki_pages()
    except Exception:
        logger.warning("wiki page warm-up failed", exc_info=True)
    # factor-history and forecast-lab built the aligned panel / hub context on their first request
    # (5-9 s CPU, 21-40 s on a busy host: past the UI's 25 s timeout). Build the route-default keys here;
    # the panel cache then serves them stale-while-revalidate (history_panel.load_aligned_panel_history).
    # ponytail: only the route defaults (NIFTY, 90 d, 14 d horizon) are warmed; another `days` pays one cold build.
    try:
        from trade_integrations.dataflows.prediction_ledger_bridge import list_factor_history_series

        list_factor_history_series(days=90, start=None, factors=None)
        from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import (
            context_from_hub,
        )

        context_from_hub("NIFTY", horizon_days=14)
    except Exception:
        logger.warning("prediction panel warm-up failed", exc_info=True)


async def _run_startup_preflight() -> None:
    """Run the local preflight gate on server startup; raise if a critical check failed.

    Network probes run after startup instead (src/preflight_startup.py): a startup path does no
    network I/O.
    """
    from src.preflight import critical_failures
    from src.preflight_startup import run_boot_gate, start_background_probes

    from src.config import migrate as _migrate
    # Function-local, like the rest of this module: `channels_routes`/`scheduled_routes` pull in
    # the whole route surface, and importing them at module scope here would make a cycle out of
    # what is currently a leaf.
    from src.api.state import _get_session_service
    from src.api.channels_routes import _start_channel_runtime
    from src.api.scheduled_routes import _start_scheduled_research_executor

    _configure_process_logging()
    from src.trade.tier_roots_guard import assert_release_tier_roots

    assert_release_tier_roots()  # release-profile process with misrouted roots refuses to boot

    try:
        _migrate.migrate_legacy_state()  # one-time pre-#904 state move; must never block startup
    except Exception:  # pragma: no cover — best-effort
        logging.getLogger(__name__).warning("Legacy state migration failed", exc_info=True)
    failed = critical_failures(run_boot_gate(console))
    if failed:
        # A check that declares itself critical ("agent cannot function") gates boot, the same
        # as the CLI paths in cli/_legacy.py. Raising here fails the FastAPI lifespan, so uvicorn
        # exits instead of logging "Application startup complete" on a stack it just called
        # broken. Nothing below (executor, watchdogs, channels) has started yet.
        # See .claude/backlog/items/2026-09-07-preflight-critical-not-blocking.md.
        names = ", ".join(failed)
        logger.error("startup preflight: critical check(s) failed: %s; refusing to start", names)
        raise RuntimeError(
            f"critical preflight check(s) failed: {names}; refusing to start "
            "(see the Preflight Check table above)"
        )

    import asyncio

    from src.api.async_bridge import register_main_loop

    loop = asyncio.get_running_loop()
    register_main_loop(loop)
    svc = _get_session_service()
    if svc is not None and hasattr(svc, "event_bus"):
        svc.event_bus.set_loop(loop)

    from src.scheduled_research.gil_tuning import tune_gil_switch_interval_for_scheduler

    tune_gil_switch_interval_for_scheduler()
    from src.api.loop_stall_watchdog import start_loop_stall_watchdog

    start_loop_stall_watchdog()
    _start_scheduled_research_executor()
    from src.trade.job_watchdog import start_job_watchdog

    start_job_watchdog()
    from src.config.accessor import get_env_config

    if get_env_config().agent_tuning.vibe_trading_channels_auto_start:
        await _start_channel_runtime()
    start_background_probes(console)
    import threading

    threading.Thread(target=_warm_route_imports, name="route-import-warmup", daemon=True).start()


async def _stop_scheduled_research_on_shutdown() -> None:
    """Stop the scheduled research executor on server shutdown."""
    from src.trade.job_watchdog import stop_job_watchdog
    from src.api.channels_routes import _stop_channel_runtime
    from src.api.scheduled_routes import _stop_scheduled_research_executor

    from src.api.loop_stall_watchdog import stop_loop_stall_watchdog

    stop_job_watchdog()
    stop_loop_stall_watchdog()
    try:
        await _stop_channel_runtime()
    finally:
        await _stop_scheduled_research_executor()
