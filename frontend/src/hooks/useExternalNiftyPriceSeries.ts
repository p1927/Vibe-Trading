import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { NIFTY_CLOSE_FACTOR, niftyCloseSeries, pivotFactorHistoryWide } from "@/lib/factorHistoryUtils";

const DEFAULT_DAYS = 120;

/** NIFTY OHLCV-like closes for external-predictions replay charts (independent of analysis backtest). */
export function useExternalNiftyPriceSeries(enabled: boolean, horizonDays: number) {
  const [loading, setLoading] = useState(false);
  const [series, setSeries] = useState<Array<{ date: string; close: number }>>([]);

  const lookbackDays = useMemo(() => Math.max(DEFAULT_DAYS, horizonDays * 6), [horizonDays]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const res = await api.getIndexFactorHistory("NIFTY", lookbackDays, [NIFTY_CLOSE_FACTOR]);
        if (cancelled) return;
        const wide = pivotFactorHistoryWide(res.series ?? []);
        const rows = niftyCloseSeries(wide)
          .filter((row) => row.close != null && Number.isFinite(row.close))
          .map((row) => ({ date: row.date, close: row.close as number }));
        setSeries(rows);
      } catch {
        if (!cancelled) setSeries([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, lookbackDays]);

  return { priceSeries: series, priceLoading: loading };
}
