// Pure record -> series helpers. Dates always come from the server; nothing here adds days.
import type { ReplayPoint, ReplayRecord } from "./types";

export interface PathVertex {
  date: string;
  level: number;
}

/** Spot at the issue day, then each recorded p50. `approximate` = straight line, not recorded. */
export function recordPath(r: ReplayRecord): { vertices: PathVertex[]; approximate: boolean } {
  return {
    vertices: [
      { date: r.issue_day, level: r.spot_at_issue },
      ...r.points.map((p) => ({ date: p.target_date, level: p.p50_level })),
    ],
    approximate: r.approximate_path,
  };
}

/** The chart's bars: the union of the actual dates and every plotted path date, ascending (its logical index). */
export function barDates(actualDates: string[], records: ReplayRecord[]): string[] {
  return [...new Set([...actualDates, ...records.flatMap((r) => recordPath(r).vertices.map((v) => v.date))])].sort();
}

/**
 * Logical bar range of the chart's time scale: `lead` bars before the anchor through the furthest recorded
 * point of the plotted records (+`pad` bars). Bars are the union of the actual dates and every plotted path date,
 * which is how the chart indexes them. Implausible records never widen it (unless they are all there is).
 * Empty records fall back to `fallbackBars` after the anchor; null when the anchor is not on the scale.
 */
export function focusRange(
  actualDates: string[], records: ReplayRecord[], anchorDay: string, fallbackBars: number, lead = 30, pad = 2,
): { from: number; to: number } | null {
  const dates = barDates(actualDates, records);
  const idx = dates.indexOf(anchorDay);
  if (idx < 0) return null;
  const sane = records.filter((r) => r.meta?.return_implausible !== true);
  const shown = sane.length ? sane : records;
  const furthest = shown.flatMap((r) => r.points.map((p) => p.target_date)).sort().pop();
  const to = furthest ? dates.indexOf(furthest) : idx + fallbackBars;
  return { from: Math.max(0, idx - lead), to: Math.max(to, idx) + pad };
}

/**
 * What the chart's visible range was fitted for. A refetch of the same view (new array identities, one more close,
 * different last price) yields the same key, so the user's pan/zoom is kept; a new anchor, source set, horizon,
 * week focus or window start yields a new one and re-fits.
 */
export function fitKey(
  anchorDay: string, focusDays: string[], sourceIds: string[], horizon: number, windowStart: string,
): string {
  return [anchorDay, [...focusDays].sort().join(","), [...new Set(sourceIds)].sort().join(","), horizon, windowStart].join("|");
}

/**
 * Whether the effect re-fits the visible range or puts the user's range back. `kept` is only trustworthy once the
 * fit for this key has been laid out: a range read in the same frame as `setVisibleLogicalRange` is still the
 * pre-layout one, and restoring it clobbers the fit (the chart then opened wide on some loads, QA4).
 */
export function rangeAction(key: string, fitted: { key: string; settled: boolean }): "fit" | "keep" {
  return fitted.key === key && fitted.settled ? "keep" : "fit";
}

/** p10/p90 at each point that has both; null when the record carries no band. */
export function band(r: ReplayRecord): { date: string; p10: number; p90: number }[] | null {
  const b = r.points.flatMap((p) =>
    p.p10_level != null && p.p90_level != null ? [{ date: p.target_date, p10: p.p10_level, p90: p.p90_level }] : [],
  );
  return b.length ? b : null;
}

export function dayOutcomes(r: ReplayRecord): Pick<
  ReplayPoint,
  "horizon_days" | "target_date" | "matured" | "error_pts" | "error_pct" | "in_band" | "direction_hit"
>[] {
  return r.points.map(({ horizon_days, target_date, matured, error_pts, error_pct, in_band, direction_hit }) => ({
    horizon_days, target_date, matured, error_pts, error_pct, in_band, direction_hit,
  }));
}

/** Group by ISO week (Monday start) of issue_day; groups and members keep input order. */
export function weekGroups(records: ReplayRecord[]): { week_start: string; records: ReplayRecord[] }[] {
  const groups = new Map<string, ReplayRecord[]>();
  for (const r of records) {
    const d = new Date(`${r.issue_day}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); // calendar bucketing, not a forecast date
    const key = d.toISOString().slice(0, 10);
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  return [...groups].map(([week_start, records]) => ({ week_start, records }));
}

/** One entry per record whose point at `horizon` has matured. */
export function errorSeries(records: ReplayRecord[], horizon: number) {
  return records.flatMap((r) => {
    const p = r.points.find((q) => q.horizon_days === horizon);
    return r.meta?.return_implausible !== true && p?.matured && p.error_pct != null && p.error_pts != null
      ? [{ issue_day: r.issue_day, error_pct: p.error_pct, error_pts: p.error_pts, in_band: p.in_band }]
      : [];
  });
}
