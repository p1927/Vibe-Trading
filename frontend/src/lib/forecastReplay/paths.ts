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
    return p?.matured && p.error_pct != null && p.error_pts != null
      ? [{ issue_day: r.issue_day, error_pct: p.error_pct, error_pts: p.error_pts, in_band: p.in_band }]
      : [];
  });
}
