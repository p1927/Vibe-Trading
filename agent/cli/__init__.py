"""Vibe-Trading CLI package.

The legacy single-file CLI has been preserved verbatim as
``cli/_legacy.py`` and is the source of truth for non-interactive
subcommands (``serve``, ``run``, ``mcp``, ``sessions``, ``swarm`` ...).
The front door (:mod:`cli.main`) shows the banner, runs the
onboarding wizard when needed, then drives the interactive loop
built on :mod:`cli.input`, :mod:`cli.completer`, and
:mod:`cli.commands.*`. Non-interactive entries still pass through to
``_legacy.main``.

The console-script entry in ``pyproject.toml``
(``vibe-trading = "cli:main"``) points at the ``main`` callable exported
here.

Compatibility note: tests and downstream callers historically reached
into ``cli._INIT_ENV_PATH`` / ``cli.cmd_memory_list`` / ``cli.Confirm``
etc. To preserve that surface we re-export every public name from
``_legacy`` via :func:`__getattr__`. New code should import the same
helpers from ``cli._legacy`` directly.

``_legacy`` is imported lazily so ``python -m cli._legacy …`` can run
the submodule as ``__main__`` without runpy's "already in sys.modules"
RuntimeWarning.
"""

from __future__ import annotations

import functools
import importlib
from typing import Any

from cli.main import main

_LEGACY_SYNCED_GLOBALS: tuple[str, ...] = (
    "_INIT_ENV_PATH",
    "AGENT_DIR",
    "RUNS_DIR",
    "SWARM_DIR",
    "SESSIONS_DIR",
    "UPLOADS_DIR",
    "_PROVIDER_CHOICES",
)

_legacy_mod: Any | None = None
_legacy_attr_cache: dict[str, Any] = {}


def _load_legacy() -> Any:
    """Import ``cli._legacy`` on first attribute access."""
    global _legacy_mod
    if _legacy_mod is None:
        _legacy_mod = importlib.import_module("cli._legacy")
    return _legacy_mod


def _sync_legacy_test_overrides() -> None:
    """Mirror package-level monkeypatches onto ``_legacy``'s module globals.

    Tests reach into ``cli.<NAME>`` to override constants, but legacy
    callables read ``<NAME>`` from their own module namespace. This hook
    copies any patched value back to ``_legacy`` for the allowlist below.
    """
    legacy = _load_legacy()
    pkg_globals = globals()
    for name in _LEGACY_SYNCED_GLOBALS:
        if name not in pkg_globals:
            continue
        new_value = pkg_globals[name]
        if getattr(legacy, name, None) is not new_value:
            setattr(legacy, name, new_value)


def _make_synced_legacy_cmd_wrapper(cmd_name: str):
    """Return a ``cmd_*`` proxy that syncs patches then reads ``_legacy`` live."""

    @functools.wraps(getattr(_load_legacy(), cmd_name))
    def _wrapper(*args, **kwargs):
        _sync_legacy_test_overrides()
        return getattr(_load_legacy(), cmd_name)(*args, **kwargs)

    _wrapper.__name__ = cmd_name
    return _wrapper


def _resolve_legacy_export(name: str) -> Any:
    if name in _legacy_attr_cache:
        return _legacy_attr_cache[name]
    legacy = _load_legacy()
    if name.startswith("cmd_") and name != "cmd_init" and hasattr(legacy, name):
        value: Any = _make_synced_legacy_cmd_wrapper(name)
    else:
        value = getattr(legacy, name)
    _legacy_attr_cache[name] = value
    return value


def cmd_init() -> int:
    """Compatibility wrapper for callers patching ``cli._INIT_ENV_PATH``."""
    _sync_legacy_test_overrides()
    return _load_legacy().cmd_init()


def __getattr__(name: str) -> Any:
    if name == "_legacy":
        return _load_legacy()
    legacy = _load_legacy()
    if not hasattr(legacy, name):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return _resolve_legacy_export(name)


def __dir__() -> list[str]:
    names = {
        key
        for key in globals()
        if not key.startswith("_") or key == "_legacy"
    }
    if _legacy_mod is not None:
        names.update(n for n in dir(_legacy_mod) if not n.startswith("__"))
    names.add("_legacy")
    return sorted(names)


__all__ = ["main", "cmd_init"]
