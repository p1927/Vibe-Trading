import { useEffect, useState } from "react";
import { api, type IndexPlaygroundContext, type PlaygroundTrigger } from "@/lib/api";

type CascadeRules = NonNullable<IndexPlaygroundContext["cascade_downstream"]>;

type PlaygroundCacheEntry = {
  headlines: PlaygroundTrigger[];
  events: PlaygroundTrigger[];
  factorNews: Record<string, PlaygroundTrigger[]>;
  cascadeDownstream: CascadeRules | null;
};

const cache = new Map<string, PlaygroundCacheEntry>();
const inflight = new Map<string, Promise<PlaygroundCacheEntry>>();

function cacheKey(ticker: string, asOf: string | undefined): string {
  return `${ticker.toUpperCase()}|${(asOf || "").slice(0, 19)}`;
}

async function fetchPlaygroundContext(ticker: string, key: string): Promise<PlaygroundCacheEntry> {
  if (inflight.has(key)) {
    return inflight.get(key)!;
  }

  const promise = api
    .getIndexPlaygroundContext(ticker)
    .then((res) => {
      const entry: PlaygroundCacheEntry = {
        headlines: res.context?.headlines ?? [],
        events: res.context?.events ?? [],
        factorNews: res.context?.factor_news ?? {},
        cascadeDownstream: res.context?.cascade_downstream ?? null,
      };
      cache.set(key, entry);
      return entry;
    })
    .finally(() => {
      inflight.delete(key);
    });

  inflight.set(key, promise);
  return promise;
}

/**
 * The one `getIndexPlaygroundContext` read (D306: the workbench and the explorer each fetched it).
 * A failed request sets `error` — an empty list from a failure must not read as "no news today".
 */
export function usePlaygroundContext(ticker: string, asOf: string | undefined) {
  const [entry, setEntry] = useState<PlaygroundCacheEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const sym = (ticker || "NIFTY").toUpperCase();
    const key = cacheKey(sym, asOf);
    let cancelled = false;

    const cached = cache.get(key);
    if (cached) {
      setEntry(cached);
      setError(null);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    setError(null);
    void fetchPlaygroundContext(sym, key)
      .then((next) => {
        if (!cancelled) setEntry(next);
      })
      .catch((caught) => {
        if (cancelled) return;
        setEntry(null);
        setError(caught instanceof Error ? caught.message : "request failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker, asOf]);

  return {
    headlines: entry?.headlines ?? [],
    events: entry?.events ?? [],
    factorNews: entry?.factorNews ?? {},
    cascadeDownstream: entry?.cascadeDownstream ?? null,
    loading,
    error,
  };
}

export function invalidatePlaygroundContext(ticker: string, asOf: string | undefined) {
  cache.delete(cacheKey(ticker, asOf));
}
