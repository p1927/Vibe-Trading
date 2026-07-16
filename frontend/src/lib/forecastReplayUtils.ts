import type {
  IndexBacktestDailyEval,
  IndexPredictionHistoryRow,
} from "@/lib/api";

export interface PricePoint {
  date: string;
  close: number;
}

export interface ForecastSnapshot {
  date: string;
  spot: number;
  expectedReturnPct: number;
  rangeLow?: number;
  rangeHigh?: number;
}

export interface LiveForecastInput {
  asOf?: string;
  spot: number;
  expectedReturnPct: number;
  rangeLow?: number | null;
  rangeHigh?: number | null;
  simulatedReturnPct?: number | null;
}

export interface ForwardPaths {
  predicted: PricePoint[];
  actual: PricePoint[];
  bandHigh: PricePoint[];
  bandLow: PricePoint[];
  horizonTarget: number;
  maturedActualReturnPct: number | null;
}

function isoDay(raw: string | undefined): string {
  return String(raw ?? "").slice(0, 10);
}

function addBusinessDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  let added = 0;
  while (added < days) {
    d.setUTCDate(d.getUTCDate() + 1);
    const dow = d.getUTCDay();
    if (dow !== 0 && dow !== 6) added += 1;
  }
  return d.toISOString().slice(0, 10);
}

export function mergePriceSeries(
  sources: Array<Array<{ date?: string; close?: number | null }>>,
): PricePoint[] {
  const map = new Map<string, number>();
  for (const src of sources) {
    for (const p of src) {
      const d = isoDay(p.date);
      const c = Number(p.close);
      if (d && Number.isFinite(c)) map.set(d, c);
    }
  }
  return [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, close]) => ({ date, close }));
}

export function buildForecastIndex(
  ledger: IndexPredictionHistoryRow[],
  backtestEvals: IndexBacktestDailyEval[],
  live?: LiveForecastInput,
): Map<string, ForecastSnapshot> {
  const map = new Map<string, ForecastSnapshot>();

  for (const ev of backtestEvals) {
    const d = isoDay(ev.date);
    if (!d || ev.spot == null || ev.predicted_return_pct == null) continue;
    map.set(d, {
      date: d,
      spot: ev.spot,
      expectedReturnPct: ev.predicted_return_pct,
    });
  }

  for (const row of ledger) {
    const d = isoDay(row.predicted_at);
    if (!d) continue;
    map.set(d, {
      date: d,
      spot: row.spot_at_prediction,
      expectedReturnPct: row.expected_return_pct,
      rangeLow: row.range_low,
      rangeHigh: row.range_high,
    });
  }

  if (live?.spot != null && Number.isFinite(live.spot)) {
    const d = isoDay(live.asOf) || isoDay(new Date().toISOString());
    const ret =
      live.simulatedReturnPct != null && Number.isFinite(live.simulatedReturnPct)
        ? live.simulatedReturnPct
        : live.expectedReturnPct;
    map.set(d, {
      date: d,
      spot: live.spot,
      expectedReturnPct: ret,
      rangeLow: live.rangeLow ?? undefined,
      rangeHigh: live.rangeHigh ?? undefined,
    });
  }

  return map;
}

export function resolveForecastForDate(
  date: string,
  index: Map<string, ForecastSnapshot>,
  prices: PricePoint[],
): ForecastSnapshot | null {
  const direct = index.get(date);
  if (direct) return direct;

  const sorted = [...index.keys()].sort();
  let fallback: ForecastSnapshot | null = null;
  for (const d of sorted) {
    if (d <= date) fallback = index.get(d) ?? fallback;
    else break;
  }
  if (fallback) return fallback;

  const spot = prices.find((p) => p.date === date)?.close;
  if (spot == null) return null;
  return { date, spot, expectedReturnPct: 0 };
}

export function buildForwardPaths(
  anchor: ForecastSnapshot,
  horizonDays: number,
  prices: PricePoint[],
  lastPriceDate: string,
): ForwardPaths {
  const h = Math.max(horizonDays, 1);
  const anchorIdx = prices.findIndex((p) => p.date === anchor.date);
  const anchorClose =
    (anchorIdx >= 0 ? prices[anchorIdx]?.close : undefined) ?? anchor.spot;
  const horizonTarget = anchorClose * (1 + anchor.expectedReturnPct / 100);

  const predicted: PricePoint[] = [];
  const actual: PricePoint[] = [{ date: anchor.date, close: anchorClose }];
  const bandHigh: PricePoint[] = [];
  const bandLow: PricePoint[] = [];

  const hasBand =
    anchor.rangeLow != null &&
    anchor.rangeHigh != null &&
    Number.isFinite(anchor.rangeLow) &&
    Number.isFinite(anchor.rangeHigh);

  for (let day = 0; day <= h; day += 1) {
    const t = day / h;
    const level = anchorClose + (horizonTarget - anchorClose) * t;
    let date: string;
    if (anchorIdx >= 0 && prices[anchorIdx + day]) {
      date = prices[anchorIdx + day].date;
    } else if (anchorIdx >= 0 && day > 0) {
      date = addBusinessDays(anchor.date, day);
    } else {
      date = day === 0 ? anchor.date : addBusinessDays(anchor.date, day);
    }

    predicted.push({ date, close: level });

    if (hasBand) {
      bandLow.push({
        date,
        close: anchorClose + ((anchor.rangeLow as number) - anchorClose) * t,
      });
      bandHigh.push({
        date,
        close: anchorClose + ((anchor.rangeHigh as number) - anchorClose) * t,
      });
    }

    if (day > 0 && date <= lastPriceDate) {
      const row = prices.find((p) => p.date === date);
      if (row) actual.push({ date: row.date, close: row.close });
    }
  }

  let maturedActualReturnPct: number | null = null;
  if (anchorIdx >= 0 && anchorIdx + h < prices.length) {
    const endClose = prices[anchorIdx + h].close;
    maturedActualReturnPct = ((endClose - anchorClose) / anchorClose) * 100;
  }

  return {
    predicted,
    actual,
    bandHigh,
    bandLow,
    horizonTarget,
    maturedActualReturnPct,
  };
}
