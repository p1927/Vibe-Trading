import { api, type MarketBundleEntry, type MarketBundleResponse } from "@/lib/api";

/** Shared session cache + in-flight dedupe for `getMarketBundle(country)` — both
 * `GlobalMarketsPanel` and `SectorIndicesPanel` fetch the same bundle for a market switch, so
 * without this they'd double the requests they're meant to be collapsing. TTL matches
 * `GlobalMarketsPanel`'s existing 30s poll interval: a switch back to an already-viewed market
 * within that window renders instantly from cache instead of re-fetching. */
const CACHE_TTL_MS = 30_000;

type BundleData = MarketBundleResponse["data"];

const cache = new Map<string, { data: BundleData; fetchedAt: number }>();
const inflight = new Map<string, Promise<BundleData>>();

export function getCachedMarketBundle(country: string): BundleData | null {
  const entry = cache.get(country);
  if (!entry || Date.now() - entry.fetchedAt > CACHE_TTL_MS) return null;
  return entry.data;
}

export function fetchMarketBundle(country: string, opts?: { force?: boolean }): Promise<BundleData> {
  if (!opts?.force) {
    const cached = getCachedMarketBundle(country);
    if (cached) return Promise.resolve(cached);
    const existing = inflight.get(country);
    if (existing) return existing;
  }
  const promise = api
    .getMarketBundle(country)
    .then((res) => {
      cache.set(country, { data: res.data, fetchedAt: Date.now() });
      inflight.delete(country);
      return res.data;
    })
    .catch((err) => {
      inflight.delete(country);
      throw err;
    });
  inflight.set(country, promise);
  return promise;
}

/** Test-only: clear cached/in-flight state so cases in the same test file don't leak into each
 * other (the cache is module-scoped and otherwise persists across tests in one run). */
export function __resetMarketBundleCacheForTests(): void {
  cache.clear();
  inflight.clear();
}

/** Look up one index's entry by name across a bundle's headline + sector lists — the frontend's
 * own index-name lists (`market_registry` indices, `sector_indices()`'s kind="sector" entries)
 * don't necessarily partition the same way the bundle's headline/sector split does (e.g. US's
 * registry headline set is SPX/NASDAQ/DOW/SOX while the bundle's "headline" kind also includes
 * VIX), so callers should look a name up across both rather than assume it's in one or the
 * other. */
export function findBundleEntry(data: BundleData, name: string): MarketBundleEntry | undefined {
  const needle = name.trim().toUpperCase();
  return data.headline.find((e) => e.name === needle) ?? data.sectors.find((e) => e.name === needle);
}
