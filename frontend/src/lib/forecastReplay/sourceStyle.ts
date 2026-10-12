import type { Provenance } from "./types";

export const TRACK_CHART_COLORS: Record<string, string> = {
  actual: "#22c55e",
  headline: "#ca8a04",
  fan: "#e11d48",
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

/**
 * A track's display label: the server's (`SourceInfo.label`, or the scoreboard report's `track_catalog`) when it
 * sent one, else the id made readable. There is no label table in the browser (D306).
 */
export function trackDisplayLabel(trackId: string, catalog?: Record<string, { label?: string }> | null): string {
  return catalog?.[trackId]?.label ?? trackId.replace(/^combiner:/, "").replace(/_/g, " ");
}

export interface SourceStyle {
  label: string;
  color: string;
  /** Canvas/SVG dash array; empty = solid. Provenance is never colour-only. */
  dash: number[];
  hatch: boolean;
}

const DASH: Record<Provenance, number[]> = { live: [], replay: [6, 4], backtest: [2, 3] };

function hashColor(id: string): string {
  let h = 2166136261; // FNV-1a
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619) >>> 0;
  return `hsl(${h % 360}, 62%, 50%)`;
}

/** Table colour for known ids (`track:` prefix stripped), stable hash colour otherwise; label from the server first. */
export function sourceStyle(sourceId: string, provenance: Provenance, label?: string): SourceStyle {
  const key = sourceId.replace(/^track:/, "").replace(/@backtest$/, "");
  return {
    label: label ?? trackDisplayLabel(key),
    color: TRACK_CHART_COLORS[key] ?? hashColor(sourceId),
    dash: DASH[provenance],
    hatch: provenance === "backtest",
  };
}
