"""Everything under the runtime root follows ``VIBE_TRADING_HOME`` (Trade backlog
2026-09-06-vibe-home-bypassed).

``get_runtime_root()`` has always honoured ``VIBE_TRADING_HOME``, but ~30 call sites resolved
the same directory with a literal ``Path.home() / ".vibe-trading"``. So the release tier
(``VIBE_TRADING_HOME=~/.vibe-trading-release``) read and wrote dev's ``.env`` (provider settings
and API keys), dev's long-term memory, dev's trade widgets, and more. Two guards:

* a ratchet: no non-test module may spell that literal again, outside an explicit allowlist;
* a behaviour check in a fresh interpreter with ``VIBE_TRADING_HOME`` set, for the paths
  that are baked in at import time (the conftest sandbox deletes the variable, so this cannot
  run in-process).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]

# Any `<something> / ".vibe-trading"`, not only `Path.home() / ...`: path_utils spelled it
# `home = Path.home()` then `home / ".vibe-trading"`, which the narrower pattern missed, and
# kept granting the release tier's file tools access to dev's uploads/runs.
_LITERAL = re.compile(r"""/\s*["']\.vibe-trading["']""")

#: The only non-test modules allowed to spell the literal, each with its reason.
_ALLOWED = {
    # The default inside get_runtime_root() itself.
    "src/config/paths.py",
    # Falls back to the literal only if src.config.paths cannot be imported at all
    # (the package is usable without the config layer).
    "src/strategy_discovery/evidence_store.py",
    # The sandbox HOME's own layout (the subprocess gets no VIBE_TRADING_HOME, so its
    # runtime root IS <sandbox>/.vibe-trading), plus the no-VIBE_TRADING_HOME fallback in
    # _sandbox_reexpose_source. Pinned by test_sandbox_reexposes_the_tier_root below.
    "src/core/runner.py",
}


def _non_test_python_files() -> list[Path]:
    out = []
    for path in AGENT_DIR.rglob("*.py"):
        rel = path.relative_to(AGENT_DIR).as_posix()
        if rel.startswith(("tests/", ".venv/")) or "/node_modules/" in rel or "__pycache__" in rel:
            continue
        out.append(path)
    return out


def test_no_literal_runtime_root_outside_allowlist() -> None:
    offenders = []
    for path in _non_test_python_files():
        rel = path.relative_to(AGENT_DIR).as_posix()
        if rel in _ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _LITERAL.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Use src.config.paths.get_runtime_root() instead of a literal "
        "Path.home() / '.vibe-trading' (it ignores VIBE_TRADING_HOME, so the release tier "
        "would share dev's state):\n" + "\n".join(offenders)
    )


def test_allowlist_entries_still_exist() -> None:
    """A stale allowlist entry would silently permit a new literal at that path."""
    for rel in _ALLOWED:
        text = (AGENT_DIR / rel).read_text(encoding="utf-8")
        assert _LITERAL.search(text), f"{rel} no longer needs its allowlist entry"


_PROBE = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from src.api import helpers
from src.memory import persistent, search_index
from src.strategy_store import sqlite_store
from src.agent import skills
from src.swarm import presets
from src.providers import llm
from src.trading import tap_forward
from src.config import bootstrap
from src.tools import qveris_tool
from backtest.loaders import mt5_loader, qveris_loader, local_loader
import importlib
cli_main = importlib.import_module("cli.main")  # `from cli import main` is the re-exported function
print(json.dumps({
    "env_path": str(helpers.ENV_PATH),
    "env_display": helpers._project_relative_path(helpers.ENV_PATH),
    "memory": str(persistent.MEMORY_BASE),
    "memory_index": str(search_index._DEFAULT_DB_PATH),
    "strategy_store": str(sqlite_store._DEFAULT_DB_PATH),
    "skills": str(skills.USER_SKILLS_DIR),
    "presets": str(presets.USER_PRESETS_DIR),
    "llm_env0": str(llm._ENV_CANDIDATES[0]),
    "llm_labels_aligned": len(llm._ENV_LABELS) == len(llm._ENV_CANDIDATES),
    "tap_env0": str(tap_forward._ENV_CANDIDATES[0]),
    "bootstrap_first_layer": [label for label, _ in bootstrap._env_layer_paths(None)][:1],
    "qveris": str(qveris_tool.QVERIS_CONFIG_PATH),
    "mt5": str(mt5_loader._MT5_CONFIG_PATH),
    "qveris_loader": str(qveris_loader._CONFIG_PATH),
    "data_bridge": str(local_loader._CONFIG_DIR),
    "cli_env": str(cli_main._ENV_PATH),
}))
"""


def test_import_time_paths_follow_vibe_trading_home(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    tier_root = fake_home / ".vibe-trading-release"
    tier_root.mkdir(parents=True)
    (tier_root / ".env").write_text("X=1\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    env.update({"HOME": str(fake_home), "USERPROFILE": str(fake_home), "VIBE_TRADING_HOME": str(tier_root)})
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(AGENT_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    root = str(tier_root)
    for key in (
        "env_path", "memory", "memory_index", "strategy_store", "skills", "presets",
        "llm_env0", "tap_env0", "qveris", "mt5", "qveris_loader", "data_bridge", "cli_env",
    ):
        assert got[key].startswith(root + os.sep), f"{key} -> {got[key]} is not under {root}"
    # The settings API names the tier's own file, not a hardcoded ~/.vibe-trading/.env.
    assert got["env_display"] == "~/.vibe-trading-release/.env"
    assert got["bootstrap_first_layer"] == ["~/.vibe-trading-release/.env"]
    assert got["llm_labels_aligned"] is True


@pytest.mark.parametrize(
    "call",
    [
        "from src.tools.autopilot_tool import _run_dir_for_hypothesis as f; print(f('h1'))",
        "from src.tools.alpha_bench_tool import _default_output_dir as f; print(f())",
        "from src.hypotheses.registry import default_hypotheses_path as f; print(f())",
        "from src.shadow_account.fonts import fonts_dir as f; print(f())",
        "from cli.onboard import _env_path as f; print(f())",
        "from cli.input import _default_history_path as f; print(f())",
        "from backtest.loaders.base import loader_cache_root as f; print(f())",
    ],
)
def test_runtime_call_sites_follow_vibe_trading_home(tmp_path: Path, call: str) -> None:
    fake_home = tmp_path / "home"
    tier_root = fake_home / ".vibe-trading-release"
    tier_root.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIBE_TRADING_HYPOTHESES_PATH")}
    env.update({"HOME": str(fake_home), "USERPROFILE": str(fake_home), "VIBE_TRADING_HOME": str(tier_root)})
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); " + call],
        cwd=str(AGENT_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    out = proc.stdout.strip().splitlines()[-1]
    assert out.startswith(str(tier_root) + os.sep), out


def _two_tier_home(tmp_path: Path) -> tuple[Path, Path, Path]:
    real_home = tmp_path / "home"
    dev_root = real_home / ".vibe-trading"
    tier_root = real_home / ".vibe-trading-release"
    for root, tier in ((dev_root, "dev"), (tier_root, "release")):
        (root / "cache").mkdir(parents=True)
        (root / "cache" / "marker").write_text(tier, encoding="utf-8")
        (root / "qveris.json").write_text(json.dumps({"tier": tier}), encoding="utf-8")
    return real_home, dev_root, tier_root


def test_sandbox_reexposes_the_tier_root(monkeypatch, tmp_path: Path) -> None:
    """Generated strategies run in a subprocess with an ephemeral HOME and no
    VIBE_TRADING_HOME, so their loaders read <sandbox>/.vibe-trading. That must be filled
    from THIS tier's root: filling it from ~/.vibe-trading gave release's backtests dev's
    loader cache and qveris config, and wrote into dev's cache through the symlink."""
    import shutil

    from src.core import runner

    real_home, dev_root, tier_root = _two_tier_home(tmp_path)
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tier_root))
    sandbox = runner._prepare_sandbox_home(real_home)
    try:
        dst = sandbox / ".vibe-trading"
        assert (dst / "cache" / "marker").read_text(encoding="utf-8") == "release"
        assert json.loads((dst / "qveris.json").read_text(encoding="utf-8")) == {"tier": "release"}
        assert (dst / "cache").resolve() == (tier_root / "cache").resolve()
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    assert (dev_root / "cache" / "marker").read_text(encoding="utf-8") == "dev"

    # Without VIBE_TRADING_HOME the tier root IS ~/.vibe-trading: unchanged behaviour.
    monkeypatch.delenv("VIBE_TRADING_HOME")
    sandbox = runner._prepare_sandbox_home(real_home)
    try:
        assert (sandbox / ".vibe-trading" / "cache" / "marker").read_text(encoding="utf-8") == "dev"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_file_tool_roots_never_grant_another_tiers_root(monkeypatch, tmp_path: Path) -> None:
    """The file-tool read/write/run allowlists used to add ~/.vibe-trading/{uploads,imports,
    runs,shadow_runs} next to the tier's own root, so release's file tools could read and
    write dev's uploads and runs."""
    from src.tools import path_utils

    real_home, dev_root, tier_root = _two_tier_home(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: real_home))
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tier_root))
    monkeypatch.delenv("VIBE_TRADING_ALLOWED_WRITE_ROOTS", raising=False)
    dev = dev_root.resolve()
    for name, roots in (
        ("write", path_utils.allowed_write_roots()),
        ("file", path_utils.allowed_file_roots()),
        ("run", path_utils._default_run_roots()),
    ):
        leaked = [r for r in roots if r.resolve().is_relative_to(dev)]
        assert not leaked, f"{name} roots grant dev's runtime root: {leaked}"
    assert (tier_root / "uploads").resolve() in path_utils.allowed_write_roots()
    assert (tier_root / "runs").resolve() in path_utils.allowed_file_roots()
