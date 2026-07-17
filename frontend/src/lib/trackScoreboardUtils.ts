/** Track scoreboard colors and helpers. */

export const TRACK_CHART_COLORS: Record<string, string> = {
  actual: "#22c55e",
  quant_ridge: "#3b82f6",
  quant_ridge_no_overlay: "#2563eb",
  macro_only: "#8b5cf6",
  scenario_anchor: "#f97316",
  event_overlay: "#ef4444",
  naive_zero: "#94a3b8",
  naive_momentum: "#14b8a6",
  bottom_up: "#0ea5e9",
  debate_numeric: "#d946ef",
  headline_legacy: "#78716c",
  "combiner:quant_only": "#6366f1",
  "combiner:equal_weight_2": "#a855f7",
  "combiner:equal_weight_3": "#ec4899",
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
