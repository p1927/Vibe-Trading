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
  "fixed_legacy",
  "stacked_ridge_meta",
  "equal_weight_ml_3",
] as const;

export { CANONICAL_TRACK_IDS, EXPERIMENTAL_TRACK_IDS, BACKTEST_COMBINER_IDS };
