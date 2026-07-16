/** Human labels for macro / model factor keys (mirrors explain.py). */
export const FACTOR_LABELS: Record<string, string> = {
  oil_brent: "Brent crude",
  oil_wti: "WTI crude",
  usd_inr: "USD/INR",
  gold: "Gold",
  sp500: "S&P 500",
  us_10y: "US 10Y yield",
  india_vix: "India VIX",
  fii_net_5d: "FII net (5d)",
  dii_net_5d: "DII net (5d)",
  fii_fut_long_short_ratio: "FII index fut long/short",
  nifty_pe: "Nifty PE",
  cpi_yoy_proxy: "CPI (proxy)",
  repo_rate: "Repo rate",
  index_sentiment: "Index sentiment",
  nifty_pcr: "NIFTY PCR",
  nifty_return_7d: "NIFTY 7d return",
  nifty_return_14d: "NIFTY 14d return",
  nifty_rsi_14: "NIFTY RSI(14)",
  nifty_realized_vol_20d: "NIFTY realized vol",
  nifty_ma20_distance_pct: "Distance from 20d MA",
  constituent_momentum_7d: "Constituent momentum (7d)",
  days_to_monthly_expiry: "Days to expiry",
  is_budget_week: "Budget week",
  is_results_season: "Results season",
};

export function labelFactor(key: string): string {
  return FACTOR_LABELS[key] || key.replace(/_/g, " ").replace(/\^2/g, "²");
}
