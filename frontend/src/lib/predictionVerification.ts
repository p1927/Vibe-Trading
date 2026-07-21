import type { IndexBacktestReport, IndexPredictionArtifact } from "@/lib/api";

/** Core macro keys always expected in live snapshot (subset of backend MACRO_FACTOR_KEYS). */
export const MACRO_CORE_KEYS = [
  "oil_brent",
  "oil_wti",
  "usd_inr",
  "gold",
  "sp500",
  "us_10y",
  "india_vix",
  "fii_net_5d",
  "dii_net_5d",
  "fii_fut_long_short_ratio",
  "nifty_pe",
  "cpi_yoy_proxy",
  "repo_rate",
  "index_sentiment",
  "nifty_pcr",
  "nifty_return_7d",
  "nifty_return_14d",
  "nifty_rsi_14",
  "nifty_realized_vol_20d",
  "nifty_ma20_distance_pct",
  "constituent_momentum_7d",
  "days_to_monthly_expiry",
  "is_budget_week",
  "is_results_season",
] as const;

/** Extended keys used by Ridge when panel coverage allows — mirrors factor_matrix.py MACRO_FACTOR_KEYS. */
export const MACRO_EXTENDED_KEYS = [
  "nifty_ma50_distance_pct",
  "nifty_ma200_distance_pct",
  "nifty_macd_line",
  "nifty_macd_signal",
  "nifty_macd_histogram",
  "nifty_bb_percent_b",
  "nifty_bb_width_pct",
  "nifty_stoch_k",
  "nifty_stoch_d",
  "nifty_williams_r",
  "nifty_cci_20",
  "nifty_adx_14",
  "nifty_atr_pct",
  "nifty_golden_cross_signal",
  "qfinindia_skew",
  "qfinindia_expected_move",
  "qfinindia_tail_risk",
  "institutional_net_5d",
  "dii_absorption_ratio",
  "nifty_earnings_yield",
  "equity_risk_premium",
  "india_term_spread",
  "india_credit_spread",
  "india_vix_velocity_3d",
  "usd_inr_momentum_5d",
  "us_10y_velocity_3d",
  "fii_net_5d_momentum",
] as const;

/** Full Ridge feature universe for UI verification — keep in sync with factor_matrix.py. */
export const MACRO_MODEL_KEYS = [...MACRO_CORE_KEYS, ...MACRO_EXTENDED_KEYS] as const;

export type ModelRole = "feeds" | "display" | "context" | "verify" | "ops";

export type VerificationStatus = "ok" | "warn" | "error" | "empty";

export interface UiVerificationRow {
  id: string;
  component: string;
  section: string;
  modelRole: ModelRole;
  status: VerificationStatus;
  source: string;
  userValue: string;
  feedsForecast: boolean;
  detail?: string;
}

export interface PredictionUiAudit {
  rows: UiVerificationRow[];
  summary: { ok: number; warn: number; error: number; empty: number };
  macroCoverage: { present: number; total: number; missing: string[] };
}

export interface PredictionUiExternalState {
  factorHistoryCount?: number;
  factorHistoryError?: string | null;
  dailyHistoryCount?: number;
  historyError?: string | null;
  derivativesSeriesCount?: number;
  derivativesError?: string | null;
  backtest?: IndexBacktestReport | null;
  backtestError?: string | null;
  horizonDays?: number;
}

function globalFactorMap(artifact: IndexPredictionArtifact): Map<string, number | null> {
  const map = new Map<string, number | null>();
  for (const row of artifact.global_factors ?? []) {
    const key = row.factor ?? row.label;
    if (key) map.set(key, row.value ?? null);
  }
  return map;
}

function statusFrom(checks: { ok: boolean; warn?: boolean; empty?: boolean }): VerificationStatus {
  if (checks.empty) return "empty";
  if (!checks.ok) return "error";
  if (checks.warn) return "warn";
  return "ok";
}

function summarize(rows: UiVerificationRow[]): PredictionUiAudit["summary"] {
  return rows.reduce(
    (acc, row) => {
      acc[row.status] += 1;
      return acc;
    },
    { ok: 0, warn: 0, error: 0, empty: 0 },
  );
}

/** Audit every Prediction page block: render data, source, and forecast contribution. */
export function runPredictionUiAudit(
  artifact: IndexPredictionArtifact | null | undefined,
  external: PredictionUiExternalState = {},
): PredictionUiAudit {
  if (!artifact) {
    return {
      rows: [
        {
          id: "artifact",
          component: "Hub artifact",
          section: "Core",
          modelRole: "display",
          status: "empty",
          source: "reports/hub/NIFTY/index_prediction/latest.json",
          userValue: "Run analysis to load prediction",
          feedsForecast: false,
          detail: "No artifact loaded from GET /trade/index-prediction",
        },
      ],
      summary: { ok: 0, warn: 0, error: 0, empty: 1 },
      macroCoverage: { present: 0, total: MACRO_MODEL_KEYS.length, missing: [...MACRO_MODEL_KEYS] },
    };
  }

  const horizon = external.horizonDays ?? artifact.horizon?.days ?? 14;
  const pred = artifact.prediction ?? {};
  const gf = globalFactorMap(artifact);
  const missingMacro = MACRO_MODEL_KEYS.filter((key) => {
    const v = gf.get(key);
    return v == null || !Number.isFinite(Number(v));
  });
  const missingCore = MACRO_CORE_KEYS.filter((key) => {
    const v = gf.get(key);
    return v == null || !Number.isFinite(Number(v));
  });
  const macroPresent = MACRO_MODEL_KEYS.length - missingMacro.length;
  const corePresent = MACRO_CORE_KEYS.length - missingCore.length;

  const rows: UiVerificationRow[] = [];

  const spotOk = artifact.spot != null && Number.isFinite(artifact.spot);
  rows.push({
    id: "spot-live",
    component: "Live spot (OpenAlgo)",
    section: "Headline",
    modelRole: "feeds",
    status: statusFrom({
      ok: spotOk && !artifact.spot_error && artifact.spot_source === "openalgo",
      warn: spotOk && artifact.spot_source !== "openalgo",
      error: Boolean(artifact.spot_error) || !spotOk,
    }),
    source: "OpenAlgo LIVE quote (INDmoney) — no history fallback",
    userValue: artifact.spot_error
      ? artifact.spot_error
      : spotOk
        ? `${artifact.spot?.toLocaleString("en-IN")} (${artifact.spot_source ?? "openalgo"})`
        : "Missing spot",
    feedsForecast: true,
    detail: artifact.as_of ? `Snapshot ${artifact.as_of.slice(0, 19)}` : undefined,
  });

  rows.push({
    id: "summary",
    component: "PredictionSummary",
    section: "Headline",
    modelRole: "display",
    status: statusFrom({
      ok: spotOk && pred.expected_return_pct != null,
      warn: !pred.range?.low,
    }),
    source: "artifact.prediction, regime, accuracy",
    userValue: spotOk
      ? `${horizon}d target from ${pred.expected_return_pct?.toFixed(2) ?? "—"}% return`
      : "Missing spot",
    feedsForecast: false,
    detail: pred.reconciled_with_scenarios
      ? "Headline blended toward scenario anchor when model diverged"
      : undefined,
  });

  rows.push({
    id: "forecast-chart",
    component: "NiftyForecastReplayChart",
    section: "Timeline",
    modelRole: "display",
    status: statusFrom({
      ok: spotOk && pred.expected_return_pct != null,
      empty: !spotOk,
    }),
    source: "ledger history + backtest daily_evaluations + live artifact",
    userValue: `${(external.dailyHistoryCount ?? 0) + (external.backtest?.daily_evaluations?.length ?? 0)} forecast anchors`,
    feedsForecast: false,
  });

  rows.push({
    id: "factor-playground",
    component: "FactorImpactWorkbench",
    section: "What-if",
    modelRole: "feeds",
    status: statusFrom({
      ok: (artifact.factor_explanation?.contributors?.length ?? 0) > 0,
      warn: missingMacro.length > 8,
    }),
    source: "factor_explanation + POST /index-prediction/simulate",
    userValue: `${artifact.factor_explanation?.contributors?.length ?? 0} ranked contributors`,
    feedsForecast: true,
    detail: "Sliders re-run Ridge macro overlay on live factor levels",
  });

  rows.push({
    id: "scenarios",
    component: "ScenarioTiles",
    section: "Scenarios",
    modelRole: "feeds",
    status: statusFrom({
      ok: (artifact.scenarios?.length ?? 0) >= 3,
      warn: (artifact.scenarios?.length ?? 0) > 0 && (artifact.scenarios?.length ?? 0) < 3,
      empty: !(artifact.scenarios?.length ?? 0),
    }),
    source: "build_index_scenarios → reconcile_prediction_with_scenarios",
    userValue: `${artifact.scenarios?.length ?? 0} outcomes (prob-sorted)`,
    feedsForecast: true,
    detail: pred.scenario_anchor_return_pct != null
      ? `Scenario anchor ${pred.scenario_anchor_return_pct}%`
      : "Anchors headline when macro model diverges >1.5%",
  });

  const signalCount = artifact.constituent_signals?.length ?? 0;
  const withSentiment = (artifact.constituent_signals ?? []).filter(
    (s) => s.sentiment_score != null && Number.isFinite(Number(s.sentiment_score)),
  ).length;
  rows.push({
    id: "constituents",
    component: "ConstituentDrivers",
    section: "Bottom-up",
    modelRole: "feeds",
    status: statusFrom({
      ok: signalCount >= 40 && withSentiment >= 30,
      warn: signalCount > 0 && withSentiment < 30,
      empty: signalCount === 0,
    }),
    source: "batch_constituent_research → attribute_constituents",
    userValue: `${signalCount} stocks · ${withSentiment} with sentiment`,
    feedsForecast: true,
    detail: `Bottom-up block ${pred.bottom_up_return_pct?.toFixed(2) ?? "—"}% (70% sentiment + 30% momentum)`,
  });

  rows.push({
    id: "factor-table",
    component: "FactorCompositionTable",
    section: "Macro levels",
    modelRole: "feeds",
    status: statusFrom({
      ok: corePresent >= 18,
      warn: corePresent >= 10 && corePresent < 18,
      empty: corePresent === 0,
    }),
    source: "fetch_global_macro_snapshot → global_factors",
    userValue: `${macroPresent}/${MACRO_MODEL_KEYS.length} Ridge inputs · ${corePresent}/${MACRO_CORE_KEYS.length} core`,
    feedsForecast: true,
    detail: missingMacro.length
      ? `Missing: ${missingMacro.slice(0, 6).join(", ")}${missingMacro.length > 6 ? "…" : ""}`
      : "All Ridge inputs present",
  });

  rows.push({
    id: "derivatives",
    component: "DerivativesFactorsPanel",
    section: "Flows",
    modelRole: "context",
    status: external.derivativesError
      ? "error"
      : statusFrom({
          ok: (external.derivativesSeriesCount ?? 0) > 30,
          warn:
            (external.derivativesSeriesCount ?? 0) > 0 &&
            (external.derivativesSeriesCount ?? 0) <= 30,
          empty: !(external.derivativesSeriesCount ?? 0),
        }),
    source: "GET /index-prediction/factor-history (PCR, FII/DII)",
    userValue: external.derivativesError
      ? "Load failed"
      : `${external.derivativesSeriesCount ?? 0} history points`,
    feedsForecast: false,
    detail: "PCR & FII/DII feed Ridge; chart is 12m context for crash-day review",
  });

  rows.push({
    id: "factor-timeline",
    component: "IndexFactorTimelineChart",
    section: "Drift",
    modelRole: "context",
    status: external.factorHistoryError
      ? "error"
      : statusFrom({
          ok: (external.factorHistoryCount ?? 0) > 50,
          warn: (external.factorHistoryCount ?? 0) > 0,
          empty: !(external.factorHistoryCount ?? 0),
        }),
    source: "GET /index-prediction/factor-history",
    userValue: external.factorHistoryError ?? `${external.factorHistoryCount ?? 0} series points`,
    feedsForecast: false,
  });

  rows.push({
    id: "sector-breadth",
    component: "SectorBreadthPanel",
    section: "Breadth",
    modelRole: "context",
    status: statusFrom({
      ok: artifact.sector_breadth?.mean_sentiment != null,
      empty: artifact.sector_breadth?.mean_sentiment == null,
    }),
    source: "aggregator._sector_breadth (constituent sentiment rollup)",
    userValue:
      artifact.sector_breadth?.mean_sentiment != null
        ? `Mean sentiment ${Number(artifact.sector_breadth.mean_sentiment).toFixed(2)}`
        : "No breadth data",
    feedsForecast: false,
    detail: "Displayed for dispersion; not a direct Ridge term in live forecast",
  });

  rows.push({
    id: "equation",
    component: "EquationCard",
    section: "Model",
    modelRole: "display",
    status: statusFrom({
      ok: Boolean(pred.equation?.coefficients && Object.keys(pred.equation.coefficients).length),
      empty: !pred.equation?.coefficients,
    }),
    source: "train_macro_ridge artifact + predict_nifty",
    userValue: pred.macro_delta_pct != null ? `Macro overlay ${pred.macro_delta_pct.toFixed(2)}%` : "—",
    feedsForecast: false,
    detail: `R² walk-forward ${pred.equation?.r2_walk_forward?.toFixed(3) ?? "—"}`,
  });

  rows.push({
    id: "sensitivity",
    component: "CausalFactorExplorer",
    section: "Sensitivity",
    modelRole: "display",
    status: statusFrom({
      ok: (artifact.factor_sensitivity?.length ?? 0) >= 5,
      warn: (artifact.factor_sensitivity?.length ?? 0) > 0,
      empty: !(artifact.factor_sensitivity?.length ?? 0),
    }),
    source: "explain.py factor_sensitivity (reconciled) + cascade simulate",
    userValue: `${artifact.factor_sensitivity?.length ?? 0} sensitivity curves`,
    feedsForecast: false,
    detail: "Derived from trained Ridge — explains marginal shocks",
  });

  rows.push({
    id: "forecast-history",
    component: "ForecastHistorySection",
    section: "Track record",
    modelRole: "verify",
    status: external.historyError
      ? "error"
      : statusFrom({
          ok: (external.dailyHistoryCount ?? 0) >= 5,
          warn: (external.dailyHistoryCount ?? 0) > 0,
          empty: !(external.dailyHistoryCount ?? 0),
        }),
    source: "GET /index-prediction/history",
    userValue: external.historyError ?? `${external.dailyHistoryCount ?? 0} daily snapshots`,
    feedsForecast: false,
  });

  rows.push({
    id: "backtest",
    component: "BacktestEvaluationPanel",
    section: "Walk-forward",
    modelRole: "verify",
    status: external.backtestError
      ? "error"
      : statusFrom({
          ok: external.backtest?.metrics?.mae_pct != null,
          empty: !external.backtest,
        }),
    source: "GET /index-prediction/backtest",
    userValue: external.backtestError
      ? "Failed"
      : external.backtest?.metrics?.mae_pct != null
        ? `MAE ${external.backtest.metrics.mae_pct.toFixed(2)}%`
        : "No report",
    feedsForecast: false,
    detail: "Validates model on past data; does not change live headline",
  });

  rows.push({
    id: "pipeline",
    component: "PredictionPipelinePanel",
    section: "Ops",
    modelRole: "ops",
    status: statusFrom({
      ok: (artifact.pipeline_log?.length ?? 0) > 0,
      warn: Boolean(artifact.stage_errors?.length),
    }),
    source: "aggregator pipeline_log + factor_catalog",
    userValue: `${artifact.pipeline_log?.length ?? 0} log lines · ${artifact.stage_errors?.length ?? 0} errors`,
    feedsForecast: false,
  });

  return {
    rows,
    summary: summarize(rows),
    macroCoverage: {
      present: macroPresent,
      total: MACRO_MODEL_KEYS.length,
      missing: [...missingMacro],
    },
  };
}

export const MODEL_ROLE_LABELS: Record<ModelRole, string> = {
  feeds: "Feeds forecast",
  display: "Forecast output",
  context: "Context only",
  verify: "History verification",
  ops: "Pipeline ops",
};
