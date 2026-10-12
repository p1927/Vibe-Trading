/** Track scoreboard colors and helpers. */

import type { IndexTrackScoreboardReport } from "@/lib/api";

import { TRACK_CHART_COLORS } from "@/lib/forecastReplay/sourceStyle";

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
