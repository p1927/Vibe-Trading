/** Human label for news_impact_engine._no_forecast's machine-readable reason codes: the one map both news views read. */
const NO_FORECAST_REASON_LABELS: Record<string, string> = {
  uncalibrated_bucket: "no calibrated magnitude for this kind of story yet",
  no_directional_cause_indicators: "no directional cause was extracted from the story",
  direction_unresolved: "the extracted causes point in conflicting directions",
  evidence_too_weak: "the cause is real but its confidence is too low to size a move",
  no_spot_or_primary_factor: "no index level or primary factor to score against",
  calibration_lookup_failed: "the calibration lookup failed",
  simulate_index_prediction_failed: "the index simulation failed",
  ledger_row_had_no_forecast: "the stored ledger row carried no forecast",
  legacy_fabricated_forecast: "this story was scored by the retired fixed-constant model; no real forecast exists",
};

export function noForecastReasonLabel(reason?: string | null): string {
  if (!reason) return "the engine declined to forecast (no reason recorded)";
  return NO_FORECAST_REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}
