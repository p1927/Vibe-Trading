/** Literature-backed notes for macro factors (Nifty 14d context). */
export interface FactorResearchNote {
  expectedDirection: "positive" | "negative" | "mixed" | "context";
  summary: string;
  caveat?: string;
}

export const FACTOR_RESEARCH_NOTES: Record<string, FactorResearchNote> = {
  oil_brent: {
    expectedDirection: "mixed",
    summary: "India is a net importer; theory says oil spikes hurt margins and INR, but empirically the Nifty–oil link varies by horizon and regime.",
    caveat: "Short-run correlation often positive during global growth rallies; quarterly studies show negative coefficient more often.",
  },
  oil_wti: {
    expectedDirection: "mixed",
    summary: "Same transmission as Brent via import bill, inflation, and USD/INR pressure.",
  },
  usd_inr: {
    expectedDirection: "mixed",
    summary: "Short-run Nifty and INR often move together via FII flows; long-run FX and equities can diverge as fundamentals dominate.",
    caveat: "2022–2024 NARDL work: FX volatility shocks hurt Nifty in the short run.",
  },
  gold: {
    expectedDirection: "mixed",
    summary: "Safe-haven demand vs real yields; negative gold shocks sometimes coincide with risk-on equity moves.",
  },
  sp500: {
    expectedDirection: "positive",
    summary: "Global risk appetite channel; EM indices co-move with US equities especially on FII-driven days.",
  },
  us_10y: {
    expectedDirection: "mixed",
    summary: "Higher yields tighten global financial conditions; impact on India is via FII rotation and USD strength.",
  },
  india_vix: {
    expectedDirection: "negative",
    summary: "Fear gauge; VIX spikes align with drawdowns, but VIX alone is a weak standalone return predictor.",
    caveat: "VIX×RSI composite shows contrarian signal in some ARDL studies.",
  },
  fii_net_5d: {
    expectedDirection: "positive",
    summary: "FII flows Granger-cause Nifty returns in multiple Indian studies; primary liquidity driver for large caps.",
  },
  dii_net_5d: {
    expectedDirection: "context",
    summary: "DII often offsets FII; direct regression coefficient frequently insignificant vs FII.",
  },
  fii_fut_long_short_ratio: {
    expectedDirection: "positive",
    summary: "Positioning indicator; rising long/short can precede trend continuation but is noisy intraday.",
  },
  nifty_pe: {
    expectedDirection: "mixed",
    summary: "Valuation mean-reversion over quarters; short 14d horizon linkage is weak.",
  },
  cpi_yoy_proxy: {
    expectedDirection: "negative",
    summary: "Higher inflation erodes real returns and can force tighter policy.",
  },
  repo_rate: {
    expectedDirection: "negative",
    summary: "Higher policy rates tighten financial conditions; RBI surprises move financials first.",
  },
  index_sentiment: {
    expectedDirection: "positive",
    summary: "FinBERT/news aggregate; literature shows unstable short-horizon predictability—use as regime input, not precise return.",
  },
  nifty_pcr: {
    expectedDirection: "mixed",
    summary: "High PCR often interpreted as support (put writing); contrarian at extremes.",
  },
  nifty_return_7d: {
    expectedDirection: "mixed",
    summary: "Short-term momentum/mean-reversion feature; sign depends on training window.",
  },
  nifty_return_14d: {
    expectedDirection: "mixed",
    summary: "Autoregressive return feature for horizon alignment.",
  },
  nifty_rsi_14: {
    expectedDirection: "mixed",
    summary: "Overbought/oversold oscillator; contrarian at extremes in some regimes.",
  },
  nifty_realized_vol_20d: {
    expectedDirection: "negative",
    summary: "Elevated vol associates with risk-off; used as risk regime flag.",
  },
  nifty_ma20_distance_pct: {
    expectedDirection: "mixed",
    summary: "Trend vs mean-reversion distance from 20d MA.",
  },
  constituent_momentum_7d: {
    expectedDirection: "positive",
    summary: "Bottom-up price trend rollup; aligns with constituent-driven index modeling literature.",
  },
  days_to_monthly_expiry: {
    expectedDirection: "context",
    summary: "Expiry-week pinning and rollover flows; range-bound scenarios more likely.",
  },
  is_budget_week: {
    expectedDirection: "context",
    summary: "Event dummy for Union budget volatility.",
  },
  is_results_season: {
    expectedDirection: "context",
    summary: "Earnings cluster calendar flag.",
  },
};

export function researchNoteForFactor(key: string): FactorResearchNote | undefined {
  const base = key.split(" ")[0]?.replace(/\^2$/, "") ?? key;
  return FACTOR_RESEARCH_NOTES[base];
}
