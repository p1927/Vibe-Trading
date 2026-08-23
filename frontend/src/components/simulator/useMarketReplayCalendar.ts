import { useCallback, useEffect, useState } from "react";
import { api, type MarketReplayCalendarDay } from "@/lib/api";

/** Shared data-loading for the per-country `market_ticks` day calendar — used by both
 * `MarketReplayPanel` (replay arm/seek) and `MarketCoveragePanel` (backfill-only), which
 * render the same day-presence grid for two different purposes. */
export function useMarketReplayCalendar(country: string) {
  const [days, setDays] = useState<MarketReplayCalendarDay[]>([]);
  const [indices, setIndices] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backfillingDay, setBackfillingDay] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getMarketReplayCalendar(country)
      .then((res) => {
        setDays(res.days ?? []);
        setIndices(res.indices ?? []);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [country]);

  useEffect(() => {
    load();
  }, [load]);

  const backfill = useCallback(
    async (date: string) => {
      setBackfillingDay(date);
      setError(null);
      try {
        await api.backfillMarketTicks(country);
        load();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Backfill failed");
      } finally {
        setBackfillingDay(null);
      }
    },
    [country, load],
  );

  return { days, indices, loading, error, backfillingDay, reload: load, backfill };
}
