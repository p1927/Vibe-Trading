import type { IndexFactorHistoryPoint } from "@/lib/api";

/** Detect long format `{ date, factor, value }` vs wide `{ date, oil_brent, ... }`. */
export function isLongFactorSeries(series: IndexFactorHistoryPoint[]): boolean {
  return series.some((row) => row.factor != null && row.value != null);
}

/** Pivot API series to wide rows for timeline charts. */
export function pivotFactorHistoryWide(
  series: IndexFactorHistoryPoint[],
): IndexFactorHistoryPoint[] {
  if (!series.length || !isLongFactorSeries(series)) {
    return series;
  }
  const byDate = new Map<string, IndexFactorHistoryPoint>();
  for (const row of series) {
    const date = String(row.date ?? "").slice(0, 10);
    const factor = row.factor;
    if (!date || !factor) continue;
    const bucket = byDate.get(date) ?? { date };
    bucket[factor] = row.value;
    byDate.set(date, bucket);
  }
  return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));
}

/** Extract one factor's time series from wide or long rows. */
export function factorSeriesValues(
  series: IndexFactorHistoryPoint[],
  factor: string,
): Array<{ date: string; value: number | null }> {
  if (isLongFactorSeries(series)) {
    return series
      .filter((r) => r.factor === factor)
      .map((r) => ({ date: String(r.date), value: r.value ?? null }))
      .sort((a, b) => a.date.localeCompare(b.date));
  }
  return series
    .map((r) => {
      const v = r[factor];
      return {
        date: String(r.date),
        value: typeof v === "number" && Number.isFinite(v) ? v : null,
      };
    })
    .sort((a, b) => a.date.localeCompare(b.date));
}

export const MACRO_DRIFT_FACTORS = [
  "oil_brent",
  "india_vix",
  "sp500",
  "us_10y",
  "usd_inr",
  "fii_net_5d",
  "gold",
  "nifty_close",
] as const;

export const DERIVATIVES_FACTORS = [
  "nifty_pcr",
  "fii_net_5d",
  "dii_net_5d",
  "fii_fut_long_short_ratio",
] as const;

export const NIFTY_CLOSE_FACTOR = "nifty_close" as const;

/** Nifty 50 closes aligned to wide factor rows (sorted by date). */
export function niftyCloseSeries(
  wide: IndexFactorHistoryPoint[],
): Array<{ date: string; close: number | null }> {
  return wide.map((row) => {
    const v = row[NIFTY_CLOSE_FACTOR];
    return {
      date: String(row.date).slice(0, 10),
      close: typeof v === "number" && Number.isFinite(v) ? v : null,
    };
  });
}

/** % change from first valid Nifty close in the window (for overlay on drift charts). */
export function niftyPctChangeSeries(
  wide: IndexFactorHistoryPoint[],
): { dates: string[]; pctChange: (number | null)[]; baseClose: number | null } {
  const closes = niftyCloseSeries(wide);
  let baseClose: number | null = null;
  for (const row of closes) {
    if (row.close != null && row.close > 0) {
      baseClose = row.close;
      break;
    }
  }
  const dates = closes.map((r) => r.date);
  const pctChange = closes.map((r) => {
    if (r.close == null || baseClose == null || baseClose <= 0) return null;
    return ((r.close / baseClose) - 1) * 100;
  });
  return { dates, pctChange, baseClose };
}

export function fmtNiftyLevel(v: number): string {
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function niftyCloseAt(wide: IndexFactorHistoryPoint[], index: number): number | null {
  const row = wide[index];
  if (!row) return null;
  const v = row[NIFTY_CLOSE_FACTOR];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
