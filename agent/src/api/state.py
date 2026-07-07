"""Lazy-init service singletons shared by session, channel, and live routes."""

from __future__ import annotations

import os

from fastapi import HTTPException

from src.api._compat import host_attr as _host_attr, set_host_attr as _set_host_attr
from src.api.helpers import RUNS_DIR, SESSIONS_DIR


# ============================================================================
# Global singletons
# ============================================================================

_session_service = None
_channel_runtime = None
_channel_bus = None
_channel_manager = None


# ============================================================================
# Lazy initializers
# ============================================================================


def _get_session_service():
    """Lazy-init session service when ENABLE_SESSION_RUNTIME=true."""
    global _session_service

    host_val = _host_attr("_session_service", None)
    if host_val is not None:
        return host_val
    if _session_service is not None:
        return _session_service

    if os.getenv("ENABLE_SESSION_RUNTIME", "true").lower() != "true":
        return None

    import asyncio

    from src.session.events import EventBus
    from src.session.service import SessionService
    from src.session.store import SessionStore

    # Honor monkeypatched SESSIONS_DIR / RUNS_DIR on api_server.
    sessions_dir = _host_attr("SESSIONS_DIR", SESSIONS_DIR)
    runs_dir = _host_attr("RUNS_DIR", RUNS_DIR)

    store = SessionStore(base_dir=sessions_dir)
    event_bus = EventBus()

    try:
        loop = asyncio.get_event_loop()
        event_bus.set_loop(loop)
    except RuntimeError:
        pass

    _session_service = SessionService(
        store=store,
        event_bus=event_bus,
        runs_dir=runs_dir,
    )
    _set_host_attr("_session_service", _session_service)
    return _session_service


def _get_channel_runtime():
    """Lazy-init IM channel runtime without starting platform adapters."""
    global _channel_runtime, _channel_bus, _channel_manager

    # Check api_server host module first (monkeypatch compatibility).
    host_rt = _host_attr("_channel_runtime", None)
    if host_rt is not None:
        return host_rt
    if _channel_runtime is not None:
        return _channel_runtime

    from src.channels.bus.queue import MessageBus
    from src.channels.config import load_channels_config
    from src.channels.manager import ChannelManager
    from src.channels.runtime import ChannelRuntime

    svc = _get_session_service()
    if not svc:
        raise HTTPException(status_code=501, detail="Session runtime not enabled")

    _channel_bus = MessageBus()
    config = load_channels_config()
    _channel_manager = ChannelManager(config, _channel_bus, session_service=svc)
    _channel_runtime = ChannelRuntime(
        bus=_channel_bus,
        session_service=svc,
        manager=_channel_manager,
        reply_timeout_s=config["reply_timeout_s"],
    )
    _set_host_attr("_channel_runtime", _channel_runtime)
    _set_host_attr("_channel_bus", _channel_bus)
    _set_host_attr("_channel_manager", _channel_manager)
    return _channel_runtime
