import type { ExternalPredictionRecord, ExternalPredictionSnapshot } from "@/lib/api";
import type { LiveForecastInput } from "@/lib/forecastReplayUtils";

export interface StreetSummaryStats {
  horizonDays: number;
  watchlistCount: number;
  forecastCount: number;
  targetMin: number | null;
  targetMax: number | null;
  targetMedian: number | null;
  spot: number | null;
  fetchedAt: string | null;
}

type HorizonMatchProvenance = {
  selected_days?: number;
  target_days_ahead?: number | null;
  in_window?: boolean | null;
  soft_mismatch?: boolean;
};

function horizonMatch(record: ExternalPredictionRecord): HorizonMatchProvenance | undefined {
  return record.provenance?.horizon_match as HorizonMatchProvenance | undefined;
}

export function hasHorizonMismatch(record: ExternalPredictionRecord): boolean {
  const match = horizonMatch(record);
  if (!match) return false;
  return match.soft_mismatch === true || match.in_window === false;
}

/** Use article target horizon on chart when tab horizon differs (soft mismatch). */
export function effectiveChartHorizonDays(
  record: ExternalPredictionRecord,
  tabHorizonDays: number,
): number {
  if (!hasHorizonMismatch(record)) return tabHorizonDays;
  const ahead = horizonMatch(record)?.target_days_ahead;
  if (typeof ahead === "number" && ahead > 0) return Math.max(1, Math.round(ahead));
  return tabHorizonDays;
}

export function filterVisiblePredictions(
  predictions: ExternalPredictionRecord[] | undefined,
): ExternalPredictionRecord[] {
  return (predictions ?? []).filter(
    (p) => p.fetch_status === "ok" && p.target?.mid != null,
  );
}

export function recordToLiveForecast(record: ExternalPredictionRecord): LiveForecastInput | undefined {
  const spot = record.spot_at_fetch;
  const mid = record.target?.mid;
  if (spot == null || mid == null || spot <= 0) return undefined;
  const expectedReturnPct =
    record.expected_return_pct ?? Math.round((mid / spot - 1) * 10000) / 100;
  return {
    asOf: record.as_of,
    spot,
    expectedReturnPct,
    rangeLow: record.target?.low ?? null,
    rangeHigh: record.target?.high ?? null,
  };
}

export function formatHorizonMatch(record: ExternalPredictionRecord): string | null {
  const match = horizonMatch(record);
  if (!match) return null;
  const selected = match.selected_days ?? record.horizon_days;
  const ahead = match.target_days_ahead;
  const base =
    ahead == null ? `Selected ${selected}d horizon` : `Selected ${selected}d · Target ~${ahead}d ahead`;
  if (hasHorizonMismatch(record)) {
    return `${base} · Horizon mismatch (chart uses article target date)`;
  }
  return base;
}

export function computeStreetSummary(
  snapshot: ExternalPredictionSnapshot | null,
  horizonDays: number,
): StreetSummaryStats {
  const visible = filterVisiblePredictions(snapshot?.predictions);
  const mids = visible
    .map((p) => p.target?.mid)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v))
    .sort((a, b) => a - b);
  const spot =
    snapshot?.internal_forecast?.spot ??
    visible.find((p) => p.spot_at_fetch != null)?.spot_at_fetch ??
    null;
  return {
    horizonDays,
    watchlistCount: snapshot?.sources?.filter((s) => s.watchlisted).length ?? 0,
    forecastCount: visible.length,
    targetMin: mids.length ? mids[0] : null,
    targetMax: mids.length ? mids[mids.length - 1] : null,
    targetMedian: mids.length ? mids[Math.floor(mids.length / 2)] : null,
    spot: typeof spot === "number" ? spot : null,
    fetchedAt: snapshot?.fetched_at ?? null,
  };
}
