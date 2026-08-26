/**
 * Options Lab — API contract types, preset strategy catalog, and pure
 * display-only helpers.
 *
 * All numeric finance math (payoff curves, Black-Scholes pricing, Greeks,
 * scenario grids) is computed by the backend (`POST /options/payoff`).
 * The ONLY client-side math living here is display-only: strike rounding,
 * percent <-> fraction conversion of user input, grid downsampling, and
 * bid/ask spread width.
 */

// ---------------------------------------------------------------------------
// API contract — POST /options/payoff
// ---------------------------------------------------------------------------

export type OptionType = "call" | "put";

/** One option leg. `qty` is a signed integer: positive = long, negative = short. */
export interface OptionLeg {
  option_type: OptionType;
  strike: number;
  qty: number;
  /** Optional per-share premium; when omitted the backend prices via Black-Scholes. */
  premium?: number;
}

export interface OptionsPayoffRequest {
  legs: OptionLeg[];
  entry_spot: number;
  expiry_days: number;
  /** Annual fraction, default 0.05. */
  risk_free_rate?: number;
  /** Annualized IV fraction, default 0.3. */
  volatility?: number;
  /** Contract multiplier, default 1. */
  multiplier?: number;
  /** Fraction in [0, 1), default 0.001. */
  commission_rate?: number;
  spot_min?: number;
  spot_max?: number;
  /** Integer 21-501, default 121. */
  spot_points?: number;
  /** IV scenarios as annualized fractions (max 9). */
  scenario_iv_values?: number[];
}

export interface BreakevenInterval {
  start: number;
  end: number | null;
}

export interface OptionsPayoffSummary {
  net_premium: number;
  entry_commission: number;
  entry_cost: number;
  entry_side: "debit" | "credit" | "flat";
  breakevens: number[];
  breakeven_intervals: BreakevenInterval[];
  max_profit: number | null;
  max_loss: number | null;
  profit_unbounded: boolean;
  loss_unbounded: boolean;
}

export interface OptionsGreeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
}

export interface ExpiryCurve {
  spot: number[];
  pnl: number[];
}

export interface ScenarioGrid {
  iv_values: number[];
  spot: number[];
  /** One row per iv_values entry, each row aligned with spot. */
  pnl: number[][];
}

export interface OptionsPayoffResponse {
  status: "ok";
  inputs: Record<string, unknown>;
  summary: OptionsPayoffSummary;
  greeks: OptionsGreeks;
  expiry_curve: ExpiryCurve;
  scenario_grid: ScenarioGrid;
  limitations: string[];
}

// ---------------------------------------------------------------------------
// API contract — GET /options/chain
// ---------------------------------------------------------------------------

/** One contract row; any market field may be null (render "–"). */
export interface OptionsContractRow {
  contract_symbol: string;
  strike: number;
  last_price: number | null;
  bid: number | null;
  ask: number | null;
  volume: number | null;
  open_interest: number | null;
  implied_volatility: number | null;
  in_the_money: boolean;
  expiration: number;
}

export interface OptionsChainData {
  ticker: string;
  expiration: number;
  /** Epoch seconds. */
  expirations: number[];
  /** Underlying spot at fetch time — only India rows carry this today (the
   * US/Yahoo tool doesn't surface it); `undefined` there, not `null`. */
  underlying_ltp?: number | null;
  /** Exchange lot size / contract multiplier — India only, sourced from
   * `stock_simulator`'s `UNDERLYING_META` (`null` for sources/underlyings
   * that don't carry it yet, e.g. indmoney/openalgo or unlisted equities);
   * `undefined` for the US/Yahoo tool, which has no such concept. */
  lot_size?: number | null;
  calls_count: number;
  puts_count: number;
  calls: OptionsContractRow[];
  puts: OptionsContractRow[];
}

export interface OptionsChainResponse {
  ok: boolean;
  market: string;
  source: string;
  data: OptionsChainData;
  /** Present on `{ok: false}` error envelopes (HTTP 400/502 throw as ApiError). */
  error?: string;
}

/** Which connector serves India options data — no automatic fallback between
 * them; an unavailable source errors rather than silently trying another.
 * `stock_simulator` (default) goes through stock_history API directly — the
 * recorded/replay archive, no bid/ask in its fields. `indmoney`/`openalgo`
 * are live, pinned to one named broker, with real top-of-book bid/ask. */
export type IndiaOptionsSource = "stock_simulator" | "indmoney" | "openalgo";

/** GET /options/india/underlyings response — known indexes always;
 * `equities` is `null` for live vendor sources (not offered, vs. genuinely
 * empty), populated only for `stock_simulator` (a cheap local listing). */
export interface IndiaUnderlyingsResponse {
  ok: boolean;
  data: {
    indexes: string[];
    equities: string[] | null;
    /** Lot size per known index (NIFTY/BANKNIFTY/SENSEX), from
     * `stock_simulator`'s `UNDERLYING_META` — not populated per equity. */
    lot_sizes?: Record<string, number>;
  };
  error?: string;
}

// ---------------------------------------------------------------------------
// API contract — GET /options/research (India-only ranked strategies)
// ---------------------------------------------------------------------------

/** One auto-ranked candidate strategy; leg/scoring field shapes come straight
 * from the options_research pipeline and are rendered generically. */
export interface RankedOptionStrategy {
  name?: string;
  tier?: string;
  score?: number;
  pop?: number;
  max_profit?: number | null;
  max_loss?: number | null;
  breakevens?: number[];
  legs?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface IndiaOptionsResearchData {
  underlying: string;
  as_of: string;
  instrument_type: string;
  expiry: string;
  spot: number | null;
  atm_strike: number | null;
  pcr: number | null;
  ranked_strategies: RankedOptionStrategy[];
  recommended: Record<string, unknown>;
  charges: Record<string, unknown>;
}

export interface IndiaOptionsResearchResponse {
  ok: boolean;
  data: IndiaOptionsResearchData;
  error?: string;
}

// ---------------------------------------------------------------------------
// API contract — GET /options/india/selector (module 5: risk-adjusted
// candidate selector for a target profit)
// ---------------------------------------------------------------------------

/** One leg of a selector candidate — shape comes straight from
 * candidate_generator/combo_builder and is rendered generically. */
export interface SelectorLeg {
  option_type: "CE" | "PE" | string;
  side: "BUY" | "SELL" | string;
  strike: number;
  quantity?: number;
  lots?: number;
  lot_size?: number;
  price?: number;
  entry_premium?: number;
  [key: string]: unknown;
}

export interface SelectorEventRisk {
  label?: string;
  date?: string;
  category?: string;
  [key: string]: unknown;
}

/** One scored candidate — always present regardless of `meets_target`
 * (`select_candidates` never filters or drops a candidate silently). */
export interface SelectorCandidate {
  name: string;
  legs: SelectorLeg[];
  max_profit: number | null;
  max_loss: number | null;
  probability_of_profit: number;
  expected_pnl: number;
  entry_iv: number;
  risk_reward_ratio: number | null;
  pop_per_risk: number | null;
  meets_target: boolean;
  event_risks: SelectorEventRisk[];
  rationale?: string | null;
}

export type SelectorRankBy = "risk_reward" | "pop_per_risk";

export interface SelectorResponseData {
  ticker: string;
  expiry_date: string;
  expiry_days: number;
  horizon_days: number;
  underlying_ltp: number;
  target_profit: number;
  rank_by: SelectorRankBy;
  distribution_type: string;
  candidates: SelectorCandidate[];
}

export interface SelectorResponse extends SelectorResponseData {
  ok: boolean;
  error?: string;
}

// ---------------------------------------------------------------------------
// API contract — GET /options/india/pop-overlay (module 4: Monte Carlo
// probability-of-profit overlaid on the full chain for a chosen horizon)
// ---------------------------------------------------------------------------

/** Mirrors `pop_engine.PopResult` verbatim (`asdict`d on the backend). */
export interface PopResult {
  horizon_days: number;
  n_paths: number;
  probability_of_profit: number;
  expected_pnl: number;
  pnl_p10: number;
  pnl_p50: number;
  pnl_p90: number;
  worst_5pct_pnl: number;
  best_5pct_pnl: number;
  distribution_type: "physical" | "risk_neutral";
  liquidity_discount_applied: boolean;
}

/** One chain row (call or put) with its PoP scored for the selected horizon.
 * `pop` is `null` (with `pop_skip_reason` set) when the leg couldn't be
 * scored — e.g. missing premium — never silently dropped from the list. */
export interface PopOverlayRow {
  strike: number;
  option_type: "CE" | "PE" | string;
  last_price?: number | null;
  bid?: number | null;
  ask?: number | null;
  implied_volatility?: number | null;
  open_interest?: number | null;
  volume?: number | null;
  pop: PopResult | null;
  pop_skip_reason?: string;
  [key: string]: unknown;
}

export interface IvTermStructurePoint {
  days_to_expiry: number;
  atm_iv: number;
}

export interface PopOverlayEventRisk {
  label?: string;
  date?: string;
  category?: string;
  [key: string]: unknown;
}

export interface PopOverlayResponse {
  ok: boolean;
  error?: string;
  ticker: string;
  source: string;
  expiry_date: string;
  expiry_days: number;
  horizon_days: number;
  underlying_ltp: number;
  distribution_type: string;
  apply_liquidity_discount: boolean;
  iv_decay_half_life_days: number | null;
  iv_decay_floor_fraction: number;
  use_iv_term_structure: boolean;
  iv_term_structure_points: IvTermStructurePoint[] | null;
  forecast_quantiles: { p10: number; p50: number; p90: number };
  overlay: PopOverlayRow[];
  event_risks: PopOverlayEventRisk[];
}

// ---------------------------------------------------------------------------
// Execution advisor (module 7) — advisory-only position monitoring
// ---------------------------------------------------------------------------

export type ExecutionAdvisorFsmState = "in_trade" | "trailing" | "exit_pending" | string;
export type ExecutionAdvisorAction = "hold" | "tighten_stop" | "exit" | string;

/** One FSM step for one open position — never an order mutation, only a
 * recommendation. Mirrors `advise_position()`'s return dict verbatim. */
export interface ExecutionAdvisorAdvisory {
  symbol: string;
  strategy_group_id: string | null;
  fsm_state: ExecutionAdvisorFsmState;
  action: ExecutionAdvisorAction;
  reason: string;
  recommended_stop_price: number | null;
  entry_price: number;
  entry_direction: "bullish" | "bearish" | string;
  entry_confidence: number | null;
  live_confidence: number | null;
  confidence_ema: number | null;
  consecutive_flip_count: number;
  ltp: number;
}

export interface ExecutionAdvisorResponse {
  ok: boolean;
  error?: string;
  count?: number;
  advisories: ExecutionAdvisorAdvisory[];
  grouped: Record<string, ExecutionAdvisorAdvisory[]>;
}

// ---------------------------------------------------------------------------
// UI parameter model (percent convention)
// ---------------------------------------------------------------------------

/**
 * Builder parameters as the USER types them. ONE convention, kept consistent
 * across the page: volatility, risk-free rate and commission rate are entered
 * as PERCENTS (30 means 30%) and converted to fractions only when the request
 * body is built.
 */
export interface OptionsLabParams {
  entry_spot: number;
  expiry_days: number;
  /** Percent: 30 = 30% annualized IV. Sent as 0.30. */
  volatility_pct: number;
  /** Percent: 5 = 5%. Sent as 0.05. */
  risk_free_rate_pct: number;
  /** Contract multiplier; 100 = standard US equity contract. */
  multiplier: number;
  /** Percent: 0.1 = 0.1%. Sent as 0.001. */
  commission_rate_pct: number;
}

export const DEFAULT_PARAMS: OptionsLabParams = {
  entry_spot: 100,
  expiry_days: 30,
  volatility_pct: 30,
  risk_free_rate_pct: 5,
  multiplier: 100,
  commission_rate_pct: 0.1,
};

export function pctToFraction(pct: number): number {
  return pct / 100;
}

export function fractionToPct(fraction: number): number {
  return fraction * 100;
}

/**
 * Validate inputs against the backend contract and build the request body.
 * Returns null when any input is out of contract bounds (the page then skips
 * the debounced call). Display-only guard — the backend stays authoritative.
 */
export function buildPayoffRequest(
  legs: OptionLeg[],
  p: OptionsLabParams,
): OptionsPayoffRequest | null {
  if (!Number.isFinite(p.entry_spot) || p.entry_spot <= 0) return null;
  if (!Number.isFinite(p.expiry_days) || p.expiry_days < 0) return null;
  if (!Number.isFinite(p.volatility_pct) || p.volatility_pct <= 0) return null;
  if (!Number.isFinite(p.risk_free_rate_pct)) return null;
  if (!Number.isFinite(p.multiplier) || p.multiplier <= 0) return null;
  if (!Number.isFinite(p.commission_rate_pct) || p.commission_rate_pct < 0 || p.commission_rate_pct >= 100) {
    return null;
  }
  if (legs.length === 0) return null;
  for (const leg of legs) {
    if (leg.option_type !== "call" && leg.option_type !== "put") return null;
    if (!Number.isFinite(leg.strike) || leg.strike <= 0) return null;
    if (!Number.isInteger(leg.qty) || leg.qty === 0) return null;
    if (leg.premium !== undefined && (!Number.isFinite(leg.premium) || leg.premium < 0)) return null;
  }
  return {
    legs: legs.map((l) => ({
      option_type: l.option_type,
      strike: l.strike,
      qty: l.qty,
      ...(l.premium !== undefined ? { premium: l.premium } : {}),
    })),
    entry_spot: p.entry_spot,
    expiry_days: p.expiry_days,
    risk_free_rate: pctToFraction(p.risk_free_rate_pct),
    volatility: pctToFraction(p.volatility_pct),
    multiplier: p.multiplier,
    commission_rate: pctToFraction(p.commission_rate_pct),
  };
}

// ---------------------------------------------------------------------------
// Preset strategy catalog
// ---------------------------------------------------------------------------

export interface StrategyPreset {
  id: string;
  /** i18n key of the display name (options.presets.*). */
  nameKey: string;
  /** Generate legs parameterized by the underlying spot S. */
  buildLegs: (spot: number) => OptionLeg[];
}

/** Strike grid step by price level — keeps preset strikes on listed-like increments. */
export function niceStrikeStep(price: number): number {
  if (price >= 200) return 5;
  if (price >= 50) return 2.5;
  if (price >= 20) return 1;
  if (price >= 5) return 0.5;
  return 0.25;
}

/** Round a raw price to a "nice" listed-style strike. Returns 0 for non-positive input. */
export function roundToNiceStrike(price: number): number {
  if (!Number.isFinite(price) || price <= 0) return 0;
  const step = niceStrikeStep(price);
  const rounded = Math.max(step, Math.round(price / step) * step);
  return Number(rounded.toFixed(4));
}

function k(spot: number, factor: number): number {
  return roundToNiceStrike(spot * factor);
}

/**
 * Twelve canonical presets, parameterized by spot S. Strike ratios follow the
 * S x {0.9, 0.95, 1.0, 1.05, 1.1} grid (rounded to nice strikes).
 */
export const STRATEGY_PRESETS: StrategyPreset[] = [
  {
    id: "long_call",
    nameKey: "options.presets.longCall",
    buildLegs: (s) => [{ option_type: "call", strike: k(s, 1), qty: 1 }],
  },
  {
    id: "long_put",
    nameKey: "options.presets.longPut",
    buildLegs: (s) => [{ option_type: "put", strike: k(s, 1), qty: 1 }],
  },
  {
    id: "bull_call_spread",
    nameKey: "options.presets.bullCallSpread",
    buildLegs: (s) => [
      { option_type: "call", strike: k(s, 0.95), qty: 1 },
      { option_type: "call", strike: k(s, 1.05), qty: -1 },
    ],
  },
  {
    id: "bear_put_spread",
    nameKey: "options.presets.bearPutSpread",
    buildLegs: (s) => [
      { option_type: "put", strike: k(s, 1.05), qty: 1 },
      { option_type: "put", strike: k(s, 0.95), qty: -1 },
    ],
  },
  {
    id: "bull_put_spread",
    nameKey: "options.presets.bullPutSpread",
    buildLegs: (s) => [
      { option_type: "put", strike: k(s, 1.05), qty: -1 },
      { option_type: "put", strike: k(s, 0.95), qty: 1 },
    ],
  },
  {
    id: "bear_call_spread",
    nameKey: "options.presets.bearCallSpread",
    buildLegs: (s) => [
      { option_type: "call", strike: k(s, 0.95), qty: -1 },
      { option_type: "call", strike: k(s, 1.05), qty: 1 },
    ],
  },
  {
    id: "long_straddle",
    nameKey: "options.presets.longStraddle",
    buildLegs: (s) => [
      { option_type: "call", strike: k(s, 1), qty: 1 },
      { option_type: "put", strike: k(s, 1), qty: 1 },
    ],
  },
  {
    id: "long_strangle",
    nameKey: "options.presets.longStrangle",
    buildLegs: (s) => [
      { option_type: "call", strike: k(s, 1.05), qty: 1 },
      { option_type: "put", strike: k(s, 0.95), qty: 1 },
    ],
  },
  {
    id: "iron_condor",
    nameKey: "options.presets.ironCondor",
    buildLegs: (s) => [
      { option_type: "put", strike: k(s, 0.9), qty: 1 },
      { option_type: "put", strike: k(s, 0.95), qty: -1 },
      { option_type: "call", strike: k(s, 1.05), qty: -1 },
      { option_type: "call", strike: k(s, 1.1), qty: 1 },
    ],
  },
  {
    id: "iron_butterfly",
    nameKey: "options.presets.ironButterfly",
    buildLegs: (s) => [
      { option_type: "put", strike: k(s, 1), qty: -1 },
      { option_type: "call", strike: k(s, 1), qty: -1 },
      { option_type: "put", strike: k(s, 0.9), qty: 1 },
      { option_type: "call", strike: k(s, 1.1), qty: 1 },
    ],
  },
  {
    id: "call_butterfly",
    nameKey: "options.presets.callButterfly",
    buildLegs: (s) => [
      { option_type: "call", strike: k(s, 0.9), qty: 1 },
      { option_type: "call", strike: k(s, 1), qty: -2 },
      { option_type: "call", strike: k(s, 1.1), qty: 1 },
    ],
  },
  {
    id: "ratio_call_spread",
    nameKey: "options.presets.ratioCallSpread",
    buildLegs: (s) => [
      { option_type: "call", strike: k(s, 1), qty: 1 },
      { option_type: "call", strike: k(s, 1.1), qty: -2 },
    ],
  },
];

export function getPreset(id: string): StrategyPreset | undefined {
  return STRATEGY_PRESETS.find((p) => p.id === id);
}

export function buildPresetLegs(presetId: string, spot: number): OptionLeg[] | null {
  const preset = getPreset(presetId);
  if (!preset || !Number.isFinite(spot) || spot <= 0) return null;
  return preset.buildLegs(spot);
}

// ---------------------------------------------------------------------------
// Display-only helpers (chain table, heatmap downsampling)
// ---------------------------------------------------------------------------

/**
 * Even-stride index selection that ALWAYS keeps the first and last element.
 * Used to downsample the scenario spot grid for heatmap display.
 */
export function downsampleIndices(total: number, maxPoints: number): number[] {
  if (total <= 0) return [];
  if (total <= maxPoints || maxPoints < 2) {
    return Array.from({ length: total }, (_, i) => i);
  }
  const stride = (total - 1) / (maxPoints - 1);
  const indices: number[] = [];
  for (let i = 0; i < maxPoints; i++) {
    indices.push(Math.round(i * stride));
  }
  indices[indices.length - 1] = total - 1;
  return indices;
}

/** Bid/ask spread width as a percent of mid. Null when uncomputable. */
export function bidAskSpreadPct(bid: number | null, ask: number | null): number | null {
  if (bid === null || ask === null || !Number.isFinite(bid) || !Number.isFinite(ask)) return null;
  const mid = (bid + ask) / 2;
  if (mid <= 0) return null;
  return ((ask - bid) / mid) * 100;
}

/** Index of the row whose strike is nearest to the reference spot (-1 when empty). */
export function nearestStrikeIndex(rows: OptionsContractRow[], spot: number): number {
  if (rows.length === 0 || !Number.isFinite(spot)) return -1;
  let best = 0;
  let bestDist = Math.abs(rows[0].strike - spot);
  for (let i = 1; i < rows.length; i++) {
    const dist = Math.abs(rows[i].strike - spot);
    if (dist < bestDist) {
      best = i;
      bestDist = dist;
    }
  }
  return best;
}

/** Locale date label for an epoch-seconds expiration (e.g. "Aug 15, 2026"). */
export function expirationLabel(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Format an IV fraction (0.30) as a percent label ("30%"). */
export function ivLabel(iv: number): string {
  const pct = fractionToPct(iv);
  return `${Number(pct.toFixed(2)).toLocaleString()}%`;
}
