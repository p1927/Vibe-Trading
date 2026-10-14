"""The Prediction page's hand-copied macro factor lists match the backend catalog.

`frontend/src/lib/predictionVerification.ts` mirrors `factors.ridge_keys.MACRO_FACTOR_KEYS` as
`MACRO_CORE_KEYS` + `MACRO_EXTENDED_KEYS`. A key added or renamed on the backend without the list
following would make the page's verification panel judge the wrong set (silently). This pins them equal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("trade_integrations")

_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "predictionVerification.ts"


def _ts_keys(text: str, name: str) -> list[str]:
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.S)
    assert match, f"{name} not found in {_TS}"
    return re.findall(r'"([a-z0-9_]+)"', match.group(1))


@pytest.mark.skipif(not _TS.is_file(), reason="frontend source not present (standalone vibe-trading)")
def test_frontend_macro_key_lists_equal_the_backend_catalog() -> None:
    from trade_integrations.factors.ridge_keys import MACRO_FACTOR_KEYS

    text = re.sub(r"//[^\n]*", "", _TS.read_text(encoding="utf-8"))
    core, extended = _ts_keys(text, "MACRO_CORE_KEYS"), _ts_keys(text, "MACRO_EXTENDED_KEYS")

    assert len(core) == len(set(core)) and len(extended) == len(set(extended))  # no copy-paste duplicates
    assert not set(core) & set(extended)  # the two lists partition the catalog
    frontend = set(core) | set(extended)
    backend = set(MACRO_FACTOR_KEYS)
    assert not backend - frontend, f"backend keys missing from the page: {sorted(backend - frontend)}"
    assert not frontend - backend, f"page keys the backend no longer has: {sorted(frontend - backend)}"
