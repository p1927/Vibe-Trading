"""One-off retag of legacy e2e memories (throwaway tmp memory dir only, never a real home)."""

from __future__ import annotations

from pathlib import Path

from src.memory.e2e_retag import E2E_TAG, retag_legacy_e2e_entries
from src.memory.persistent import PersistentMemory
from src.trade.session_context import memory_matches_session

_CFG = {"session_kind": "autonomous_agent", "symbols": ["NIFTY"], "execution_market": "IN"}


def _write(memdir: Path, name: str, desc: str, body: str, keywords: str = "[]") -> Path:
    path = memdir / f"project_{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {desc}\ntype: project\nid: abc123\nkeywords: {keywords}\n"
        f"quality_score: 0.5\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _entry(memdir: Path, name: str):
    return next(e for e in PersistentMemory(memdir).list_entries() if e.title == name)


def test_retag_dry_run_apply_idempotent_and_filter_follows_the_tag(tmp_path: Path) -> None:
    legacy = _write(tmp_path, "boot", "dev", "Commit from cached context after idempotent reads.", "[a, b]")
    real = _write(tmp_path, "condor", "options", "NIFTY iron condor sizing in low VIX.")
    mention = _write(tmp_path, "note", "x", "Our integration test plan for broker X is dull.")
    plain_index = tmp_path / "MEMORY.md"
    plain_index.write_text("# index\nintegration test\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in (legacy, real, mention, plain_index)}

    # dry run: reports, touches nothing (not even a lock file)
    found = retag_legacy_e2e_entries(tmp_path)
    assert sorted(p.name for p in found) == ["project_boot.md", "project_note.md"]
    assert {p: p.read_bytes() for p in before} == before
    assert not (tmp_path / ".lock").exists()

    # before the retag the filter no longer guesses from words
    assert memory_matches_session(_entry(tmp_path, "boot"), _CFG) is True

    assert retag_legacy_e2e_entries(tmp_path, apply=True) == found
    assert _entry(tmp_path, "boot").keywords == (E2E_TAG, "a", "b")
    assert memory_matches_session(_entry(tmp_path, "boot"), _CFG) is False
    assert memory_matches_session(_entry(tmp_path, "condor"), _CFG) is True
    # an e2e session still sees its own tagged memories
    assert memory_matches_session(_entry(tmp_path, "boot"), {**_CFG, "e2e_integration_test": True}) is True
    # only the keywords line changed; body and the other files are byte-identical
    assert real.read_bytes() == before[real] and plain_index.read_bytes() == before[plain_index]
    assert legacy.read_text().replace(f"keywords: [{E2E_TAG}, a, b]", "keywords: [a, b]") == before[legacy].decode()

    # idempotent: a second apply finds nothing and rewrites nothing
    snapshot = legacy.read_bytes()
    assert retag_legacy_e2e_entries(tmp_path, apply=True) == []
    assert legacy.read_bytes() == snapshot


def test_retag_adds_a_keywords_line_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "project_nokw.md"
    path.write_text("---\nname: nokw\ndescription: d\ntype: project\n---\n\nsynthetic alert replay\n", encoding="utf-8")
    assert retag_legacy_e2e_entries(tmp_path, apply=True) == [path]
    assert _entry(tmp_path, "nokw").keywords == (E2E_TAG,)
    assert path.read_text().endswith("\nsynthetic alert replay\n")
