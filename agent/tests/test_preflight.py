"""Tests for startup preflight checks."""

from __future__ import annotations

import sys
from pathlib import Path

import requests
import trade_integrations.http as trade_http

from src import preflight

_AGENT_DIR = Path(__file__).resolve().parents[1]


def _active_env_example_lines() -> list[str]:
    """Return the non-comment, non-blank lines of the real agent/.env.example."""
    text = (_AGENT_DIR / ".env.example").read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_env_example_default_provider_is_not_openrouter() -> None:
    """The template must not ship with OpenRouter as an active LLM provider block.

    Regression test for
    .claude/backlog/archive/items/2026-09-03-vibe-agent-openrouter-default-config-drift.md:
    `agent/.env.example` used to have OpenRouter as its only uncommented provider block
    (with a fake `sk-or-...here` key), so any freshly-created `agent/.env` (copied verbatim
    by `integrations/trade_integrations/stack_env_sync.py` when none exists yet) silently
    activated OpenRouter as the running default. Per the recorded maintainer decision,
    OpenRouter must stay available (as one of the commented-out blocks) but never be the
    *active* template default — the template currently ships with every provider block
    commented out, forcing an explicit, deliberate choice.
    """
    active_lines = _active_env_example_lines()
    active_providers = [
        line.split("=", 1)[1].strip().lower()
        for line in active_lines
        if line.startswith("LANGCHAIN_PROVIDER=")
    ]
    assert "openrouter" not in active_providers


def test_env_example_active_credentials_are_not_placeholders() -> None:
    """No uncommented `*_API_KEY=`/`*_TOKEN=` line in the template may be a fake value.

    Companion to the OpenRouter-specific check above: guards the general case (any
    provider, not just OpenRouter) from becoming an active default while still carrying
    an unfilled template placeholder value.
    """
    active_lines = _active_env_example_lines()
    for line in active_lines:
        if "_API_KEY=" in line or "_TOKEN=" in line:
            _, _, value = line.partition("=")
            assert not preflight._is_placeholder_credential(value), line


def _configure_llm_preflight(monkeypatch) -> None:
    """Install a minimal OpenAI-compatible provider environment for preflight tests."""
    import src.providers.llm as llm

    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "gpt-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr(llm, "_ensure_dotenv", lambda: None)
    monkeypatch.setattr(llm, "_sync_provider_env", lambda: None)
    monkeypatch.setattr(
        llm,
        "provider_diagnostics",
        lambda: {
            "base_url": "https://example.test/v1",
            "timeout_seconds": 120,
            "max_retries": 2,
            "proxy": {},
        },
    )


def test_llm_preflight_probe_does_not_follow_redirects(monkeypatch) -> None:
    """A redirect response still proves the HTTPS provider base is reachable."""
    _configure_llm_preflight(monkeypatch)
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 307
        return response

    monkeypatch.setattr(trade_http, "get", fake_get)

    result = preflight._check_llm_provider()

    assert result.status == "ready"
    assert calls == [
        (
            "https://example.test",
            {
                "timeout": 10,
                "allow_redirects": False,
            },
        )
    ]


def test_llm_preflight_rejects_placeholder_credential_without_network_call(
    monkeypatch,
) -> None:
    """A never-configured template credential fails loudly, before any network I/O.

    Regression test for the class of bug in
    .claude/backlog/archive/items/2026-09-03-vibe-agent-openrouter-default-config-drift.md:
    a stale `agent/.env.example` placeholder key (`OPENROUTER_API_KEY=sk-or-...here`)
    silently became the active runtime credential and only surfaced as a slow
    TLS-timeout stall against the real provider endpoint. This must instead fail
    immediately, with a clear message, and never reach the network probe.
    """
    _configure_llm_preflight(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-or-...here")

    def fake_get(url: str, **kwargs: object) -> object:
        del url, kwargs
        raise AssertionError("must not probe the network with a placeholder credential")

    monkeypatch.setattr(trade_http, "get", fake_get)

    result = preflight._check_llm_provider()

    assert result.status == "error"
    assert result.critical is True
    assert "placeholder" in result.message


def test_is_placeholder_credential_matches_env_example_shapes() -> None:
    """Covers every placeholder shape actually used in agent/.env.example."""
    placeholders = [
        "sk-or-...here",
        "xxx",
        "sk-xxx",
        "sk-ant-xxx",
        "gho_xxx",
        "gsk_xxx",
        "nvapi-xxx",
        "sk-kimi-xxx",
        "your-tushare-token",
        "your-access-token",
        "your_public_api_key",
    ]
    for value in placeholders:
        assert preflight._is_placeholder_credential(value), value

    real_looking = ["", "sk-proj-abc123realkey", "ollama", "MZ1x9pQvR7kLh2"]
    for value in real_looking:
        assert not preflight._is_placeholder_credential(value), value


def test_llm_preflight_probe_reports_request_errors(monkeypatch) -> None:
    """Request failures remain critical errors for the LLM provider check."""
    _configure_llm_preflight(monkeypatch)

    def fake_get(url: str, **kwargs: object) -> object:
        del url, kwargs
        raise requests.Timeout("timed out")

    monkeypatch.setattr(trade_http, "get", fake_get)

    result = preflight._check_llm_provider()

    assert result.status == "error"
    assert result.critical is True
    assert "Timeout: timed out" in result.message


def test_copilot_preflight_uses_sdk_auth_instead_of_openai_base_url(
    monkeypatch,
) -> None:
    """Copilot has no OpenAI base URL because the official SDK owns transport."""
    import src.providers.copilot_auth as copilot_auth
    import src.providers.llm as llm

    monkeypatch.setenv("LANGCHAIN_PROVIDER", "copilot")
    monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "claude-sonnet-5")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr(llm, "_ensure_dotenv", lambda: None)
    monkeypatch.setattr(llm, "_sync_provider_env", lambda: None)
    monkeypatch.setattr(
        llm,
        "provider_diagnostics",
        lambda: {
            "base_url": "https://api.githubcopilot.com",
            "timeout_seconds": 120,
            "max_retries": 2,
            "proxy": {},
        },
    )
    monkeypatch.setattr(
        copilot_auth,
        "get_copilot_auth_status",
        lambda: (True, "sykuang (via gh)"),
        raising=False,
    )

    result = preflight._check_llm_provider()

    assert result.status == "ready"
    assert result.critical is False
    assert "sykuang (via gh)" in result.message


def test_akshare_check_uses_spec_without_import(monkeypatch) -> None:
    """AKShare's package import is heavy; preflight should only check discovery."""
    monkeypatch.delitem(sys.modules, "akshare", raising=False)
    monkeypatch.setattr(preflight, "find_spec", lambda name: object() if name == "akshare" else None)

    result = preflight._check_akshare()

    assert result.status == "ready"
    assert result.message == "installed"
    assert "akshare" not in sys.modules


def test_akshare_check_skips_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(preflight, "find_spec", lambda name: None)

    result = preflight._check_akshare()

    assert result.status == "skipped"
    assert result.message == "package not installed"


def test_content_filter_threshold_check(monkeypatch) -> None:
    """Content Filter Threshold row must appear in preflight output."""
    monkeypatch.setenv("CONTENT_FILTER_WARNING_THRESHOLD", "0.10")

    result = preflight._check_content_filter_threshold()

    assert result.name == "Content Filter Threshold"
    assert result.status == "ready"
    assert "10%" in result.message
    assert "CONTENT_FILTER_WARNING_THRESHOLD" in result.message


def test_content_filter_threshold_default(monkeypatch) -> None:
    """Default threshold is 5% when env var is unset."""
    monkeypatch.delenv("CONTENT_FILTER_WARNING_THRESHOLD", raising=False)

    result = preflight._check_content_filter_threshold()

    assert result.name == "Content Filter Threshold"
    assert result.status == "ready"
    assert "5%" in result.message
