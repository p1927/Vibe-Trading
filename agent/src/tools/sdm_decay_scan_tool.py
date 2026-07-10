"""BaseTool wrapper for batch decay monitoring scan across active artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from src.agent.tools import BaseTool
from src.strategy_store.decay import DecayEvaluator
from src.strategy_store.models import (
    ArtifactStatus,
    ArtifactType,
    DecaySignal,
    DecaySnapshot,
)
from src.strategy_store._shared import get_store as _get_store


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def _error(exc: Exception) -> str:
    return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


def _compute_scan_metrics(
    bench_history: list[Any],
) -> dict[str, float | None]:
    """Compute baseline and rolling IC metrics from bench history.

    Uses last 20 bench results: baseline from first 5, rolling from last 5.
    Returns dict with baseline_ic_mean, rolling_ic_mean, ic_ratio,
    rolling_ir, ic_positive_ratio.
    """
    result: dict[str, float | None] = {
        "baseline_ic_mean": None,
        "rolling_ic_mean": None,
        "ic_ratio": None,
        "rolling_ir": None,
        "ic_positive_ratio": None,
    }

    # bench_history is newest-first; reverse for chronological order
    chronological = list(reversed(bench_history))

    ic_values = [
        r.ic_mean for r in chronological if r.ic_mean is not None
    ]

    if len(ic_values) < 3:
        return result

    baseline_ics = ic_values[:5]
    rolling_ics = ic_values[-5:]

    baseline_mean = sum(baseline_ics) / len(baseline_ics)
    rolling_mean = sum(rolling_ics) / len(rolling_ics)

    result["baseline_ic_mean"] = round(baseline_mean, 6)
    result["rolling_ic_mean"] = round(rolling_mean, 6)

    if baseline_mean != 0:
        result["ic_ratio"] = round(rolling_mean / baseline_mean, 4)

    # IR from rolling window
    if len(rolling_ics) > 1:
        mean_r = sum(rolling_ics) / len(rolling_ics)
        var_r = sum((x - mean_r) ** 2 for x in rolling_ics) / (len(rolling_ics) - 1)
        std_r = var_r**0.5
        if std_r > 0:
            result["rolling_ir"] = round(mean_r / std_r, 4)

    # IC positive ratio across all available values
    positive_count = sum(1 for v in ic_values if v > 0)
    result["ic_positive_ratio"] = round(positive_count / len(ic_values), 4)

    return result


class SdmDecayScanTool(BaseTool):
    """Run decay monitoring scan on active factors/strategies."""

    name = "sdm_decay_scan"
    description = (
        "Run decay monitoring scan on active factors/strategies. "
        "Evaluates rolling IC vs baseline for each active artifact and "
        "reports decay signals."
    )
    is_readonly = True
    repeatable = True
    parameters = {
        "type": "object",
        "properties": {
            "universe": {
                "type": "string",
                "description": "Filter by universe",
            },
            "artifact_type": {
                "type": "string",
                "enum": ["factor", "strategy"],
                "description": "Filter by type",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Report without applying transitions",
                "default": False,
            },
        },
        "required": [],
    }

    def execute(self, **kwargs: Any) -> str:
        """Scan active artifacts for decay and return a summary."""
        try:
            store = _get_store()
            evaluator = DecayEvaluator()

            universe = kwargs.get("universe")
            type_filter = None
            if kwargs.get("artifact_type"):
                type_filter = ArtifactType(kwargs["artifact_type"])
            dry_run = bool(kwargs.get("dry_run", False))

            # Collect ACTIVE and MONITORING artifacts
            active = store.list_artifacts(
                type=type_filter,
                status=ArtifactStatus.ACTIVE,
                universe=universe,
            )
            monitoring = store.list_artifacts(
                type=type_filter,
                status=ArtifactStatus.MONITORING,
                universe=universe,
            )

            targets = list(active) + list(monitoring)

            counts = {
                "total_scanned": 0,
                "healthy": 0,
                "warning": 0,
                "decayed": 0,
                "critical": 0,
                "insufficient_data": 0,
            }
            transitions_applied: list[dict[str, Any]] = []
            per_artifact: list[dict[str, Any]] = []

            for artifact in targets:
                counts["total_scanned"] += 1
                bench_history = list(
                    store.get_bench_history(artifact.id, limit=20)
                )

                if len(bench_history) < 3:
                    counts["insufficient_data"] += 1
                    per_artifact.append({
                        "artifact_id": artifact.id,
                        "name": artifact.name,
                        "signal": "insufficient_data",
                        "bench_count": len(bench_history),
                    })
                    continue

                metrics = _compute_scan_metrics(bench_history)

                signal = evaluator.evaluate_decay(
                    ic_ratio=metrics["ic_ratio"],
                    ir=metrics["rolling_ir"],
                    ic_positive_ratio=metrics["ic_positive_ratio"],
                )

                # Count by signal
                signal_key = signal.value
                if signal_key in counts:
                    counts[signal_key] += 1

                # Determine transition
                decay_history = list(
                    store.get_decay_history(artifact.id, limit=10)
                )
                prior_signals = [
                    s.decay_signal for s in reversed(decay_history)
                    if s.decay_signal is not None
                ]
                recommended = evaluator.should_transition(
                    artifact.status, prior_signals + [signal]
                )

                # Apply transition if not dry_run
                transition_info: dict[str, Any] | None = None
                if recommended and not dry_run:
                    updated = store.update_status(
                        artifact.id,
                        recommended,
                        reason=f"Decay scan: {signal.value} signal triggered transition",
                    )
                    if updated is not None:
                        transition_info = {
                            "from": artifact.status.value,
                            "to": recommended.value,
                        }
                        transitions_applied.append({
                            "artifact_id": artifact.id,
                            "name": artifact.name,
                            **transition_info,
                        })

                # Record decay snapshot
                if not dry_run:
                    snapshot = DecaySnapshot(
                        artifact_id=artifact.id,
                        rolling_ic_mean=metrics["rolling_ic_mean"],
                        rolling_ir=metrics["rolling_ir"],
                        baseline_ic_mean=metrics["baseline_ic_mean"],
                        ic_ratio=metrics["ic_ratio"],
                        decay_signal=signal,
                        detail=json.dumps(metrics, ensure_ascii=False),
                    )
                    store.record_decay_snapshot(snapshot)

                entry: dict[str, Any] = {
                    "artifact_id": artifact.id,
                    "name": artifact.name,
                    "current_status": artifact.status.value,
                    "signal": signal.value,
                    "metrics": metrics,
                }
                if transition_info:
                    entry["transition"] = transition_info
                elif recommended and dry_run:
                    entry["recommended_transition"] = recommended.value

                per_artifact.append(entry)

            return _ok({
                "summary": counts,
                "transitions_applied": len(transitions_applied),
                "dry_run": dry_run,
                "artifacts": per_artifact,
            })
        except Exception as exc:
            return _error(exc)
