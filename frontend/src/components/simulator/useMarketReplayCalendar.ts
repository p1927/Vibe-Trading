import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type MarketReplayCalendarDay } from "@/lib/api";
import { localIsoDate } from "@/lib/utils";

const WINDOW_DAYS = 120;

function recentDates(end: string, count: number): string[] {
  const out: string[] = [];
  const cursor = new Date(end + "T00:00:00");
  for (let i = 0; i < count; i++) {
    out.push(localIsoDate(cursor));
    cursor.setDate(cursor.getDate() - 1);
  }
  return out.reverse();
}

export interface WeekGridCell {
  date: string;
}

/** Lays a flat run of consecutive ISO dates out into Mon..Sun week columns, plus month-label
 * positions — the same "weeks as columns, weekday/month text around the grid" shape India's
 * `StockHistoryCoveragePanel` `YearHeatmap` uses, so non-India's day-presence grid can show the
 * same date/week orientation instead of an unlabeled flat wrap of cells. */
function buildWeekGrid(dates: string[]): {
  weeks: (WeekGridCell | null)[][];
  monthLabels: { col: number; label: string }[];
} {
  if (dates.length === 0) return { weeks: [], monthLabels: [] };

  const start = new Date(dates[0] + "T00:00:00");
  const end = new Date(dates[dates.length - 1] + "T00:00:00");
  const startDow = (start.getDay() + 6) % 7; // Mon=0..Sun=6
  const gridStart = new Date(start);
  gridStart.setDate(gridStart.getDate() - startDow);
  const endDow = (end.getDay() + 6) % 7;
  const gridEnd = new Date(end);
  gridEnd.setDate(gridEnd.getDate() + (6 - endDow));

  const totalDays = Math.round((gridEnd.getTime() - gridStart.getTime()) / 86_400_000) + 1;
  const totalWeeks = totalDays / 7;
  const dateSet = new Set(dates);

  const weeks: (WeekGridCell | null)[][] = [];
  const monthLabels: { col: number; label: string }[] = [];
  const seenMonth = new Set<string>();
  let lastLabelCol = -Infinity;
  const MIN_MONTH_LABEL_GAP_COLS = 3;

  for (let w = 0; w < totalWeeks; w++) {
    const week: (WeekGridCell | null)[] = [];
    for (let d = 0; d < 7; d++) {
      const cell = new Date(gridStart);
      cell.setDate(cell.getDate() + w * 7 + d);
      const key = localIsoDate(cell);
      week.push(dateSet.has(key) ? { date: key } : null);
      if (d === 0) {
        const mkey = `${cell.getFullYear()}-${cell.getMonth()}`;
        if (!seenMonth.has(mkey)) {
          seenMonth.add(mkey);
          if (w - lastLabelCol >= MIN_MONTH_LABEL_GAP_COLS) {
            monthLabels.push({ col: w, label: cell.toLocaleString(undefined, { month: "short" }) });
            lastLabelCol = w;
          }
        }
      }
    }
    weeks.push(week);
  }
  return { weeks, monthLabels };
}

/** Shared data-loading for the per-country `market_ticks` day calendar — used by both
 * `MarketReplayPanel` (replay arm/seek) and `MarketCoveragePanel` (backfill-only), which
 * render the same day-presence grid for two different purposes.
 *
 * Pages a `WINDOW_DAYS`-day window at a time via the backend's `lookback_days`/`before` params
 * (see `history_data.py`'s `/replay/calendar` route) rather than fetching everything —
 * `goOlder`/`goNewer`/`goToLatest` mirror India's `SimulatorReplayCalendar` year-paging, just
 * server-side (round-tripping per page) instead of client-side windowing over an
 * already-fully-loaded array, since non-India history lives in `market_ticks` behind a
 * deliberately-bounded query, not a fully-loaded parquet read. */
export function useMarketReplayCalendar(country: string) {
  const [days, setDays] = useState<MarketReplayCalendarDay[]>([]);
  const [indices, setIndices] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backfillingDay, setBackfillingDay] = useState<string | null>(null);
  // null = the default "most recent WINDOW_DAYS days" window; otherwise an ISO date the window
  // ends on, for paging further back in history.
  const [windowEnd, setWindowEnd] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getMarketReplayCalendar(country, { lookbackDays: WINDOW_DAYS, before: windowEnd ?? undefined })
      .then((res) => {
        setDays(res.days ?? []);
        setIndices(res.indices ?? []);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [country, windowEnd]);

  useEffect(() => {
    load();
  }, [load]);

  // Reset to the latest window on a country switch, so paging state from one market's tab
  // doesn't carry over silently into another's.
  useEffect(() => {
    setWindowEnd(null);
  }, [country]);

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

  const grid = useMemo(() => {
    const end =
      windowEnd ?? (days.length > 0 ? days.reduce((max, d) => (d.date > max ? d.date : max), days[0].date) : localIsoDate(new Date()));
    return recentDates(end, WINDOW_DAYS);
  }, [days, windowEnd]);

  const { weeks, monthLabels } = useMemo(() => buildWeekGrid(grid), [grid]);

  const isLatestWindow = windowEnd === null;

  const goOlder = useCallback(() => {
    const cursor = new Date(grid[0] + "T00:00:00");
    cursor.setDate(cursor.getDate() - 1);
    setWindowEnd(localIsoDate(cursor));
  }, [grid]);

  const goNewer = useCallback(() => {
    setWindowEnd((current) => {
      if (current === null) return current;
      const cursor = new Date(current + "T00:00:00");
      cursor.setDate(cursor.getDate() + WINDOW_DAYS);
      const today = localIsoDate(new Date());
      return cursor >= new Date(today + "T00:00:00") ? null : localIsoDate(cursor);
    });
  }, []);

  const goToLatest = useCallback(() => setWindowEnd(null), []);

  return {
    days,
    indices,
    loading,
    error,
    backfillingDay,
    reload: load,
    backfill,
    grid,
    weeks,
    monthLabels,
    windowDays: WINDOW_DAYS,
    isLatestWindow,
    goOlder,
    goNewer,
    goToLatest,
  };
}
