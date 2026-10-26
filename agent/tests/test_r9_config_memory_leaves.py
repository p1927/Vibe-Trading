"""R9 P3 leaves (config/memory): an unreadable env file is loud; archive never overwrites."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config.bootstrap import _parse_env_file
from src.memory.hierarchy import archive_destination


def test_unreadable_env_file_raises_not_empty_layer(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes(b"\xff\xfe not utf-8 \x80")
    with pytest.raises(ValueError):  # UnicodeDecodeError; an OSError (EACCES) propagates the same way
        _parse_env_file(env)
    ok = tmp_path / "ok.env"
    ok.write_text("A=1\n# c\nB='2'\n", encoding="utf-8")
    assert _parse_env_file(ok) == {"A": "1", "B": "2"}


def test_archive_destination_never_overwrites_an_archived_body(tmp_path: Path) -> None:
    base = tmp_path / "mem"
    archive = base / "archive"
    archive.mkdir(parents=True)
    src = base / "note.md"
    src.write_text("new", encoding="utf-8")
    (archive / "note.md").write_text("OLD ARCHIVED BODY", encoding="utf-8")
    first = archive_destination(base, archive, src)
    assert first != archive / "note.md" and first.suffix == ".md"
    first.write_text("x", encoding="utf-8")
    second = archive_destination(base, archive, src)
    assert second not in (first, archive / "note.md")
    assert (archive / "note.md").read_text(encoding="utf-8") == "OLD ARCHIVED BODY"
    assert archive_destination(base, archive, base / "fresh.md") == archive / "fresh.md"
