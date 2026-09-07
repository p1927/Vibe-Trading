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


async def _run_startup_preflight() -> None:
    """Run preflight checks on server startup."""
    from src.preflight import run_preflight

    from src.config import migrate as _migrate
    # Function-local, like the rest of this module: `channels_routes`/`scheduled_routes` pull in
    # the whole route surface, and importing them at module scope here would make a cycle out of
    # what is currently a leaf.
    from src.api.state import _get_session_service
    from src.api.channels_routes import _start_channel_runtime
    from src.api.scheduled_routes import _start_scheduled_research_executor

    _configure_process_logging()

    try:
        _migrate.migrate_legacy_state()  # one-time pre-#904 state move; must never block startup
    except Exception:  # pragma: no cover — best-effort
        logging.getLogger(__name__).warning("Legacy state migration failed", exc_info=True)
    run_preflight(console)

    import asyncio

    from src.api.async_bridge import register_main_loop

    loop = asyncio.get_running_loop()
    register_main_loop(loop)
    svc = _get_session_service()
    if svc is not None and hasattr(svc, "event_bus"):
        svc.event_bus.set_loop(loop)

    from src.scheduled_research.gil_tuning import tune_gil_switch_interval_for_scheduler

    tune_gil_switch_interval_for_scheduler()
    _start_scheduled_research_executor()
    from src.trade.job_watchdog import start_job_watchdog

    start_job_watchdog()
    from src.config.accessor import get_env_config

    if get_env_config().agent_tuning.vibe_trading_channels_auto_start:
        await _start_channel_runtime()


async def _stop_scheduled_research_on_shutdown() -> None:
    """Stop the scheduled research executor on server shutdown."""
    from src.trade.job_watchdog import stop_job_watchdog
    from src.api.channels_routes import _stop_channel_runtime
    from src.api.scheduled_routes import _stop_scheduled_research_executor

    stop_job_watchdog()
    try:
        await _stop_channel_runtime()
    finally:
        await _stop_scheduled_research_executor()
