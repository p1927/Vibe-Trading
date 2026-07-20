"""Uvicorn server with clearer shutdown diagnostics."""

from __future__ import annotations

import asyncio
import logging
import time

from uvicorn.server import Server

from src.api.runtime_activity import log_shutdown_wait

logger = logging.getLogger("uvicorn.error")

_SHUTDOWN_LOG_INTERVAL_S = 2.0


class TransparentShutdownServer(Server):
    """Log which connections/tasks block graceful reload/shutdown."""

    async def _wait_tasks_to_complete(self) -> None:
        if self.server_state.connections and not self.force_exit:
            log_shutdown_wait(
                "connections",
                connection_count=len(self.server_state.connections),
                uvicorn_task_count=len(self.server_state.tasks),
            )
            logger.info("Waiting for connections to close. (CTRL+C to force quit)")
            last_log = time.monotonic()
            while self.server_state.connections and not self.force_exit:
                if time.monotonic() - last_log >= _SHUTDOWN_LOG_INTERVAL_S:
                    log_shutdown_wait(
                        "connections",
                        connection_count=len(self.server_state.connections),
                        uvicorn_task_count=len(self.server_state.tasks),
                    )
                    last_log = time.monotonic()
                await asyncio.sleep(0.1)
            log_shutdown_wait(
                "connections-done",
                connection_count=0,
                uvicorn_task_count=len(self.server_state.tasks),
            )

        if self.server_state.tasks and not self.force_exit:
            log_shutdown_wait(
                "tasks",
                connection_count=len(self.server_state.connections),
                uvicorn_task_count=len(self.server_state.tasks),
            )
            logger.info("Waiting for background tasks to complete. (CTRL+C to force quit)")
            last_log = time.monotonic()
            while self.server_state.tasks and not self.force_exit:
                if time.monotonic() - last_log >= _SHUTDOWN_LOG_INTERVAL_S:
                    log_shutdown_wait(
                        "tasks",
                        connection_count=len(self.server_state.connections),
                        uvicorn_task_count=len(self.server_state.tasks),
                    )
                    last_log = time.monotonic()
                await asyncio.sleep(0.1)
            log_shutdown_wait(
                "tasks-done",
                connection_count=len(self.server_state.connections),
                uvicorn_task_count=0,
            )

        for server in self.servers:
            await server.wait_closed()
