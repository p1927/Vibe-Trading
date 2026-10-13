/** Track scoreboard colors and helpers. */

import type { IndexTrackScoreboardReport } from "@/lib/api";

import { hashColor, TRACK_CHART_COLORS } from "@/lib/forecastReplay/sourceStyle";

/** The one colour of a track: the table's, else the stable hash colour the replay uses (never one grey for every unlisted track). */
export function trackColor(trackId: string): string {
  return TRACK_CHART_COLORS[trackId] ?? hashColor(trackId);
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

const COMBINER_PREFIX = "combiner:";

export interface ScoreboardIds {
  /** Live forecast tracks (the server's `track_catalog`, not experimental). */
  live: string[];
  /** Gated ML / fusion tracks (`track_catalog[id].experimental`). */
  experimental: string[];
  /** Combiner ids without the `combiner:` prefix (`report.combiners` keys, server order). */
  combiners: string[];
}

/**
 * Which tracks and combiners a scoreboard shows, read from the report itself (server order). The server owns the
 * lists (`track_constants.py`); a hand-copied list in the browser drifted: seven tracks and a combiner were never shown.
 */
export function scoreboardIds(report: IndexTrackScoreboardReport | null | undefined): ScoreboardIds {
  const catalog = report?.track_catalog ?? {};
  const catalogTracks = Object.entries(catalog).filter(([id]) => !id.startsWith(COMBINER_PREFIX));
  const tracks: [string, { experimental?: boolean }][] = catalogTracks.length
    ? catalogTracks
    : Object.keys(report?.tracks ?? {}).map((id) => [id, {}]);
  const combiners = Object.keys(report?.combiners ?? {});
  for (const id of Object.keys(catalog)) {
    const bare = id.slice(COMBINER_PREFIX.length);
    if (id.startsWith(COMBINER_PREFIX) && !combiners.includes(bare)) combiners.push(bare);
  }
  return {
    live: tracks.filter(([, c]) => !c.experimental).map(([id]) => id),
    experimental: tracks.filter(([, c]) => c.experimental).map(([id]) => id),
    combiners,
  };
}

/** The Promote cell of a combiner: only `promoted_combiners` is a promotion; a raw pass says which bar it failed. */
export function promotionCell(
  promo: IndexTrackScoreboardReport["promotion"] | null | undefined,
  cid: string,
): { label: string; tone: "yes" | "raw" | "no"; title?: string } {
  const verdict = promo?.verdicts?.[cid];
  if ((promo?.promoted_combiners ?? []).includes(cid)) return { label: "yes", tone: "yes" };
  if (verdict?.promoted) {
    const ci = promo?.bootstrap_ci?.[cid];
    if (ci && ci.passes === false) {
      const why = ci.reason
        ? ci.reason.replace(/_/g, " ")
        : ci.lower_bound_pp != null
          ? `bootstrap lower bound ${ci.lower_bound_pp}pp is under the ${ci.margin_pp ?? "?"}pp margin`
          : "bootstrap bar failed";
      return { label: "raw pass only", tone: "raw", title: `Passed this run's thresholds, not promoted: ${why}` };
    }
    return { label: "raw pass only", tone: "raw", title: "Passed this run's thresholds; not yet stable over consecutive runs" };
  }
  return { label: verdict?.mae_passes === false ? "mae" : "no", tone: "no" };
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
