"""Path constants, dotenv I/O, SPA deep-link middleware, and path-parameter validation."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api._compat import host_attr as _host_attr
from src.core.atomic_write import SECRET_WRITE, atomic_write_text
from src.config.paths import get_runs_dir, get_runtime_root, get_sessions_dir, get_uploads_dir
from src.tools.redaction import redact_internal_paths, redact_text

logger = logging.getLogger(__name__)


# ============================================================================
# Path constants
# ============================================================================

# helpers.py lives at agent/src/api/helpers.py — 4 levels up to Vibe-Trading/.
# AGENT_DIR stays a code location; state dirs resolve under the user-level
# runtime root (#904).
_AGENT_DIR = Path(__file__).resolve().parent.parent.parent  # agent/

RUNS_DIR = get_runs_dir()
SESSIONS_DIR = get_sessions_dir()
UPLOADS_DIR = get_uploads_dir()
AGENT_DIR = _AGENT_DIR
# This tier's runtime root (VIBE_TRADING_HOME), like RUNS_DIR/SESSIONS_DIR above. A literal
# ~/.vibe-trading here made the release tier read and WRITE dev's provider settings and API
# keys (Trade backlog 2026-09-06-vibe-home-bypassed).
ENV_PATH = get_runtime_root() / ".env"
LEGACY_ENV_PATH = AGENT_DIR / ".env"
ENV_EXAMPLE_PATH = AGENT_DIR / ".env.example"


# ============================================================================
# SPA deep-link fallback
# ============================================================================

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
_SPA_HTML_EXACT_PATHS: frozenset[str] = frozenset({"/correlation", "/prediction"})
_SPA_HTML_PATH_REGEX: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/runs/[^/]+/?$"),
)
# When the SPA static mount catches a 404, do not serve index.html for API paths.
_SPA_FALLBACK_BLOCK_PREFIXES: tuple[str, ...] = (
    "trade/",
    "sessions/",
    "runs/",
    "swarm/",
    "settings/",
    "channels/",
    "mandate/",
    "live/",
    "upload/",
    "alpha/",
    "qveris/",
    "autonomous-agents/",
    "board/",
    "memory/",
    "watches",
    "scheduled-runs",
    "scheduler-registry",
    "trading/",
    "knowledge/",
    "execution-advisor",
    "options/",
    "auth/",
    "system/",
    "shadow-reports/",
    "skills",
    "docs",
    "redoc",
    "openapi.json",
)


def _should_spa_static_fallback(path: str) -> bool:
    """Return False when a missing path should stay a real 404 (API namespace)."""
    normalized = (path or "").lstrip("/")
    if not normalized:
        return True
    return not any(normalized.startswith(prefix) for prefix in _SPA_FALLBACK_BLOCK_PREFIXES)


class SPAStaticFiles(StaticFiles):
    """Serve index.html for SPA routes; keep API 404s as JSON/plain 404."""

    async def get_response(self, path: str, scope: Dict[str, Any]):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            if not _should_spa_static_fallback(path):
                raise
            return await super().get_response("index.html", scope)


def _is_spa_html_route(path: str) -> bool:
    """Return True when *path* corresponds to a frontend SPA page that shadows
    an API endpoint and should fall back to ``index.html`` on browser
    navigation."""
    if path in _SPA_HTML_EXACT_PATHS:
        return True
    return any(pattern.match(path) for pattern in _SPA_HTML_PATH_REGEX)


async def _spa_html_deep_link_fallback(request: Request, call_next):
    """Serve ``frontend/dist/index.html`` when a browser navigates directly to
    an SPA path that also exists as an API endpoint."""
    if request.method == "GET":
        accept = request.headers.get("accept", "")
        if "text/html" in accept and _is_spa_html_route(request.url.path):
            index = _FRONTEND_DIST / "index.html"
            if index.exists():
                return FileResponse(str(index))
    return await call_next(request)


# ============================================================================
# Dotenv helpers
# ============================================================================


def _ensure_agent_env_file(path: Path | None = None) -> Path:
    """Ensure the selected settings dotenv exists with private permissions."""
    env_path = path or _host_attr("ENV_PATH", ENV_PATH)
    env_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not env_path.exists():
        atomic_write_text(env_path, "# Created by Vibe-Trading Web UI settings.\n", **SECRET_WRITE)
    return env_path


def _strip_env_value(value: str) -> str:
    """Remove basic dotenv quotes and inline comments."""
    value = value.strip()
    if value[:1] in {"'", '"'}:
        q = value[0]
        i = 1
        while i < len(value):
            if value[i] == "\\" and i + 1 < len(value):
                i += 2
                continue
            if value[i] == q:
                return value[1:i].strip()
            i += 1
        return value
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value.strip()


def _dotenv_key(raw_key: str) -> str:
    """Return the canonical env key, stripping an optional ``export `` prefix.

    python-dotenv accepts ``export KEY=value``; Settings must too so reads and
    upserts hit the same key users set when sourcing a shell-style dotenv.
    """
    key = raw_key.strip()
    if key.lower().startswith("export "):
        key = key[7:].strip()
    return key


def _read_env_values(path: Path) -> Dict[str, str]:
    """Read active KEY=value entries from a dotenv file."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = _dotenv_key(key)
        if key:
            values[key] = _strip_env_value(value)
    return values


def _project_relative_path(path: Path) -> str:
    """Return a project-relative display path without leaking an absolute path."""
    home = Path.home()
    try:
        # A runtime-root file (the settings .env) is shown home-collapsed, so each tier
        # reports its own root (~/.vibe-trading/.env vs ~/.vibe-trading-release/.env)
        # instead of one hardcoded string, without leaking the absolute home path.
        if path.is_relative_to(get_runtime_root()) and path.is_relative_to(home):
            return "~/" + path.relative_to(home).as_posix()
    except (TypeError, ValueError):
        pass
    try:
        return path.resolve().relative_to(AGENT_DIR.parent.resolve()).as_posix()
    except ValueError:
        return path.name


def _format_env_value(value: str) -> str:
    """Format a dotenv value without allowing multiline injection."""
    if "\n" in value or "\r" in value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Environment values cannot contain newlines",
        )
    value = value.strip()
    if not value:
        return ""
    if any(ch.isspace() for ch in value) or "#" in value:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _write_env_values(path: Path, updates: Dict[str, str]) -> None:
    """Upsert active dotenv values while preserving comments and ordering."""
    _ensure_agent_env_file(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    # Last active KEY= wins on read; update that line so upserts stick.
    last_active: Dict[str, int] = {}
    for index, raw in enumerate(lines):
        stripped = raw.lstrip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key = _dotenv_key(stripped.split("=", 1)[0])
        if key in updates:
            last_active[key] = index
    seen: set[str] = set()
    for key, index in last_active.items():
        stripped = lines[index].lstrip()
        prefix = "export " if stripped.lower().startswith("export ") else ""
        lines[index] = f"{prefix}{key}={_format_env_value(updates[key])}"
        seen.add(key)
    missing = [key for key in updates if key not in seen]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Updated from Web UI")
        for key in missing:
            lines.append(f"{key}={_format_env_value(updates[key])}")
    atomic_write_text(path, "\n".join(lines) + "\n", **SECRET_WRITE)


def _is_configured_secret(value: str, placeholders: set[str]) -> bool:
    """Return True when a secret is set and not a documented placeholder."""
    normalized = value.strip().strip('"').strip("'")
    if not normalized:
        return False
    return normalized.lower() not in {p.lower() for p in placeholders}


def _coerce_float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ============================================================================
# Path-parameter validation
# ============================================================================

_SAFE_PATH_PARAM_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validate_path_param(value: str, kind: str) -> None:
    """Reject path parameters that could escape the parent directory."""
    if not _SAFE_PATH_PARAM_RE.fullmatch(value or ""):
        raise HTTPException(status_code=400, detail=f"invalid {kind}")


def is_unsafe_path_component(value: str) -> bool:
    """True when *value* could climb out of the directory it is joined onto.

    Names (tickers like ``M&M``, index names like ``NIFTY 50``) are looser than ids, so this
    rejects only what changes the path: a separator, NUL/control char, or a bare ``.``/``..``.
    """
    return value in {".", ".."} or any(c in "/\\" or ord(c) < 32 for c in value)


def safe_path_component(value: str, kind: str) -> str:
    """Return *value* unchanged, or raise 400 when it is an unsafe path component."""
    if is_unsafe_path_component(value):
        raise HTTPException(status_code=400, detail=f"invalid {kind}")
    return value


_ERROR_DETAIL_MAX_CHARS = 300


def redacted_error_detail(exc: BaseException) -> str:
    """Client-safe text for an upstream failure; also logs the redacted one-liner.

    Call inside the ``except`` block. A raw ``str(exc)`` can carry filesystem paths or a
    credential-bearing URL; this keeps the diagnosis (type + message) and drops those.
    """
    text = redact_internal_paths(redact_text(str(exc) or type(exc).__name__))[:_ERROR_DETAIL_MAX_CHARS]
    logger.warning("upstream failure surfaced to client: %s: %s", type(exc).__name__, text)
    return text


@contextmanager
def bad_gateway_on_error(log: logging.Logger, message: str, *args: object):
    """Map any failure inside the ``with`` body to HTTP 502 with a redacted detail.

    The one shape the trade routes repeated ~70 times: ``try: ... except Exception as exc:
    log.exception(message, *args); raise HTTPException(502, redacted_error_detail(exc)) from exc``.
    It catches ``Exception`` (so an ``HTTPException`` raised inside is converted too, as the inline
    blocks did); ``return`` inside the body still returns normally.
    """
    try:
        yield
    except Exception as exc:
        log.exception(message, *args)
        raise HTTPException(status_code=502, detail=redacted_error_detail(exc)) from exc
