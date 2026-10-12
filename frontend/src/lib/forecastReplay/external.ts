import type { ExternalPredictionRecord } from "../api";
import type { ReplayRecord } from "./types";

/**
 * An analyst card's forecast as a replay record. The target date is the one the source published
 * (never computed here); null when the card has no spot, target or target date to place on a chart.
 */
export function recordFromExternal(rec: ExternalPredictionRecord, tabHorizonDays: number): ReplayRecord | null {
  const spot = rec.spot_at_fetch;
  const mid = rec.target?.mid;
  const targetDate = rec.target_date?.slice(0, 10);
  const issuedAt = rec.as_of ?? rec.published_at;
  if (spot == null || spot <= 0 || mid == null || !targetDate || !issuedAt) return null;
  return {
    source_id: `external:${rec.source_id}`,
    family: "external",
    issued_at: issuedAt,
    issue_day: issuedAt.slice(0, 10),
    provenance: "live",
    spot_at_issue: spot,
    approximate_path: true,
    meta: { direction: rec.direction ?? "neutral" },
    points: [{
      horizon_days: rec.horizon_days ?? tabHorizonDays,
      target_date: targetDate,
      p50_level: mid,
      p10_level: rec.target?.low ?? null,
      p90_level: rec.target?.high ?? null,
      matured: false, actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null,
    }],
  };
}
