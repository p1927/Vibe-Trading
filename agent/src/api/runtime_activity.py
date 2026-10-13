"""Log slow / in-flight HTTP requests and named background tasks for dev transparency.

``runtime_activity_middleware`` is registered in ``api_server.py``; a request that takes 2s or
longer (time to response headers for a stream) is logged on ``uvicorn.error``, and
``STACK_DEV`` logs every request. ``register_named_task``/``finish_named_task`` log long-running
background work (the scheduled-research executor)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import Response

from src.config.accessor import get_env_config

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
    return get_env_config().trade.stack_dev.strip().lower() in {"1", "true", "yes", "on"}


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


async def runtime_activity_middleware(request: Request, call_next) -> Response:
    request_id = begin_request(request)
    try:
        return await call_next(request)
    finally:
        end_request(request_id)
