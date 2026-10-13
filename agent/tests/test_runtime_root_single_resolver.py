"""One resolver for the runtime root (Trade D306, backlog review-vibe-home-scope).

Every tier sets VIBE_TRADING_HOME to its own directory, so a literal ``/ ".vibe-trading"`` in
agent code is a tier leak waiting to happen (release writing dev's state).
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config.paths import get_runtime_root

_SRC = Path(__file__).resolve().parents[1] / "src"
_LITERAL = re.compile(r"""/\s*["']\.vibe-trading["']""")
# paths.py is the resolver; runner.py names the sandbox's own fake HOME (and its real-HOME
# fallback when VIBE_TRADING_HOME is unset, where the two are the same directory).
_ALLOWED = {"config/paths.py", "core/runner.py"}


def test_no_literal_runtime_root_outside_the_resolver() -> None:
    offenders = [
        f"{rel}:{n}: {line.strip()}"
        for path in _SRC.rglob("*.py")
        if (rel := path.relative_to(_SRC).as_posix()) not in _ALLOWED
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _LITERAL.search(line)
    ]
    assert not offenders, "use src.config.paths.get_runtime_root():\n" + "\n".join(offenders)


def test_evidence_store_default_db_follows_the_tier(monkeypatch, tmp_path: Path) -> None:
    from src.strategy_discovery.evidence_store import _default_db_path

    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path / "rel"))
    assert _default_db_path() == get_runtime_root() / "strategy_discovery.db"
    assert _default_db_path().parent == tmp_path / "rel"
