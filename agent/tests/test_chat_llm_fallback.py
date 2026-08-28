"""ChatLLM.build_fallback() — cross-provider fallback resolved from the shared

trade_integrations adapter catalog (integrations/trade_integrations/dataflows/
model_adapters/catalog.yaml), the same source of truth generative.py's batch/
ingestion runtime fallback already uses.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.providers.chat import ChatLLM, LLMRuntimeSnapshot


def _client(provider: str, model: str = "MiniMax-M3") -> ChatLLM:
    client = ChatLLM.__new__(ChatLLM)
    client.model_name = model
    client.runtime_snapshot = LLMRuntimeSnapshot(
        provider=provider, configured_model=model, reasoning_effort=""
    )
    return client


class _Spec:
    def __init__(self, provider: str, model: str, enabled: bool = True, fallback_adapter_id: str | None = None):
        self.provider = provider
        self.model = model
        self.enabled = enabled
        self.fallback_adapter_id = fallback_adapter_id


def _patch_registry(monkeypatch, *, adapters, by_id, provider_configured):
    registry_mod = pytest.importorskip(
        "trade_integrations.dataflows.model_adapters.registry"
    )
    generative_mod = pytest.importorskip(
        "trade_integrations.dataflows.model_adapters.generative"
    )
    monkeypatch.setattr(registry_mod, "list_adapters", lambda kind=None: adapters)
    monkeypatch.setattr(registry_mod, "get_adapter_by_id", lambda adapter_id: by_id.get(adapter_id))
    monkeypatch.setattr(generative_mod, "_PROVIDER_CONFIGURED", provider_configured)


def test_build_fallback_resolves_configured_cross_provider_adapter(monkeypatch) -> None:
    """minimax's catalog entry names nvidia as fallback → a ready ChatLLM for it."""
    minimax_spec = _Spec("minimax", "MiniMax-M3", fallback_adapter_id="nvidia-generative")
    nvidia_spec = _Spec("nvidia", "nvidia/nemotron-3-nano-30b-a3b")
    _patch_registry(
        monkeypatch,
        adapters=[minimax_spec],
        by_id={"nvidia-generative": nvidia_spec},
        provider_configured={"nvidia": lambda: True},
    )
    built: dict[str, Any] = {}

    def _fake_init(self, model_name=None, *, provider=None):
        built["model_name"] = model_name
        built["provider"] = provider
        self.model_name = model_name
        self.runtime_snapshot = LLMRuntimeSnapshot(
            provider=provider, configured_model=model_name, reasoning_effort=""
        )

    monkeypatch.setattr(ChatLLM, "__init__", _fake_init)

    fallback = _client("minimax").build_fallback()

    assert fallback is not None
    assert built == {"model_name": "nvidia/nemotron-3-nano-30b-a3b", "provider": "nvidia"}
    assert fallback.runtime_snapshot.provider == "nvidia"


def test_build_fallback_none_when_no_fallback_configured_on_catalog_entry(monkeypatch) -> None:
    minimax_spec = _Spec("minimax", "MiniMax-M3", fallback_adapter_id=None)
    _patch_registry(
        monkeypatch, adapters=[minimax_spec], by_id={}, provider_configured={}
    )

    assert _client("minimax").build_fallback() is None


def test_build_fallback_none_when_fallback_provider_uncredentialed(monkeypatch) -> None:
    minimax_spec = _Spec("minimax", "MiniMax-M3", fallback_adapter_id="nvidia-generative")
    nvidia_spec = _Spec("nvidia", "nvidia/nemotron-3-nano-30b-a3b")
    _patch_registry(
        monkeypatch,
        adapters=[minimax_spec],
        by_id={"nvidia-generative": nvidia_spec},
        provider_configured={"nvidia": lambda: False},
    )

    assert _client("minimax").build_fallback() is None


def test_build_fallback_none_when_no_matching_primary_adapter(monkeypatch) -> None:
    """Primary provider isn't represented in the generative catalog at all."""
    _patch_registry(monkeypatch, adapters=[], by_id={}, provider_configured={})

    assert _client("some-unlisted-provider").build_fallback() is None


def test_build_fallback_none_when_fallback_construction_raises(monkeypatch) -> None:
    minimax_spec = _Spec("minimax", "MiniMax-M3", fallback_adapter_id="nvidia-generative")
    nvidia_spec = _Spec("nvidia", "nvidia/nemotron-3-nano-30b-a3b")
    _patch_registry(
        monkeypatch,
        adapters=[minimax_spec],
        by_id={"nvidia-generative": nvidia_spec},
        provider_configured={"nvidia": lambda: True},
    )

    def _raise_init(self, model_name=None, *, provider=None):
        raise RuntimeError("NVIDIA_API_KEY is not set")

    monkeypatch.setattr(ChatLLM, "__init__", _raise_init)

    assert _client("minimax").build_fallback() is None
