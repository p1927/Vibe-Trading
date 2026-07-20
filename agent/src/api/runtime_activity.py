"""Track in-flight HTTP work and named background tasks for dev transparency."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("uvicorn.error")

_lock = threading.Lock()


@dataclass(frozen=True)
class ActiveRequest:
    request_id: str
    method: str
    path: str
    query: str
    started_at: float
    client: str


_active_requests: dict[str, ActiveRequest] = {}
_named_tasks: dict[str, tuple[str, float]] = {}


def _dev_verbose() -> bool:
    return os.getenv("STACK_DEV", "").strip().lower() in {"1", "true", "yes", "on"}


def begin_request(request: Request) -> str:
    request_id = uuid.uuid4().hex[:8]
    path = request.url.path
    query = str(request.url.query) if request.url.query else ""
    client = request.client.host if request.client else "?"
    record = ActiveRequest(
        request_id=request_id,
        method=request.method,
        path=path,
        query=query,
        started_at=time.monotonic(),
        client=client,
    )
    with _lock:
        _active_requests[request_id] = record
    if _dev_verbose():
        suffix = f"?{query}" if query else ""
        logger.info("[runtime] → %s %s%s (%s)", record.method, path, suffix, client)
    return request_id


def end_request(request_id: str) -> None:
    with _lock:
        record = _active_requests.pop(request_id, None)
    if record is None:
        return
    elapsed = time.monotonic() - record.started_at
    suffix = f"?{record.query}" if record.query else ""
    if _dev_verbose() or elapsed >= 2.0:
        logger.info(
            "[runtime] ← %s %s%s (%.1fs)",
            record.method,
            record.path,
            suffix,
            elapsed,
        )


def register_named_task(name: str) -> str:
    task_id = uuid.uuid4().hex[:8]
    with _lock:
        _named_tasks[task_id] = (name, time.monotonic())
    if _dev_verbose():
        logger.info("[runtime] +task %s", name)
    return task_id


def finish_named_task(task_id: str) -> None:
    with _lock:
        entry = _named_tasks.pop(task_id, None)
    if entry is None:
        return
    name, started_at = entry
    elapsed = time.monotonic() - started_at
    logger.info("[runtime] -task %s (%.1fs)", name, elapsed)


def snapshot() -> dict[str, Any]:
    now = time.monotonic()
    with _lock:
        requests = [
            {
                "method": record.method,
                "path": record.path,
                "query": record.query,
                "elapsed_s": round(now - record.started_at, 1),
                "client": record.client,
            }
            for record in _active_requests.values()
        ]
        named_tasks = [
            {"name": name, "elapsed_s": round(now - started_at, 1)}
            for name, started_at in _named_tasks.values()
        ]
    return {"requests": requests, "named_tasks": named_tasks}


def log_shutdown_wait(
    phase: str,
    *,
    connection_count: int,
    uvicorn_task_count: int,
) -> None:
    """Log what is still running while uvicorn waits to reload/stop."""
    snap = snapshot()
    request_count = len(snap["requests"])
    named_count = len(snap["named_tasks"])
    logger.info(
        "[runtime] shutdown wait (%s): uvicorn_connections=%d uvicorn_tasks=%d "
        "tracked_requests=%d named_tasks=%d",
        phase,
        connection_count,
        uvicorn_task_count,
        request_count,
        named_count,
    )
    for item in sorted(snap["requests"], key=lambda row: -row["elapsed_s"]):
        suffix = f"?{item['query']}" if item["query"] else ""
        logger.info(
            "[runtime]   in-flight %s %s%s (%.1fs, %s)",
            item["method"],
            item["path"],
            suffix,
            item["elapsed_s"],
            item["client"],
        )
    for item in sorted(snap["named_tasks"], key=lambda row: -row["elapsed_s"]):
        logger.info(
            "[runtime]   background %s (%.1fs)",
            item["name"],
            item["elapsed_s"],
        )
    if connection_count and not request_count:
        logger.info(
            "[runtime]   note: open connections with no tracked request yet (handshake/SSE idle)"
        )


async def runtime_activity_middleware(request: Request, call_next) -> Response:
    request_id = begin_request(request)
    try:
        return await call_next(request)
    finally:
        end_request(request_id)


def tracked_create_task(coro, *, name: str) -> asyncio.Task | None:
    """Create a named asyncio task and log when it finishes."""
    task_id = register_named_task(name)

    async def _runner() -> Any:
        try:
            return await coro
        finally:
            finish_named_task(task_id)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("[runtime] cannot create task %s: no running event loop", name)
        finish_named_task(task_id)
        return None
    return loop.create_task(_runner(), name=name)
