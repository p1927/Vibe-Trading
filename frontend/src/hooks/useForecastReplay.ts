import { useCallback, useEffect, useState } from "react";
import { api, type ForecastReplayParams, type ForecastReplayResponse } from "@/lib/api";
import { isAbortError } from "@/lib/abort";

/** Fetches the replay bundle (no request while `sources` is empty: the server reads that as "every source"); a param change aborts the stale request and keeps the old bundle until the new one lands. */
export function useForecastReplay(p: ForecastReplayParams) {
  const [bundle, setBundle] = useState<ForecastReplayResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const refetch = useCallback(() => setNonce((n) => n + 1), []);
  const { ticker, start, end, horizon, provenance } = p;
  const sourcesKey = p.sources.join(",");

  useEffect(() => {
    if (!sourcesKey) {
      setBundle(null);
      setLoading(false);
      return;
    }
    const ctl = new AbortController();
    setLoading(true);
    setError(null);
    api
      .getForecastReplay({ ticker, sources: sourcesKey ? sourcesKey.split(",") : [], start, end, horizon, provenance }, ctl.signal)
      .then((b) => setBundle(b))
      .catch((e) => {
        if (!isAbortError(e)) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!ctl.signal.aborted) setLoading(false);
      });
    return () => ctl.abort();
  }, [ticker, sourcesKey, start, end, horizon, provenance, nonce]);

  return { bundle, loading, error, refetch };
}
