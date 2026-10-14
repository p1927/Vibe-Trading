"""E2E-test memory tag and the one-off migration that tags legacy entries.

Autonomous-agent sessions must not auto-recall memories written by an e2e/integration run. The
filter used to guess from substrings of the entry text (``_E2E_MEMORY_MARKERS``), so a real memory
that mentioned "integration test" was hidden and an e2e one worded differently leaked. It is now
a tag: an entry whose ``keywords`` carry :data:`E2E_TAG` is an e2e memory
(``src.trade.session_context.memory_matches_session``).

:func:`retag_legacy_e2e_entries` is the one-off, idempotent migration that adds the tag to
entries the old substring list would have hidden. It defaults to a dry run; applying it to a real
memory home is a separate, explicit step (backlog 2026-10-14-e2e-memory-tag-retag-apply).
"""

from __future__ import annotations

import os
import re
from contextlib import nullcontext
from pathlib import Path

from src.agent.frontmatter import parse_frontmatter
from src.memory.persistent import memory_lock

E2E_TAG = "e2e-test"

#: The retired substring list; kept ONLY so the migration can find legacy entries.
LEGACY_E2E_MARKERS = (
    "handoff cycle",
    "cached context",
    "synthetic alert",
    "idempotent reads",
    "verification reads",
    "e2e phase",
    "integration test",
    "commit from cached",
)

_FM_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_KEYWORDS_LINE_RE = re.compile(r"^keywords:.*$", re.MULTILINE)


def _is_legacy_e2e(meta: dict, body: str, stem: str) -> bool:
    blob = f"{meta.get('name', stem)} {meta.get('description', '')} {body}".lower()
    return any(marker in blob for marker in LEGACY_E2E_MARKERS)


def retag_legacy_e2e_entries(memory_dir: Path, *, apply: bool = False) -> list[Path]:
    """Add :data:`E2E_TAG` to every legacy e2e entry under ``memory_dir``.

    Idempotent: an entry that already carries the tag is skipped. Only the ``keywords:``
    frontmatter line changes (the tag goes first so the 5-keyword cap never drops it); the body
    and every other field are left byte-identical.

    Args:
        memory_dir: The memory home to scan (recursive over ``*.md``, ``MEMORY.md`` excluded).
        apply: ``False`` (default) only reports; ``True`` rewrites the files.

    Returns:
        The entries that were (or, on a dry run, would be) retagged.
    """
    changed: list[Path] = []
    # A dry run must not touch the home at all (memory_lock creates a .lock file).
    with (memory_lock(memory_dir) if apply else nullcontext(True)) as acquired:
        if not acquired:
            raise RuntimeError(f"memory lock timeout for {memory_dir}; retag not applied")
        for path in sorted(memory_dir.rglob("*.md")):
            if path.name == "MEMORY.md":
                continue
            text = path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(text)
            fm = _FM_RE.match(text)
            if fm is None or not meta:
                continue  # no frontmatter: not a memory entry the loader reads
            keywords = meta.get("keywords", [])
            keywords = keywords if isinstance(keywords, list) else []
            if E2E_TAG in keywords or not _is_legacy_e2e(meta, body, path.stem):
                continue
            changed.append(path)
            if not apply:
                continue
            line = f"keywords: [{', '.join([E2E_TAG, *keywords])}]"
            head = fm.group(1)
            head = _KEYWORDS_LINE_RE.sub(lambda _m: line, head, count=1) if "keywords:" in head else f"{head}\n{line}"
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text[: fm.start(1)] + head + text[fm.end(1):], encoding="utf-8")
            os.replace(tmp, path)
    return changed
