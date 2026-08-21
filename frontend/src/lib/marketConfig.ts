/**
 * Options Lab market registry — single source of truth for the `"us" |
 * "india_equity"` union (previously duplicated independently as
 * `OptionsLabMarket` in OptionsLab.tsx and `ChainMarket` in
 * OptionsChainTable.tsx) plus everything that varies per market: currency
 * symbol/locale for display, default ticker, and the initial spot guess
 * shown before a real chain loads.
 *
 * Deliberately does NOT hold a lot-size/multiplier lookup table — that value
 * comes from the backend (`stock_simulator`'s `UNDERLYING_META`, threaded
 * through `/options/chain`'s `lot_size` field) so the exchange's lot size
 * stays a server-owned fact instead of a frontend copy that goes stale when
 * an exchange revises it.
 */

export type MarketId = "us" | "india_equity";

export interface MarketConfig {
  id: MarketId;
  currencySymbol: string;
  currencyCode: string;
  /** Locale for `Intl.NumberFormat` — only affects grouping/decimal style. */
  locale: string;
  defaultTicker: string;
  /** Rough placeholder spot before a real chain loads (refined by the
   * chain's `underlying_ltp` once available). */
  defaultSpotGuess: number;
  /** i18n key for this market's display name (used in the market selector
   * and interpolated into "{{market}} Options Chain"-style titles). */
  labelKey: string;
}

export const MARKETS: Record<MarketId, MarketConfig> = {
  us: {
    id: "us",
    currencySymbol: "$",
    currencyCode: "USD",
    locale: "en-US",
    defaultTicker: "AAPL",
    defaultSpotGuess: 100,
    labelKey: "options.chain.marketUs",
  },
  india_equity: {
    id: "india_equity",
    currencySymbol: "₹",
    currencyCode: "INR",
    locale: "en-IN",
    defaultTicker: "NIFTY",
    defaultSpotGuess: 24000,
    labelKey: "options.chain.marketIndia",
  },
};

/** Prefix a numeric value with the market's currency symbol. Returns "–"
 * for null/non-finite input, matching the dash convention used throughout
 * the Options Lab display helpers. */
export function formatCurrency(
  market: MarketId,
  value: number | null | undefined,
  opts: Intl.NumberFormatOptions = { maximumFractionDigits: 2 },
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  const { locale, currencySymbol } = MARKETS[market];
  return `${currencySymbol}${value.toLocaleString(locale, opts)}`;
}
