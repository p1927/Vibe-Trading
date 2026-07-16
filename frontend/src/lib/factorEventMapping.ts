import type { IndexUpcomingEvent, PlaygroundTrigger } from "@/lib/api";

const NEWS_KEYWORD_RULES: Array<{ keywords: string[]; factors: string[] }> = [
  { keywords: ["war", "conflict", "missile", "geopolit"], factors: ["oil_brent", "india_vix", "gold"] },
  { keywords: ["oil", "crude", "opec", "brent", "wti"], factors: ["oil_brent", "oil_wti"] },
  { keywords: ["fii", "foreign fund", "outflow", "inflow"], factors: ["fii_net_5d"] },
  { keywords: ["rbi", "repo", "mpc", "rate hike", "rate cut"], factors: ["repo_rate"] },
  { keywords: ["fed", "wall street", "s&p", "treasury", "us market"], factors: ["sp500", "us_10y"] },
  { keywords: ["results", "earnings", "quarter", "profit", "guidance"], factors: ["index_sentiment"] },
];

const EVENT_TYPE_FACTORS: Record<string, { factor: string; shock: number }> = {
  monthly_expiry: { factor: "india_vix", shock: 4 },
  rbi_policy: { factor: "repo_rate", shock: 5 },
  union_budget: { factor: "repo_rate", shock: 6 },
  results: { factor: "index_sentiment", shock: 4 },
  earnings: { factor: "index_sentiment", shock: 4 },
};

export function resolveHeadlineTrigger(title: string): {
  primaryFactor: string;
  shockPct: number;
  factors: string[];
} {
  const lower = title.toLowerCase();
  const factors: string[] = [];
  for (const rule of NEWS_KEYWORD_RULES) {
    if (rule.keywords.some((kw) => lower.includes(kw))) {
      factors.push(...rule.factors);
    }
  }
  const unique = [...new Set(factors)];
  return {
    primaryFactor: unique[0] ?? "index_sentiment",
    shockPct: 5,
    factors: unique.length ? unique : ["index_sentiment"],
  };
}

export function resolveUpcomingEvent(ev: IndexUpcomingEvent): {
  primaryFactor: string;
  shockPct: number;
} {
  const etype = String(ev.event_type || "");
  const hit = EVENT_TYPE_FACTORS[etype];
  if (hit) return { primaryFactor: hit.factor, shockPct: hit.shock };
  if (etype.includes("result") || etype.includes("earning")) {
    return { primaryFactor: "index_sentiment", shockPct: 4 };
  }
  return { primaryFactor: "index_sentiment", shockPct: 3 };
}

export function triggerToWorkbenchState(trigger: PlaygroundTrigger): {
  primaryFactor: string;
  shockPct: number;
  eventPresetId?: string;
} {
  return {
    primaryFactor: trigger.primary_factor || "index_sentiment",
    shockPct: trigger.suggested_shock_pct ?? 5,
    eventPresetId: trigger.event_preset_id,
  };
}

export const FACTOR_LABELS: Record<string, string> = {
  oil_brent: "Brent crude",
  oil_wti: "WTI crude",
  usd_inr: "USD/INR",
  india_vix: "India VIX",
  sp500: "S&P 500",
  us_10y: "US 10Y yield",
  fii_net_5d: "FII net (5d)",
  dii_net_5d: "DII net (5d)",
  nifty_pcr: "Nifty PCR",
  repo_rate: "Repo rate",
  gold: "Gold",
  index_sentiment: "Index sentiment",
  constituent_momentum_7d: "Constituent momentum",
  fii_fut_long_short_ratio: "FII fut long/short",
};

export function factorLabel(key: string): string {
  return FACTOR_LABELS[key] || key.replace(/_/g, " ");
}
