import type { IndexTrackScoreboardReport } from "@/lib/api";
import {
  type ForecastSnapshot,
  type LiveForecastInput,
  mergePriceSeries,
  type PricePoint,
} from "@/lib/forecastReplayUtils";

const EXPERIMENTAL_TRACK_IDS = [
  "lightgbm_macro",
  "xgboost_macro",
  "arimax_macro",
  "darts_macro",
  "automl_cached",
] as const;

const CANONICAL_TRACK_IDS = [
  "quant_ridge",
  "quant_ridge_no_overlay",
  "macro_only",
  "macro_only_no_overlay",
  "bottom_up",
  "scenario_anchor",
  "event_overlay",
  "naive_zero",
  "naive_momentum",
  "debate_numeric",
  "headline_legacy",
] as const;

const BACKTEST_COMBINER_IDS = [
  "quant_only",
  "equal_weight_2",
  "equal_weight_3",
  "equal_weight_quant_3",
  "inverse_mae_w6",
  "shrinkage_50",
  "alignment_grid",
  "stress_conditional",
  "stress_conditional",
  "fixed_legacy",
  "stacked_ridge_meta",
  "equal_weight_ml_3",
] as const;

export { CANONICAL_TRACK_IDS, EXPERIMENTAL_TRACK_IDS, BACKTEST_COMBINER_IDS };

const TRACK_LABELS: Record<string, string> = {
  quant_ridge: "Quant Ridge",
  quant_ridge_no_overlay: "Quant Ridge (no overlay)",
  macro_only: "Macro only",
  macro_only_no_overlay: "Macro only (no overlay)",
  scenario_anchor: "Scenario anchor",
  event_overlay: "News shock",
  naive_zero: "Naive zero",
  naive_momentum: "Naive momentum",
  bottom_up: "Bottom up",
  debate_numeric: "Debate numeric",
  headline_legacy: "Headline legacy",
  lightgbm_macro: "LightGBM macro (ML)",
  xgboost_macro: "XGBoost macro (ML)",
  arimax_macro: "ARIMAX macro (ML)",
  darts_macro: "Darts macro (ML)",
  automl_cached: "AutoML cached (ML)",
  "combiner:quant_only": "Combiner: quant only",
  "combiner:equal_weight_2": "Combiner: equal (2)",
  "combiner:equal_weight_3": "Combiner: equal (3)",
  "combiner:equal_weight_quant_3": "Combiner: equal quant (3)",
  "combiner:shrinkage_50": "Combiner: shrinkage",
  "combiner:inverse_mae_w6": "Combiner: inverse MAE",
  "combiner:alignment_grid": "Combiner: alignment",
  "combiner:fixed_legacy": "Combiner: fixed legacy",
  "combiner:stress_conditional": "Combiner: stress",
  "combiner:stacked_ridge_meta": "Combiner: stacked ML meta",
  "combiner:equal_weight_ml_3": "Combiner: equal ML top 3",
};

function isoDay(raw: string | undefined): string {
  return String(raw ?? "").slice(0, 10);
}

export function trackDisplayLabel(trackId: string): string {
  return TRACK_LABELS[trackId] ?? trackId.replace(/^combiner:/, "").replace(/_/g, " ");
}

export function listScoreboardTrackIds(
  _report: IndexTrackScoreboardReport,
  includeCombiners = false,
): string[] {
  const ordered = [...CANONICAL_TRACK_IDS];
  if (!includeCombiners) return ordered;
  const combiners = BACKTEST_COMBINER_IDS.map((id) => `combiner:${id}`);
  return [...ordered, ...combiners];
}

export function scoreboardPriceSeries(report: IndexTrackScoreboardReport): PricePoint[] {
  const fromNifty = (report.nifty_series ?? []).map((p) => ({ date: p.date, close: p.close }));
  const fromChart = (report.chart?.nifty_close_series ?? []).map((p) => ({ date: p.date, close: p.close }));
  const liveSpot = report.live?.spot;
  const liveDate = isoDay(report.live?.as_of);
  const livePoint =
    liveSpot != null && Number.isFinite(liveSpot) && liveDate
      ? [{ date: liveDate, close: liveSpot }]
      : [];
  return mergePriceSeries([fromNifty, fromChart, livePoint]);
}

export function buildTrackForecastIndex(
  report: IndexTrackScoreboardReport,
  trackId: string,
): Map<string, ForecastSnapshot> {
  const map = new Map<string, ForecastSnapshot>();

  for (const row of report.daily_evaluations ?? []) {
    if (row.track_id !== trackId) continue;
    const d = isoDay(row.date);
    const spot = row.close;
    const ret = row.predicted_pct;
    if (!d || spot == null || ret == null || !Number.isFinite(spot) || !Number.isFinite(ret)) continue;
    map.set(d, {
      date: d,
      spot,
      expectedReturnPct: ret,
      source: "backtest",
    });
  }

  const live = report.live;
  const liveKey = trackId.startsWith("combiner:") ? null : trackId;
  const liveRow = liveKey ? live?.forecast_tracks?.[liveKey] : null;
  if (
    liveRow &&
    liveRow.available !== false &&
    live?.spot != null &&
    Number.isFinite(live.spot) &&
    liveRow.expected_return_pct != null &&
    Number.isFinite(liveRow.expected_return_pct)
  ) {
    const asOfDay = isoDay(live.as_of) || isoDay(new Date().toISOString());
    const entry: ForecastSnapshot = {
      date: asOfDay,
      spot: live.spot,
      expectedReturnPct: liveRow.expected_return_pct,
      source: "live",
    };
    map.set(asOfDay, entry);
  }

  const livePoint = report.chart?.live_point;
  if (
    livePoint?.tracks?.[trackId] != null &&
    livePoint.date &&
    livePoint.spot != null &&
    Number.isFinite(livePoint.spot)
  ) {
    const d = isoDay(livePoint.date);
    map.set(d, {
      date: d,
      spot: livePoint.spot,
      expectedReturnPct: livePoint.tracks[trackId],
      source: "live",
    });
  }

  return map;
}

export function buildTrackLiveForecast(
  report: IndexTrackScoreboardReport,
  trackId: string,
): LiveForecastInput | undefined {
  const live = report.live;
  if (!live?.spot || !Number.isFinite(live.spot)) return undefined;
  const liveKey = trackId.startsWith("combiner:") ? null : trackId;
  const row = liveKey ? live.forecast_tracks?.[liveKey] : null;
  if (!row || row.available === false || row.expected_return_pct == null) return undefined;
  return {
    asOf: live.as_of,
    spot: live.spot,
    expectedReturnPct: row.expected_return_pct,
  };
}
