"""Fork-only preflight checks, run alongside upstream's own checks in preflight.py.

Extracted per docs/FORK_CONVENTIONS.md — each check function here is
self-contained (only needs ``CheckResult``, imported back from
``preflight.py``) and independent of upstream's own check implementations in
that file. ``preflight.py`` lazily imports these inside ``run_preflight()`` to
avoid a module-load-time import cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.preflight import CheckResult

if TYPE_CHECKING:
    from rich.console import Console


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
    executor_hint = "executor will start" if master else "executor skipped (master off)"

    status = "ready"
    if not report.layers_loaded and not master and not index_on and not monitor_on:
        status = "warning"

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


def require_prediction_ml_runtime(console: "Optional[Console]" = None) -> Optional[int]:
    """Hard-gate server startup on the Prediction ML check. None = proceed, else exit code."""
    from rich.console import Console

    result = check_prediction_ml()
    if result.status == "ready":
        return None
    out = console or Console()
    out.print(f"[red]FAIL[/red] Prediction ML: {result.message}")
    if result.impact:
        out.print(f"  {result.impact}")
    return 1
