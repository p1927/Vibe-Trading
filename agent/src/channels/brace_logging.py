"""Brace-style (loguru-compatible) logging for channel adapters.

Fork-only sidecar (docs/FORK_CONVENTIONS.md). The channel adapters were ported
from a loguru codebase: their log calls use ``{}`` placeholders with positional
args (``logger.info("sent to {}", chat_id)``) and occasionally loguru's
``logger.opt(exception=True)``. The loggers themselves are stdlib ``logging``,
which formats with ``msg % args`` -- so every such record raised
``TypeError: not all arguments converted`` inside the handler and was dropped
(``--- Logging error ---`` on stderr), including security audit lines.

Rather than rewrite ~300 upstream call sites, :func:`get_channel_logger` wraps
the stdlib logger in :class:`BraceStyleAdapter`, which renders ``{}`` messages
before handing them to stdlib logging. ``%s``-style messages pass through
untouched, so either style works on the same logger.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any


def render_brace(msg: Any, args: tuple[Any, ...]) -> tuple[Any, tuple[Any, ...]]:
    """Return ``(msg, args)`` with a brace-style message rendered, else unchanged.

    Only a ``str`` message that contains ``{`` and has args is a brace
    candidate. If ``str.format`` rejects it (a ``%s`` message that merely
    contains a literal brace, or a mapping-style ``%(key)s`` call), the
    original pair is returned so stdlib ``%`` formatting applies as before --
    including its normal ``handleError`` path for a genuinely malformed call.
    """
    if not args or not isinstance(msg, str) or "{" not in msg:
        return msg, args
    if len(args) == 1 and isinstance(args[0], Mapping):
        # stdlib's ``%(key)s`` convention; never a positional ``{}`` call.
        return msg, args
    try:
        return msg.format(*args), ()
    except (IndexError, KeyError, ValueError, AttributeError, TypeError):
        return msg, args


class _OptView:
    """Minimal stand-in for loguru's ``logger.opt(exception=True)``."""

    def __init__(self, adapter: "BraceStyleAdapter", exc_info: bool) -> None:
        self._adapter = adapter
        self._exc_info = exc_info

    def _log(self, level: int, msg: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        if self._exc_info:
            kwargs.setdefault("exc_info", True)
        # Skip this frame and the public level method that called it.
        kwargs["stacklevel"] = kwargs.get("stacklevel", 1) + 2
        self._adapter.log(level, msg, *args, **kwargs)

    def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, args, kwargs)

    def info(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, args, kwargs)

    def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, args, kwargs)

    def error(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, args, kwargs)

    def critical(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, args, kwargs)

    def exception(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, msg, args, kwargs)


class BraceStyleAdapter(logging.LoggerAdapter):
    """``LoggerAdapter`` that accepts both ``{}`` and ``%s`` message styles."""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(logger, None)

    def log(self, level: int, msg: Any, *args: Any, **kwargs: Any) -> None:
        if not self.isEnabledFor(level):
            return
        msg, args = render_brace(msg, args)
        # Skip this frame so %(funcName)s/%(lineno)d name the real call site.
        kwargs["stacklevel"] = kwargs.get("stacklevel", 1) + 1
        super().log(level, msg, *args, **kwargs)

    def opt(self, *, exception: bool = False, **_ignored: Any) -> _OptView:
        """loguru compatibility: ``logger.opt(exception=True).error(...)``."""
        return _OptView(self, bool(exception))


def get_channel_logger(name: str) -> BraceStyleAdapter:
    """Return the brace-aware logger channel code should log through."""
    return BraceStyleAdapter(logging.getLogger(name))
