/** Track scoreboard colors and helpers. */

import type { IndexTrackScoreboardReport } from "@/lib/api";

export const TRACK_CHART_COLORS: Record<string, string> = {
  actual: "#22c55e",
  quant_ridge: "#3b82f6",
  quant_ridge_no_overlay: "#2563eb",
  macro_only: "#8b5cf6",
  macro_only_no_overlay: "#7c3aed",
  scenario_anchor: "#f97316",
  event_overlay: "#ef4444",
  naive_zero: "#94a3b8",
  naive_momentum: "#14b8a6",
  bottom_up: "#0ea5e9",
  debate_numeric: "#d946ef",
  headline_legacy: "#78716c",
  lightgbm_macro: "#059669",
  xgboost_macro: "#10b981",
  arimax_macro: "#0891b2",
  darts_macro: "#6366f1",
  automl_cached: "#a855f7",
  "combiner:quant_only": "#6366f1",
  "combiner:equal_weight_2": "#a855f7",
  "combiner:equal_weight_3": "#ec4899",
  "combiner:equal_weight_quant_3": "#db2777",
  "combiner:shrinkage_50": "#eab308",
  "combiner:stress_conditional": "#f43f5e",
};

export function trackColor(trackId: string): string {
  return TRACK_CHART_COLORS[trackId] ?? "#64748b";
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function fmtHitRate(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${Math.round(v * 100)}%`;
}

export function fmtNum(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export interface TrackEvalStats {
  evalCount: number;
  hitCount: number;
  missCount: number;
  hitRate: number | null;
  maePct: number | null;
}

export function computeTrackEvalStats(
  daily: IndexTrackScoreboardReport["daily_evaluations"],
  trackId: string,
): TrackEvalStats {
  const rows = (daily ?? []).filter((r) => r.track_id === trackId);
  const hitCount = rows.filter((r) => r.direction_hit).length;
  const evalCount = rows.length;
  const missCount = evalCount - hitCount;
  const maeValues = rows
    .map((r) => Math.abs(Number(r.error_pct)))
    .filter((v) => Number.isFinite(v));
  return {
    evalCount,
    hitCount,
    missCount,
    hitRate: evalCount > 0 ? hitCount / evalCount : null,
    maePct: maeValues.length ? maeValues.reduce((a, b) => a + b, 0) / maeValues.length : null,
  };
}
