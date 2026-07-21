import { useEffect, useState } from "react";
import { api, type PlaygroundTrigger } from "@/lib/api";

type PlaygroundCacheEntry = {
  headlines: PlaygroundTrigger[];
  events: PlaygroundTrigger[];
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

export function usePlaygroundContext(ticker: string, asOf: string | undefined) {
  const [headlines, setHeadlines] = useState<PlaygroundTrigger[]>([]);
  const [events, setEvents] = useState<PlaygroundTrigger[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const sym = (ticker || "NIFTY").toUpperCase();
    const key = cacheKey(sym, asOf);
    let cancelled = false;

    const cached = cache.get(key);
    if (cached) {
      setHeadlines(cached.headlines);
      setEvents(cached.events);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    void fetchPlaygroundContext(sym, key)
      .then((entry) => {
        if (cancelled) return;
        setHeadlines(entry.headlines);
        setEvents(entry.events);
      })
      .catch(() => {
        if (cancelled) return;
        setHeadlines([]);
        setEvents([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [ticker, asOf]);

  return { headlines, events, loading };
}

export function invalidatePlaygroundContext(ticker: string, asOf: string | undefined) {
  cache.delete(cacheKey(ticker, asOf));
}
