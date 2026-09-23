"""Shared fixtures and sys.path setup for all tests."""

from __future__ import annotations

import atexit
import os
import shutil
import site
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure agent/ is on sys.path so imports like `backtest.*` and `src.*` work.
AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

# --------------------------------------------------------------------------- #
# Sandbox the config runtime root BEFORE any test module is imported (#1116).
# --------------------------------------------------------------------------- #
# The suite must never resolve its config root against the real ~/.vibe-trading
# (live mandate + audit-ledger state). Modules in BOTH categories must resolve
# to a temp sandbox:
#
#   * Import-time-baked constants (bound when the module is first imported,
#     i.e. during collection): loop.RUNS_DIR/SESSIONS_DIR, goal/session
#     _DB_PATH, helpers.ENV_PATH, memory MEMORY_BASE, skills USER_SKILLS_DIR,
#     strategy_store _DEFAULT_DB_PATH, swarm presets USER_PRESETS_DIR,
#     qveris QVERIS_CONFIG_PATH.
#   * Runtime Path.home() call sites: redaction's internal-root anchors
#     (_internal_roots_for_cwd), alpha_bench report output (_default_output_dir),
#     autopilot run dirs, uploads shadow_reports.
#
# Because the import-time constants are baked during test-module collection, the
# sandbox cannot be a pytest fixture (fixtures run AFTER collection); it has to
# be installed at conftest import time, before pytest imports any test module.
#
# The sandbox owns exactly ONE knob: the home directory. ``get_runtime_root()``
# consults ``VIBE_TRADING_HOME`` first and only falls back to
# ``Path.home()/".vibe-trading"`` (src/config/paths.py:27-34), so the override is
# DELETED rather than pointed at the sandbox. Two reasons, both load-bearing:
#
#   * A developer with ``VIBE_TRADING_HOME`` exported in their shell would
#     otherwise keep resolving to it — the leak this sandbox exists to close.
#   * Setting it would outrank every per-test ``monkeypatch.setenv("HOME")`` /
#     ``monkeypatch.setattr(Path, "home", ...)``, silently collapsing per-test
#     roots into one shared directory. That is not hypothetical: it made
#     test_shadow_account's "returns latest" read a profile another test in the
#     same file had saved.
#
# With the override gone, ``Path.home()`` is the single point of control, so a
# test redirecting home gets its own root on every platform and needs no
# knowledge of this sandbox. On Windows ``Path.home()`` ignores $HOME and reads
# %USERPROFILE%, so both spellings are set.
_PRIOR_SANDBOX_ENV = {
    key: os.environ.get(key)
    for key in ("VIBE_TRADING_HOME", "HOME", "USERPROFILE", "PYTHONPATH")
}

# Outcome guard, armed BEFORE the redirect so it reuses the app's own resolution
# rather than restating it. The assertions in _sandbox_runtime_root check that
# the redirect is INSTALLED, which is a proxy; this checks what #1116 is actually
# about — that nothing reached the user's own state — so a path the redirect does
# not cover fails the run with the artefact named, instead of quietly appending to
# a live audit ledger for another release. The ledgers are the headline harm
# (fabricated order_rejected records in an append-only, tamper-evident chain) and
# they move only on a real live action, so they cannot produce noise.
from src.config.paths import get_runtime_root  # noqa: E402 — needs AGENT_DIR above

_REAL_LEDGERS = tuple(
    get_runtime_root() / "live" / name for name in ("audit.jsonl", "audit_chain.jsonl")
)
# The sandbox root is RESOLVED. On macOS ``tempfile.mkdtemp()`` returns a path
# under ``/var/folders/...``, which is a symlink to ``/private/var/folders/...``.
# Handing the unresolved form to HOME breaks every guard that compares a
# ``Path.resolve()``d path against a ``Path.home()``-derived prefix: the two
# spellings of the same directory do not compare equal, the guard reads it as an
# escape attempt and refuses. Resolving here keeps one spelling everywhere.
_SANDBOX_HOME = Path(tempfile.mkdtemp(prefix="vibe-trading-test-home-")).resolve()
os.environ.pop("VIBE_TRADING_HOME", None)
os.environ["HOME"] = str(_SANDBOX_HOME)
os.environ["USERPROFILE"] = str(_SANDBOX_HOME)
# Trade's `get_hub_dir()` refuses to guess a hub and, under pytest, any hub outside the system temp
# dir; some Trade modules resolve it at import. Declare a scratch hub before collection (overriding
# a `.env`-loaded real one), so no vibetrading test can reach a real hub.
os.environ["TRADE_STACK_HUB_DIR"] = tempfile.mkdtemp(prefix="trade-pytest-hub-")
# Trade's executor-gateway call log (`executor_gateway.config.EXECUTOR_GATEWAY_DB_PATH`, read at
# import) defaults to the Trade root's real `log/executor_gateway.db`: an adapter call a test
# reaches must not add rows to the live spend rollup (nor, as before that path was root-anchored,
# create `log/` inside this checkout). Must be set before `import trade_integrations` below.
os.environ["TRADE_LOG_DIR"] = tempfile.mkdtemp(prefix="trade-pytest-log-")
(_SANDBOX_HOME / ".vibe-trading").mkdir(parents=True, exist_ok=True)

# Same leak, same mechanism, for MLflow and the rest of Trade's per-tier state: both tiers' .env
# set MLFLOW_TRACKING_URI, and `import trade_integrations` `setdefault`s that `.env` into the
# process. Deleting the value afterwards (what this block used to do) did not hold: other Trade
# code runs `load_trade_env()` again mid-session (recorder/ind_client.py at import,
# `ensure_openalgo_env()`, ...), and that put dev's real `~/.vibe-trading/mlflow.db` back. Trade's
# `tier_state_guard` SETS every tier-state variable to a session sandbox instead, so a later
# `setdefault` cannot replace it. It also arms an audit hook that blocks and records any access to
# a real tier's MLflow store, hub, rate-limit dir, observability feed or executor_gateway.db,
# checked in `pytest_sessionfinish` below. `import trade_integrations` still comes first, so its
# one-time `register.apply()` runs before the sandbox values are written.
import trade_integrations  # noqa: E402,F401 -- see ordering note above
from trade_integrations import tier_state_guard as _tier_state_guard  # noqa: E402

_TIER_SANDBOX = Path(tempfile.mkdtemp(prefix="trade-pytest-"))
_tier_state_guard.install(AGENT_DIR.parents[1], _TIER_SANDBOX)

# Same leak for Trade's observability feed: events/issues default to the Trade checkout's
# `log/observability/`, the running dev stack's own feed in the main checkout, so a test's error
# event opened a real issue there. Point it at scratch, as Trade's own tests/conftest.py does.
for _obs_var in ("TRADE_OBSERVABILITY_EVENTS_PATH", "TRADE_OBSERVABILITY_ISSUES_PATH"):
    os.environ.pop(_obs_var, None)
os.environ["TRADE_OBSERVABILITY_DIR"] = tempfile.mkdtemp(prefix="trade-pytest-observability-")
# And for Trade's cross-process rate-limit paces (`rate_limit._cross_process_dir`), which are one
# machine-wide queue shared with the running dev/release tiers: a test must never claim a slot in
# their MiniMax account pace.
os.environ["TRADE_RATE_LIMIT_DIR"] = tempfile.mkdtemp(prefix="trade-pytest-rate-limit-")

# A hermetic LLM configuration, for the same reason. Tests that enter the app's lifespan
# (``with TestClient(app)``) run the real boot gate, which refuses to start without a configured
# LLM provider (a critical check). Only a shell or checkout whose `.env` configured one (loaded
# by the import above) could boot, so the suite passed in the main checkout and failed in a
# worktree. The key is fake and the base URL unroutable: the boot gate checks configuration
# only (no network), and any call a test reaches fails fast instead of spending a real account.
# Tests about provider selection set their own values with monkeypatch.
_PRIOR_SANDBOX_ENV.update(
    {key: os.environ.get(key) for key in ("LANGCHAIN_PROVIDER", "LANGCHAIN_MODEL_NAME", "OPENAI_API_KEY", "OPENAI_BASE_URL")}
)
os.environ.update(
    {
        "LANGCHAIN_PROVIDER": "openai",
        "LANGCHAIN_MODEL_NAME": "pytest-sandbox-model",
        "OPENAI_API_KEY": "sk-pytest-sandbox",
        "OPENAI_BASE_URL": "http://127.0.0.1:9/v1",
    }
)

# A developer shell that exports MARKET_DATA_ORDER_* would silently reorder
# the default fallback chains (registry.refresh_source_order_overrides reads
# them at import time), breaking every default-order assertion in the suite.
# Scrub them for the session; override tests set them explicitly and restore
# defaults themselves. Must run before any test module imports the registry.
_PRIOR_SOURCE_ORDER_ENV = {
    key: os.environ.pop(key)
    for key in [k for k in list(os.environ) if k.startswith("MARKET_DATA_ORDER_")]
}

# Tests that spawn a subprocess hand it this environment, HOME included. On a
# machine whose dependencies live in the per-user site directory -- what
# ``pip install --user`` does, and the default when no virtualenv is active --
# that directory is derived FROM HOME, so redirecting HOME hides numpy, pandas
# and pytest itself from every child. The child then comes up with a partial
# tool registry rather than an error (tool auto-discovery logs an import failure
# and moves on), which surfaces as a baffling "Tool 'x' not found" far from the
# cause. ``site.USER_SITE`` was computed at interpreter startup, before this
# redirect, so it still names the real directory: pin it for children.
if site.ENABLE_USER_SITE and site.USER_SITE and Path(site.USER_SITE).is_dir():
    _prior_path = os.environ.get("PYTHONPATH", "")
    if site.USER_SITE not in _prior_path.split(os.pathsep):
        os.environ["PYTHONPATH"] = os.pathsep.join(
            part for part in (site.USER_SITE, _prior_path) if part
        )


def _ledger_state() -> dict[str, tuple[int, int] | None]:
    """Return (size, mtime_ns) per real live ledger; None where absent."""
    state: dict[str, tuple[int, int] | None] = {}
    for path in _REAL_LEDGERS:
        try:
            stat = path.stat()
        except OSError:
            state[str(path)] = None
        else:
            state[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return state


_REAL_LEDGER_BASELINE = _ledger_state()


def _assert_real_root_untouched() -> None:
    """Raise if the user's own live ledgers moved during the run."""
    if changed := [p for p, now in _ledger_state().items() if _REAL_LEDGER_BASELINE[p] != now]:
        raise AssertionError(
            f"The suite wrote into the REAL config root (#1116): {changed}. "
            f"Something resolved outside the sandbox at {_SANDBOX_HOME}. Route "
            "the write through get_runtime_root(); do not widen this guard."
        )


def _teardown_sandbox() -> None:
    shutil.rmtree(_SANDBOX_HOME, ignore_errors=True)
    shutil.rmtree(_TIER_SANDBOX, ignore_errors=True)
    for key, prior in _PRIOR_SANDBOX_ENV.items():
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior
    os.environ.update(_PRIOR_SOURCE_ORDER_ENV)


# Safety net for e.g. `--collect-only` (no fixtures run) and abnormal exits.
atexit.register(_teardown_sandbox)


#: Where a cwd-relative default (MLflow's `./mlruns`, a `log/` dir) lands when a test runs from
#: agent/. Both are Trade-owned state that belongs in a temp store under tests.
_CHECKOUT_LEAK_DIRS = (AGENT_DIR / "mlruns", AGENT_DIR / "log")


def _checkout_leak_state() -> set[str]:
    return {str(p) for d in _CHECKOUT_LEAK_DIRS if d.exists() for p in [d, *d.rglob("*")]}


_CHECKOUT_LEAK_BASELINE = _checkout_leak_state()


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook
    """Fail the run if anything escaped the sandbox into the user's own state or the checkout."""
    _assert_real_root_untouched()
    if blocked := _tier_state_guard.violations():
        raise AssertionError(
            "The suite touched real Trade tier state (blocked by tier_state_guard): "
            + "; ".join(blocked)
        )
    if leaked := sorted(_checkout_leak_state() - _CHECKOUT_LEAK_BASELINE):
        raise AssertionError(
            f"The suite wrote into the checkout: {leaked[:5]} ({len(leaked)} paths). A test "
            "reached a cwd-relative store; point it at a temp dir here instead of ignoring it."
        )


@pytest.fixture(autouse=True, scope="session")
def _sandbox_runtime_root():
    """Guard the import-time sandbox and tear it down at session end.

    Home is redirected at conftest import time above — before collection, so
    constants baked at module import resolve there too. This fixture only asserts
    the invariant is still active and removes the temp dir at session end. The
    function-scoped ``_reset_env_config`` fixture snapshots the environment each
    test, so the sandbox survives every test while per-test monkeypatches keep
    working.
    """
    assert os.environ["HOME"] == str(_SANDBOX_HOME)
    assert os.environ["USERPROFILE"] == str(_SANDBOX_HOME)
    yield _SANDBOX_HOME / ".vibe-trading"
    _teardown_sandbox()


@pytest.fixture(autouse=True)
def _reset_env_config():
    """Isolate each test from the process environment and the config cache.

    Two things leak between tests otherwise, and the second one bit us:

    1. The cached ``EnvConfig`` singleton, so ``monkeypatch.setenv`` would have
       no effect on anything already holding the cached instance.
    2. ``os.environ`` itself. The settings write path deliberately applies a
       written ``.env`` to the running process, which is correct in production
       (settings take effect without a restart) but means a settings TEST
       writing ``TUSHARE_TOKEN=ts-secret-token`` into a temp file leaks that
       value into the process for every test that follows. That is exactly how
       four live-data tests came to fail with "token is wrong" while passing in
       isolation -- and monkeypatch cannot undo it, because the test never went
       through monkeypatch to set it.

    Snapshotting and restoring the whole environment closes the class of bug
    rather than the one instance of it.
    """
    from src.config.accessor import reset_env_config
    from src.config.bootstrap import reset_bootstrap

    saved_environ = dict(os.environ)
    reset_env_config()
    reset_bootstrap()
    yield
    os.environ.clear()
    os.environ.update(saved_environ)
    reset_env_config()
    reset_bootstrap()


@pytest.fixture(autouse=True)
def _reset_run_log_buffer():
    """Every test starts with an empty scheduled-job log buffer.

    ``run_log_buffer`` is module-level state keyed by job id, and many tests dispatch a job
    called ``job-1``: one that logged a stage line left it in the buffer, and the next test
    reading ``job-1``'s log saw it (``test_run_log_buffer`` failed after the options and
    calibration stage-sink tests)."""
    from src.scheduled_research import run_log_buffer

    run_log_buffer._BUFFERS.clear()
    run_log_buffer._SEQ_COUNTERS.clear()
    yield
