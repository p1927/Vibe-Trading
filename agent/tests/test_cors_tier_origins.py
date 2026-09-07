"""Both tiers' UI origins must be CORS-allowed.

The frontend resolves an absolute API origin per UI port (`apiBase.ts`, mirroring
`stack/ports.yaml`), so a release-tier UI call to the release API is genuinely cross-origin.
When the release pair was missing from the allowlist, every release board call failed with an
opaque browser CORS error that the server never logs — the UI simply rendered "Failed to load
agents" with nothing to trace.
"""

from __future__ import annotations

import pytest

from src.api.security import _DEFAULT_CORS_ORIGINS


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:5899",  # dev UI      -> dev API 8899
        "http://127.0.0.1:5909",  # release UI  -> release API 8909
        "http://localhost:5899",
        "http://localhost:5909",
    ],
)
def test_both_tier_ui_origins_are_allowed(origin: str) -> None:
    assert origin in _DEFAULT_CORS_ORIGINS


def test_dev_and_release_ui_ports_are_both_present() -> None:
    """Guards the asymmetry directly: dev was listed, release was not."""
    for port in ("5899", "5909"):
        assert any(o.endswith(f":{port}") for o in _DEFAULT_CORS_ORIGINS), (
            f"UI port {port} has no CORS origin — that tier's UI cannot reach its API"
        )


def test_wildcard_is_still_rejected() -> None:
    from src.api.security import _parse_cors_origins

    with pytest.raises(RuntimeError):
        _parse_cors_origins("*")
