"""Startup guard: a release-profile vibe-api whose roots point at the wrong tier refuses to boot.

Sidecar (docs/FORK_CONVENTIONS.md) over Trade's `stack_env_sync.assert_tier_roots`, called from
`src.api.lifecycle._run_startup_preflight`. vibe-api is the service that actually mis-rooted on
2026-09-07 (wrote into dev's mirror while every health surface stayed green). No-op outside
`STACK_PROFILE=release`; under release, a missing `trade_integrations` raises rather than skips,
because a guard that silently disappears is the failure it exists to prevent.
"""

from __future__ import annotations

import os

from src.trade.hub_bridge import ensure_trade_stack_path


def assert_release_tier_roots() -> None:
    if os.environ.get("STACK_PROFILE", "dev").strip().lower() != "release":
        return
    ensure_trade_stack_path()
    from trade_integrations.stack_env_sync import assert_tier_roots

    # module_file is this file: it must sit inside TRADE_STACK_ROOT (the release worktree).
    assert_tier_roots(module_file=__file__)
