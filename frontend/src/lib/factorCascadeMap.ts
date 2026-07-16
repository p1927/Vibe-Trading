/** Downstream factors moved when a primary macro driver shocks (heuristic cascade). */
export const CASCADE_DOWNSTREAM: Record<string, Array<{ factor: string; multiplier: number; mode: string }>> = {
  oil_brent: [
    { factor: "usd_inr", multiplier: 0.15, mode: "relative" },
    { factor: "india_vix", multiplier: 0.15, mode: "absolute" },
    { factor: "gold", multiplier: 0.05, mode: "relative" },
  ],
  oil_wti: [
    { factor: "usd_inr", multiplier: 0.12, mode: "relative" },
    { factor: "india_vix", multiplier: 0.12, mode: "absolute" },
  ],
  usd_inr: [
    { factor: "india_vix", multiplier: 0.08, mode: "absolute" },
    { factor: "fii_net_5d", multiplier: -0.02, mode: "relative" },
  ],
  fii_net_5d: [
    { factor: "usd_inr", multiplier: 0.1, mode: "relative" },
    { factor: "india_vix", multiplier: 0.12, mode: "absolute" },
    { factor: "sp500", multiplier: 0.08, mode: "relative" },
  ],
  dii_net_5d: [
    { factor: "fii_net_5d", multiplier: -0.05, mode: "relative" },
    { factor: "india_vix", multiplier: -0.05, mode: "absolute" },
  ],
  sp500: [
    { factor: "fii_net_5d", multiplier: 0.1, mode: "relative" },
    { factor: "india_vix", multiplier: -0.08, mode: "absolute" },
    { factor: "usd_inr", multiplier: -0.05, mode: "relative" },
  ],
  us_10y: [
    { factor: "usd_inr", multiplier: 0.06, mode: "relative" },
    { factor: "sp500", multiplier: -0.05, mode: "relative" },
    { factor: "india_vix", multiplier: 0.05, mode: "absolute" },
  ],
  india_vix: [
    { factor: "fii_net_5d", multiplier: -0.08, mode: "relative" },
    { factor: "index_sentiment", multiplier: -0.1, mode: "relative" },
  ],
  repo_rate: [
    { factor: "usd_inr", multiplier: 0.04, mode: "relative" },
    { factor: "india_vix", multiplier: 0.1, mode: "absolute" },
  ],
  index_sentiment: [{ factor: "india_vix", multiplier: -0.06, mode: "absolute" }],
  nifty_pcr: [
    { factor: "india_vix", multiplier: 0.05, mode: "absolute" },
    { factor: "fii_net_5d", multiplier: -0.05, mode: "relative" },
  ],
};

export const PINNED_CAUSAL_FACTORS = [
  "fii_net_5d",
  "dii_net_5d",
  "oil_brent",
  "india_vix",
  "usd_inr",
  "nifty_pcr",
  "sp500",
  "us_10y",
] as const;
