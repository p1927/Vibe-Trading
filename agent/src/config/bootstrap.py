"""One-shot environment bootstrap for the Vibe-Trading agent.

Loads layered ``.env`` files, strips blank ``os.environ`` keys that block
file values, merges with stack-exported variables (non-empty exports win),
then builds the :class:`~src.config.env_schema.EnvConfig` singleton once.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from src.config.accessor import get_env_config, reset_env_config

logger = logging.getLogger(__name__)

AGENT_DIR = Path(__file__).resolve().parents[2]

_bootstrapped = False
_lock = threading.Lock()


@dataclass(frozen=True)
class BootstrapReport:
    """Summary returned by :func:`bootstrap_environment`."""

    bootstrapped: bool
    already_bootstrapped: bool
    layers_loaded: tuple[str, ...]
    stripped_blank_keys: tuple[str, ...]
    vibe_trading_enable_scheduler: bool
    index_research_enable_scheduler: bool
    index_monitor_enable_scheduler: bool


def is_bootstrapped() -> bool:
    """Return whether :func:`bootstrap_environment` has completed in this process."""
    return _bootstrapped


def reset_bootstrap() -> None:
    """Clear bootstrap state (tests only)."""
    global _bootstrapped  # noqa: PLW0603
    with _lock:
        _bootstrapped = False
        reset_env_config()


def resolve_trade_root(trade_root: Path | None = None) -> Path | None:
    """Resolve the Trade repo root from explicit path or environment."""
    if trade_root is not None:
        return trade_root if trade_root.is_dir() else None

    for key in ("TRADE_STACK_ROOT",):
        raw = os.environ.get(key, "").strip()
        if raw:
            candidate = Path(raw)
            if candidate.is_dir():
                return candidate

    candidate = AGENT_DIR.parents[1]
    if (candidate / ".env").is_file() or (candidate / "integrations").is_dir():
        return candidate
    return None


def _env_layer_paths(trade_root: Path | None) -> list[tuple[str, Path]]:
    layers: list[tuple[str, Path]] = []
    operator = Path.home() / ".vibe-trading" / ".env"
    if operator.is_file():
        layers.append(("~/.vibe-trading/.env", operator))
    if trade_root is not None:
        trade_env = trade_root / ".env"
        if trade_env.is_file():
            layers.append(("trade/.env", trade_env))
    agent_env = AGENT_DIR / ".env"
    if agent_env.is_file():
        layers.append(("agent/.env", agent_env))
    return layers


def _strip_blank_env_keys() -> list[str]:
    stripped: list[str] = []
    for key, value in list(os.environ.items()):
        if value is not None and not str(value).strip():
            del os.environ[key]
            stripped.append(key)
    return stripped


def _parse_env_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _apply_env_layer(values: dict[str, str]) -> None:
    for key, value in values.items():
        if value.strip():
            os.environ[key] = value


def _capture_non_empty_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if value is not None and str(value).strip()
    }


def bootstrap_environment(*, trade_root: Path | None = None) -> BootstrapReport:
    """Load all env layers once and refresh :func:`get_env_config`.

    Idempotent: subsequent calls return the cached report without re-reading files.
    """
    global _bootstrapped  # noqa: PLW0603

    with _lock:
        if _bootstrapped:
            cfg = get_env_config()
            return BootstrapReport(
                bootstrapped=True,
                already_bootstrapped=True,
                layers_loaded=(),
                stripped_blank_keys=(),
                vibe_trading_enable_scheduler=cfg.agent_tuning.vibe_trading_enable_scheduler,
                index_research_enable_scheduler=cfg.agent_tuning.index_research_enable_scheduler,
                index_monitor_enable_scheduler=cfg.agent_tuning.index_monitor_enable_scheduler,
            )

        preserved = _capture_non_empty_env()
        stripped = _strip_blank_env_keys()

        resolved_trade = resolve_trade_root(trade_root)
        loaded_labels: list[str] = []
        for label, path in _env_layer_paths(resolved_trade):
            _apply_env_layer(_parse_env_file(path))
            loaded_labels.append(label)

        for key, value in preserved.items():
            os.environ[key] = value

        reset_env_config()
        _bootstrapped = True
        cfg = get_env_config()

        logger.info(
            "env bootstrap ready | layers=%s stripped_blank=%d master=%s index=%s monitor=%s",
            ",".join(loaded_labels) or "none",
            len(stripped),
            cfg.agent_tuning.vibe_trading_enable_scheduler,
            cfg.agent_tuning.index_research_enable_scheduler,
            cfg.agent_tuning.index_monitor_enable_scheduler,
        )

        return BootstrapReport(
            bootstrapped=True,
            already_bootstrapped=False,
            layers_loaded=tuple(loaded_labels),
            stripped_blank_keys=tuple(stripped),
            vibe_trading_enable_scheduler=cfg.agent_tuning.vibe_trading_enable_scheduler,
            index_research_enable_scheduler=cfg.agent_tuning.index_research_enable_scheduler,
            index_monitor_enable_scheduler=cfg.agent_tuning.index_monitor_enable_scheduler,
        )
