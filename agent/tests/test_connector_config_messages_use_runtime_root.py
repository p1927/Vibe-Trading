"""India broker connector config errors name this tier's runtime root, never a literal ~/.vibe-trading.

On release the real root is ~/.vibe-trading-release, so a message that names the dev path sent the
operator to edit the wrong file.
"""

from __future__ import annotations

import sys
import types

import pytest

from src.trading.connectors.dhan import sdk as dhan_sdk
from src.trading.connectors.shoonya import sdk as shoonya_sdk


@pytest.fixture(autouse=True)
def _no_real_dhan_sdk(monkeypatch):
    """The Dhan SDK is an optional dependency; the config check runs before any SDK call."""
    monkeypatch.setitem(sys.modules, "dhanhq", types.SimpleNamespace(dhanhq=lambda *a, **k: object()))


@pytest.fixture
def runtime_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_HOME", str(tmp_path))
    return tmp_path


def test_dhan_missing_config_message_names_the_runtime_root(runtime_root, monkeypatch) -> None:
    with pytest.raises(dhan_sdk.DhanConfigError) as exc:
        dhan_sdk._dhan_client(dhan_sdk.DhanConfig())
    assert str(dhan_sdk.config_path()) in str(exc.value)
    assert str(runtime_root) in str(exc.value)
    assert "~/.vibe-trading" not in str(exc.value)


def test_shoonya_missing_config_message_names_the_runtime_root(runtime_root) -> None:
    with pytest.raises(shoonya_sdk.ShoonyaConfigError) as exc:
        shoonya_sdk._login(shoonya_sdk.ShoonyaConfig())
    assert str(shoonya_sdk.config_path()) in str(exc.value)
    assert str(runtime_root) in str(exc.value)
    assert "~/.vibe-trading" not in str(exc.value)


def test_connector_messages_follow_a_changed_runtime_root(tmp_path, monkeypatch) -> None:
    """Two tiers, two roots: the message is computed per call, not frozen at import."""
    messages = []
    for name in ("dev", "release"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setenv("VIBE_TRADING_HOME", str(root))
        with pytest.raises(dhan_sdk.DhanConfigError) as exc:
            dhan_sdk._dhan_client(dhan_sdk.DhanConfig())
        messages.append(str(exc.value))
    assert str(tmp_path / "dev") in messages[0] and str(tmp_path / "release") not in messages[0]
    assert str(tmp_path / "release") in messages[1]
