"""Fork-only preflight checks, run alongside upstream's own checks in preflight.py.

Extracted per docs/FORK_CONVENTIONS.md — each check function here is
self-contained (only needs ``CheckResult``, imported back from
``preflight.py``) and independent of upstream's own check implementations in
that file. ``preflight.py`` lazily imports these inside ``run_preflight()`` to
avoid a module-load-time import cycle.
"""

from __future__ import annotations

from src.preflight import CheckResult


def check_environment() -> CheckResult:
    """Verify env bootstrap completed and scheduler flags are readable."""
    from src.config.accessor import get_env_config
    from src.config.bootstrap import bootstrap_environment

    report = bootstrap_environment()
    cfg = get_env_config()
    master = cfg.agent_tuning.vibe_trading_enable_scheduler
    index_on = cfg.agent_tuning.index_research_enable_scheduler
    monitor_on = cfg.agent_tuning.index_monitor_enable_scheduler

    layers = ", ".join(report.layers_loaded) if report.layers_loaded else (
        "cached" if report.already_bootstrapped else "defaults only"
    )

    flags = (
        f"master={'on' if master else 'off'} "
        f"index={'on' if index_on else 'off'} "
        f"monitor={'on' if monitor_on else 'off'}"
    )
    executor_hint = (
        "executor starts paused; resume via POST /scheduled-runs/scheduler/resume"
        if master
        else "executor skipped (master off)"
    )

    status = "ready"
    if not report.layers_loaded and not master and not index_on and not monitor_on:
        # One of CheckResult's four statuses: print_preflight has no display for any other, and
        # an unknown one (this used to say "warning") raised KeyError and failed API startup.
        status = "not_configured"

    return CheckResult(
        name="Environment",
        status=status,
        message=f"{layers} | {flags} | {executor_hint}",
        impact="scheduler and LLM read misconfigured env when bootstrap fails",
    )


def check_prediction_ml() -> CheckResult:
    """Verify forecast-lab ML runtime (libomp + lightgbm/xgboost/darts)."""
    try:
        from src.trade.hub_bridge import ensure_trade_stack_path

        ensure_trade_stack_path()
        from trade_integrations.ml_runtime_env import verify_prediction_ml

        ok, message = verify_prediction_ml()
        if ok:
            return CheckResult(
                name="Prediction ML",
                status="ready",
                message=message,
                impact="",
            )
        return CheckResult(
            name="Prediction ML",
            status="error",
            message=message,
            impact="forecast lab ML tracks unavailable — run: ./scripts/ensure_prediction_ml.sh",
            critical=True,
        )
    except Exception as exc:
        return CheckResult(
            name="Prediction ML",
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            impact="forecast lab ML tracks unavailable",
            critical=True,
        )


def check_prediction_ml_installed() -> CheckResult:
    """The API boot gate's half of ``check_prediction_ml``: libomp (macOS) and the packages exist.

    Finding them costs nothing; importing them costs seconds of CPU, so the full import check
    (``check_prediction_ml``) runs after startup with the other probes (src/preflight_startup.py).
    """
    import sys
    from importlib.util import find_spec

    from src.trade.hub_bridge import ensure_trade_stack_path

    ensure_trade_stack_path()
    from trade_integrations.ml_runtime_env import PREDICTION_ML_MODULES, resolve_libomp_libdir

    missing = [m for m in PREDICTION_ML_MODULES if find_spec(m) is None]
    if sys.platform == "darwin" and not resolve_libomp_libdir():
        missing.append("libomp (brew install libomp)")
    if missing:
        return CheckResult(
            name="Prediction ML",
            status="error",
            message=f"not installed: {', '.join(missing)}",
            impact="forecast lab ML tracks unavailable — run: ./scripts/ensure_prediction_ml.sh",
            critical=True,
        )
    return CheckResult(
        name="Prediction ML",
        status="ready",
        message=f"installed: {', '.join(PREDICTION_ML_MODULES)} (imports verified after startup)",
        impact="",
    )
