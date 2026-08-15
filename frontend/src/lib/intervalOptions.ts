/**
 * Dropdown option vocabularies + helpers for the stock-simulator
 * recording config UI. See
 * `docs/superpowers/plans/2026-08-15-per-category-recording-intervals.md`
 * and
 * `docs/superpowers/plans/2026-08-15-nifty50-equity-recording.md`
 * for the product rationale.
 *
 * Vocabularies:
 *
 * - `INTERVAL_OPTIONS` — the seconds-list shown next to each REST poll
 *   row (option chain, market depth, full quote). The first entry,
 *   `"off"`, maps to `0` and tells the scheduler to never fire that
 *   category.
 * - `WS_THROTTLE_OPTIONS` — Hz-based, shown on the WebSocket LTP row.
 *   WS is event-driven (not a poll), so the dropdown is a max-rate.
 *   `"unlimited"` maps to `0` and tells `MaxRateLimiter` to never drop.
 * - `HISTORICAL_INTERVAL_OPTIONS` — Indmoney's `/market/historical/{interval}`
 *   accepted values, rendered with user-friendly labels. Each entry
 *   carries its `max_lookback_days` so the UI can clamp the user's
 *   lookback window to the per-interval API maximum (7d for minutes,
 *   14d for hours, 365d for daily+).
 * - `HISTORICAL_LOOKBACK_OPTIONS` — the user's pick of how far back to
 *   pull. The recorder clamps to the interval's max at request time.
 * - `OFF_KEY` / `UNLIMITED_KEY` — the string sentinels the helpers
 *   recognise.
 */

export type IntervalKey =
  | "off"
  | "1s"
  | "5s"
  | "10s"
  | "20s"
  | "30s"
  | "1m"
  | "2m"
  | "5m"
  | "10m"
  | "15m"
  | "20m"
  | "30m"
  | "45m"
  | "1h"
  | "2h"
  | "5h"
  | "1d";

export interface IntervalOption {
  key: IntervalKey;
  value: number; // seconds (0 for "off")
  label: string;
}

export const OFF_KEY: IntervalKey = "off";

export const INTERVAL_OPTIONS: readonly IntervalOption[] = [
  { key: "off", value: 0, label: "Off" },
  { key: "1s", value: 1, label: "1 second" },
  { key: "5s", value: 5, label: "5 seconds" },
  { key: "10s", value: 10, label: "10 seconds" },
  { key: "20s", value: 20, label: "20 seconds" },
  { key: "30s", value: 30, label: "30 seconds" },
  { key: "1m", value: 60, label: "1 minute" },
  { key: "2m", value: 120, label: "2 minutes" },
  { key: "5m", value: 300, label: "5 minutes" },
  { key: "10m", value: 600, label: "10 minutes" },
  { key: "15m", value: 900, label: "15 minutes" },
  { key: "20m", value: 1200, label: "20 minutes" },
  { key: "30m", value: 1800, label: "30 minutes" },
  { key: "45m", value: 2700, label: "45 minutes" },
  { key: "1h", value: 3600, label: "1 hour" },
  { key: "2h", value: 7200, label: "2 hours" },
  { key: "5h", value: 18000, label: "5 hours" },
  { key: "1d", value: 86400, label: "1 day" },
] as const;

export type WsThrottleKey = "unlimited" | "50hz" | "10hz" | "4hz" | "1hz";

export interface WsThrottleOption {
  key: WsThrottleKey;
  hz: number; // 0 means unlimited
  label: string;
}

export const UNLIMITED_KEY: WsThrottleKey = "unlimited";

export const WS_THROTTLE_OPTIONS: readonly WsThrottleOption[] = [
  { key: "unlimited", hz: 0, label: "Unlimited (every tick)" },
  { key: "50hz", hz: 50, label: "50 / sec" },
  { key: "10hz", hz: 10, label: "10 / sec" },
  { key: "4hz", hz: 4, label: "4 / sec" },
  { key: "1hz", hz: 1, label: "1 / sec" },
] as const;

/**
 * Indmoney `/market/historical/{interval}` accepted values, rendered
 * with user-friendly labels. Each entry carries its `max_lookback_days`
 * — the UI clamps the user's lookback window to this maximum before
 * sending it to the recorder (the recorder also clamps defensively,
 * so a misclamped request never produces a 400). Keys are user-facing;
 * `apiValue` is what gets sent to the backend.
 */
export type HistoricalIntervalKey =
  | "1m" | "5m" | "15m" | "30m"
  | "1h" | "2h" | "4h"
  | "1d" | "1w" | "1mo";

export interface HistoricalIntervalOption {
  key: HistoricalIntervalKey;
  apiValue: string;
  label: string;
  max_lookback_days: number;
}

export const HISTORICAL_INTERVAL_OPTIONS: readonly HistoricalIntervalOption[] = [
  { key: "1m",  apiValue: "1minute",  label: "1 minute",     max_lookback_days: 7 },
  { key: "5m",  apiValue: "5minute",  label: "5 minutes",    max_lookback_days: 7 },
  { key: "15m", apiValue: "15minute", label: "15 minutes",   max_lookback_days: 7 },
  { key: "30m", apiValue: "30minute", label: "30 minutes",   max_lookback_days: 7 },
  { key: "1h",  apiValue: "60minute", label: "1 hour",       max_lookback_days: 14 },
  { key: "2h",  apiValue: "120minute",label: "2 hours",      max_lookback_days: 14 },
  { key: "4h",  apiValue: "240minute",label: "4 hours",      max_lookback_days: 14 },
  { key: "1d",  apiValue: "1day",     label: "1 day",        max_lookback_days: 365 },
  { key: "1w",  apiValue: "1week",    label: "1 week",       max_lookback_days: 365 },
  { key: "1mo", apiValue: "1month",   label: "1 month",      max_lookback_days: 365 },
] as const;

export type HistoricalLookbackKey =
  | "1d" | "5d" | "7d" | "14d" | "30d" | "90d" | "180d" | "365d";

export interface HistoricalLookbackOption {
  key: HistoricalLookbackKey;
  days: number;
  label: string;
}

export const HISTORICAL_LOOKBACK_OPTIONS: readonly HistoricalLookbackOption[] = [
  { key: "1d",   days: 1,   label: "1 day" },
  { key: "5d",   days: 5,   label: "5 days" },
  { key: "7d",   days: 7,   label: "7 days (1 week)" },
  { key: "14d",  days: 14,  label: "14 days (2 weeks)" },
  { key: "30d",  days: 30,  label: "30 days (1 month)" },
  { key: "90d",  days: 90,  label: "90 days (3 months)" },
  { key: "180d", days: 180, label: "180 days (6 months)" },
  { key: "365d", days: 365, label: "365 days (1 year)" },
] as const;

/**
 * Defaults applied when the page first loads. The 30s full-quote
 * default reflects that OHLC/volume updates less often than the
 * per-strike greeks (which move tick-to-tick); the 4Hz WS default is
 * "enough granularity to replay the curve at 1x" without flooding
 * Timescale.
 */
export const DEFAULT_CATEGORY_INTERVALS: Record<string, number> = {
  option_chain: 10,
  market_depth: 10,
  full_quote: 30,
};

/**
 * Per-equity (NIFTY50) REST poll defaults. Defaults are *off* — the
 * user opts in. The recorder applies these as shared cadences across
 * every selected equity (the user's explicit choice; no per-equity
 * overrides in this slice).
 */
export const DEFAULT_EQUITY_INTERVALS: Record<string, number> = {
  equity_option_chain: 0,
  equity_market_depth: 0,
  equity_full_quote: 0,
};

/**
 * Default historical-candle config. 1d candles for the last 30 days
 * matches the typical "give the replay enough context" goal without
 * pulling a year's worth of data by accident.
 */
export const DEFAULT_HISTORICAL_CONFIG: {
  interval: HistoricalIntervalKey;
  lookback_days: HistoricalLookbackKey;
} = {
  interval: "1d",
  lookback_days: "30d",
};

export const DEFAULT_WS_THROTTLE_HZ: number = 4;

const _INTERVAL_BY_KEY: Record<string, number> = Object.fromEntries(
  INTERVAL_OPTIONS.map((o) => [o.key, o.value]),
);
const _INTERVAL_BY_VALUE: Record<number, IntervalKey> = (() => {
  const out: Record<number, IntervalKey> = {};
  for (const o of INTERVAL_OPTIONS) {
    out[o.value] = o.key;
  }
  return out;
})();

const _HZ_BY_KEY: Record<string, number> = Object.fromEntries(
  WS_THROTTLE_OPTIONS.map((o) => [o.key, o.hz]),
);
const _HZ_BY_VALUE: Record<number, WsThrottleKey> = (() => {
  const out: Record<number, WsThrottleKey> = {};
  for (const o of WS_THROTTLE_OPTIONS) {
    out[o.hz] = o.key;
  }
  return out;
})();

/** Resolve a select value (IntervalKey) to a seconds number. Returns
 * `null` for the sentinel "off" (or any unknown key) so callers can
 * decide whether to fall back to a default. */
export function intervalKeyToSeconds(key: string | null | undefined): number | null {
  if (!key) return null;
  if (key === OFF_KEY) return 0;
  const v = _INTERVAL_BY_KEY[key];
  return typeof v === "number" ? v : null;
}

/** Inverse of `intervalKeyToSeconds`. Falls back to OFF_KEY for
 * seconds values that aren't in the option list (so the user sees the
 * real configured cadence as "Off" rather than a wrong row). */
export function secondsToIntervalKey(seconds: number | null | undefined): IntervalKey {
  if (seconds === null || seconds === undefined || seconds <= 0) return OFF_KEY;
  return _INTERVAL_BY_VALUE[seconds] ?? OFF_KEY;
}

/** Resolve a WS select value (WsThrottleKey) to an hz number. Returns
 * `null` for "unlimited" (meaning the throttle is disabled). */
export function wsKeyToHz(key: string | null | undefined): number | null {
  if (!key || key === UNLIMITED_KEY) return null;
  const v = _HZ_BY_KEY[key];
  return typeof v === "number" && v > 0 ? v : null;
}

/** Inverse of `wsKeyToHz`. */
export function hzToWsKey(hz: number | null | undefined): WsThrottleKey {
  if (hz === null || hz === undefined || hz <= 0) return UNLIMITED_KEY;
  return _HZ_BY_VALUE[hz] ?? UNLIMITED_KEY;
}

// Historical interval / lookback lookup tables + helpers.

const _HISTORICAL_BY_KEY: Record<string, HistoricalIntervalOption> =
  Object.fromEntries(
    HISTORICAL_INTERVAL_OPTIONS.map((o) => [o.key, o]),
  );

/** Resolve a user-facing interval key to its API value (e.g. "1d" → "1day"). */
export function historicalIntervalKeyToApi(key: HistoricalIntervalKey | string): string {
  const opt = _HISTORICAL_BY_KEY[key];
  return opt ? opt.apiValue : String(key);
}

/** Resolve a user-facing interval key to its max-lookback window (days). */
export function historicalIntervalMaxLookbackDays(
  key: HistoricalIntervalKey | string,
): number {
  const opt = _HISTORICAL_BY_KEY[key];
  return opt ? opt.max_lookback_days : 7;
}

const _LOOKBACK_BY_KEY: Record<string, number> = Object.fromEntries(
  HISTORICAL_LOOKBACK_OPTIONS.map((o) => [o.key, o.days]),
);
const _LOOKBACK_BY_DAYS: Record<number, HistoricalLookbackKey> = (() => {
  const out: Record<number, HistoricalLookbackKey> = {};
  for (const o of HISTORICAL_LOOKBACK_OPTIONS) {
    out[o.days] = o.key;
  }
  return out;
})();

/** Resolve a user-facing lookback key to its day count. */
export function historicalLookbackKeyToDays(key: HistoricalLookbackKey | string): number {
  const v = _LOOKBACK_BY_KEY[key];
  return typeof v === "number" ? v : 1;
}

/** Inverse of `historicalLookbackKeyToDays`. Falls back to the closest
 * matching key (rounded down so we never invent a larger window than
 * the user picked). */
export function daysToHistoricalLookbackKey(
  days: number,
): HistoricalLookbackKey {
  if (days <= 0) return "1d";
  // Direct hit
  if (_LOOKBACK_BY_DAYS[days]) return _LOOKBACK_BY_DAYS[days];
  // Largest key ≤ days
  let best: HistoricalLookbackKey = "1d";
  for (const o of HISTORICAL_LOOKBACK_OPTIONS) {
    if (o.days <= days) best = o.key;
  }
  return best;
}

/** Clamp a requested lookback window to its interval's API max. The
 * recorder also clamps defensively — this is the UI-side guard so the
 * user sees the actual window they're sending. */
export function clampHistoricalLookbackDays(
  intervalKey: HistoricalIntervalKey | string,
  requestedDays: number,
): number {
  const max = historicalIntervalMaxLookbackDays(intervalKey);
  if (requestedDays <= 0) return Math.min(1, max);
  return Math.min(requestedDays, max);
}
