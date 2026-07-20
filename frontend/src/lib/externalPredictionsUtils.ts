import type { ExternalPredictionRecord } from "@/lib/api";
import type { LiveForecastInput } from "@/lib/forecastReplayUtils";

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
  const match = record.provenance?.horizon_match as
    | { selected_days?: number; target_days_ahead?: number | null; in_window?: boolean | null }
    | undefined;
  if (!match) return null;
  const selected = match.selected_days ?? record.horizon_days;
  const ahead = match.target_days_ahead;
  if (ahead == null) return `Selected ${selected}d horizon`;
  return `Selected ${selected}d · Target ~${ahead}d ahead`;
}
