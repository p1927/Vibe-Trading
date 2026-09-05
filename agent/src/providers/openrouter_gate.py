"""Fork-only guard: Trade never uses OpenRouter as an LLM provider.

Sidecar module per docs/FORK_CONVENTIONS.md — kept separate from
``src/providers/llm.py`` (an upstream-shared, general-purpose provider file)
rather than spliced inline, so upstream syncs never conflict with this
Trade-specific policy.

Policy (2026-09-05, explicit user directive): Trade never uses OpenRouter as
an LLM provider, anywhere, in any project or submodule — all LLM calls should
route through Trade's own LLM adapter
(``integrations/trade_integrations/dataflows/model_adapters``) instead. This
supersedes the earlier "OpenRouter stays available as an optional, non-default
provider" decision recorded in
.claude/backlog/archive/items/2026-09-04-openrouter-broader-audit-and-env-precedence.md.
See .claude/backlog/items/2026-09-05-openrouter-ban-llm-adapter-audit.md.

Deliberately does not touch ``PROVIDER_CAPABILITIES["openrouter"]`` in
``src/providers/capabilities.py`` — that table is a declarative capability
description, not something that executes a network call by itself. This gate
is called at the one or two real chokepoints that do (``build_llm`` and
preflight's ``_check_llm_provider``), so blocking there is sufficient without
touching upstream-shaped capability data.
"""

from __future__ import annotations

_BANNED_PROVIDERS = frozenset({"openrouter"})


def assert_not_openrouter(provider: str | None) -> None:
    """Raise ``RuntimeError`` if ``provider`` resolves to OpenRouter.

    Call this at any point that is about to construct a live LLM client or
    otherwise treat ``provider`` as usable, before that construction happens.
    """
    normalized = (provider or "").strip().lower()
    if normalized in _BANNED_PROVIDERS:
        raise RuntimeError(
            "OpenRouter is disabled as an LLM provider in this repo (Trade "
            "policy, 2026-09-05) — set LANGCHAIN_PROVIDER to a supported "
            "provider (e.g. minimax, nvidia, anthropic) instead of "
            "'openrouter'. See "
            ".claude/backlog/items/2026-09-05-openrouter-ban-llm-adapter-audit.md."
        )
