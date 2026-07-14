"""Turnover-aware optimizer: mean-variance utility with an L1 turnover penalty.

Solves, per rebalance date::

    min  -w'mu + lambda * w'Sigma w + gamma * ||w - w_prev||_1
    s.t. w >= 0, sum(w) = 1
         w_i <= max_per_name                          (per-name cap)
         sum_{i in group_g} w_i <= max_per_group[g]   (per-group cap)

where ``w_prev`` is the weight vector applied at the previous rebalance,
restricted to the current active set (assets absent last time have prior
weight 0, so entries and exits both count as turnover). With ``gamma == 0``
the objective reduces to the mean-variance utility baseline.

The penalty ``gamma`` is scale-sensitive: it is measured against the return
term ``w'mu``, so an appropriate magnitude depends on the return units of the
input window. For daily returns (~1e-3), even ``gamma`` around 0.5 makes the
optimizer strongly prefer holding still. Callers should tune ``gamma`` relative
to their data frequency.

Realized per-rebalance turnover (``0.5 * ||w_t - w_{t-1}||_1``) is accumulated
on the instance for cost-adjusted analysis. This is a class-API affordance:
the engine's module-level ``optimize`` entry constructs a fresh instance and
returns only the positions frame, so callers who want the turnover series must
instantiate ``TurnoverAwareOptimizer`` directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backtest.optimizers.base import BaseOptimizer


class TurnoverAwareOptimizer(BaseOptimizer):
    """Mean-variance weights penalized for turnover against prior weights.

    Attributes:
        risk_aversion: Weight on the variance term (lambda).
        turnover_penalty: Weight on the L1 turnover term (gamma). 0 reduces to
            the mean-variance baseline.
        max_per_name: Per-asset weight cap (None = no limit).
        groups: Asset-code → group-name mapping for per-group caps.
        max_per_group: Group-name → maximum total weight for that group.
        realized_turnover: Per-rebalance realized turnover collected during
            ``optimize`` (``0.5 * ||w_t - w_{t-1}||_1``).
    """

    def __init__(
        self,
        lookback: int = 60,
        risk_aversion: float = 1.0,
        turnover_penalty: float = 0.0,
        max_per_name: Optional[float] = None,
        groups: Optional[Dict[str, str]] = None,
        max_per_group: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(lookback=lookback, **kwargs)
        self.risk_aversion = float(risk_aversion)
        self.turnover_penalty = float(turnover_penalty)
        self.max_per_name = float(max_per_name) if max_per_name is not None else None
        self.groups: Dict[str, str] = dict(groups) if groups else {}
        self.max_per_group: Dict[str, float] = dict(max_per_group) if max_per_group else {}
        self._prev: Dict[str, float] = {}
        self.realized_turnover: List[float] = []

    def _build_context(
        self, window: pd.DataFrame, active: List[str]
    ) -> "Dict[str, Any] | None":
        """Mean vector, covariance, and active codes for the current window."""
        mu = window.mean().values
        cov = window.cov().values
        if np.isnan(cov).any() or np.isnan(mu).any():
            return None
        return {"cov": cov, "mu": mu, "active": list(active)}

    def _calc_weights(self, ctx: Dict[str, Any]) -> np.ndarray:
        """SLSQP weights for the penalized objective; updates turnover state."""
        from scipy.optimize import minimize

        mu = np.asarray(ctx["mu"], dtype=float)
        cov = np.asarray(ctx["cov"], dtype=float)
        active: List[str] = ctx["active"]
        n = len(mu)
        if n == 0:
            return self._equal_weight(0)

        w_prev = np.array([self._prev.get(code, 0.0) for code in active], dtype=float)
        lam = self.risk_aversion
        gamma = self.turnover_penalty

        def objective(w: np.ndarray) -> float:
            ret = w @ mu
            var = w @ cov @ w
            turn = np.abs(w - w_prev).sum()
            return -ret + lam * var + gamma * turn

        # --- bounds: per-name cap ---
        upper = 1.0
        if self.max_per_name is not None:
            upper = min(1.0, float(self.max_per_name))
        bounds = [(0.0, upper)] * n

        # --- constraints: simplex + per-group caps ---
        constraints: list = [
            {"type": "eq", "fun": lambda w: w.sum() - 1.0},
        ]

        group_indices: Dict[str, List[int]] = {}
        if self.groups and self.max_per_group:
            for i, code in enumerate(active):
                g = self.groups.get(code)
                if g is not None:
                    group_indices.setdefault(g, []).append(i)
            for g, cap in self.max_per_group.items():
                indices = group_indices.get(g, [])
                if not indices:
                    continue
                cap = float(cap)
                constraints.append({
                    "type": "ineq",
                    "fun": lambda w, idx=indices, c=cap: c - w[np.array(idx)].sum(),
                })

        x0 = w_prev if w_prev.sum() > 1e-12 else self._equal_weight(n)
        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 200, "ftol": 1e-10},
        )

        weights = self._normalize(result.x) if result.success else self._equal_weight(n)
        self._record_turnover(active, weights)
        return weights

    def _record_turnover(self, active: List[str], weights: np.ndarray) -> None:
        """Accumulate realized turnover and roll prior weights forward."""
        codes = set(active) | set(self._prev)
        new_map = {code: float(weights[i]) for i, code in enumerate(active)}
        turnover = 0.5 * sum(
            abs(new_map.get(code, 0.0) - self._prev.get(code, 0.0)) for code in codes
        )
        self.realized_turnover.append(turnover)
        self._prev = new_map


def optimize(
    ret: pd.DataFrame,
    pos: pd.DataFrame,
    dates: pd.DatetimeIndex,
    lookback: int = 60,
    risk_aversion: float = 1.0,
    turnover_penalty: float = 0.0,
    max_per_name: Optional[float] = None,
    groups: Optional[Dict[str, str]] = None,
    max_per_group: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Module-level entry: turnover-penalized mean-variance positions."""
    return TurnoverAwareOptimizer(
        lookback=lookback,
        risk_aversion=risk_aversion,
        turnover_penalty=turnover_penalty,
        max_per_name=max_per_name,
        groups=groups,
        max_per_group=max_per_group,
    ).optimize(ret, pos, dates)
