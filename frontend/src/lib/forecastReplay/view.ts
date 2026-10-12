// Pure view helpers for the replay panel. Forecast dates always come from the server; the only
// calendar arithmetic here is the fetch window (`shiftDays`), never a forecast or target date.
import type { ReplayPoint, ReplayRecord, ReplaySource } from "./types";

/** Records that carry a point at `horizon`; the whole path of such a record is still drawn. */
export function forHorizon(records: ReplayRecord[], horizon: number): ReplayRecord[] {
  return records.filter((r) => r.points.some((p) => p.horizon_days === horizon));
}

export function pointAt(r: ReplayRecord, horizon: number): ReplayPoint | undefined {
  return r.points.find((p) => p.horizon_days === horizon);
}

/** Sorted unique issue days. */
export function anchorDays(records: ReplayRecord[]): string[] {
  return [...new Set(records.map((r) => r.issue_day))].sort();
}

/** Calendar shift for the fetch window only. */
export function shiftDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Percent move from spot to the p50 at `horizon`, null when the record has no such point. */
export function forecastReturnPct(r: ReplayRecord, horizon: number): number | null {
  const p = pointAt(r, horizon);
  return p && r.spot_at_issue > 0 ? (p.p50_level / r.spot_at_issue - 1) * 100 : null;
}

export type Maturity = "matured" | "partial" | "pending";

/** Whether every / some / none of a record's points have matured. */
export function maturity(r: ReplayRecord): Maturity {
  const n = r.points.filter((p) => p.matured).length;
  return n === 0 ? "pending" : n === r.points.length ? "matured" : "partial";
}

/** Counts of the 3 buckets over the records of one day. */
export function maturityBuckets(records: ReplayRecord[]): Record<Maturity, number> {
  const out: Record<Maturity, number> = { matured: 0, partial: 0, pending: 0 };
  for (const r of records) out[maturity(r)] += 1;
  return out;
}

/** Latest issue day with at least one matured point. */
export function latestMaturedDay(records: ReplayRecord[]): string | null {
  const days = records.filter((r) => r.points.some((p) => p.matured)).map((r) => r.issue_day);
  return days.length ? days.sort()[days.length - 1] : null;
}

export function rollingMean(values: number[], window: number): (number | null)[] {
  return values.map((_, i) => {
    if (i + 1 < window) return null;
    const w = values.slice(i + 1 - window, i + 1);
    return w.reduce((a, b) => a + b, 0) / window;
  });
}

export interface LiveOverlay {
  asOf?: string;
  spot: number;
  expectedReturnPct: number;
  rangeLow?: number | null;
  rangeHigh?: number | null;
  /** Factor-workbench what-if; replaces expectedReturnPct when set. */
  simulatedReturnPct?: number | null;
}

export const OVERLAY_SOURCE_ID = "live-overlay";

/**
 * Today's artifact forecast (plus the workbench simulation) as a synthetic live record. Its target date is
 * copied from the newest recorded point at `horizon` (the browser never computes one); null when none exists.
 */
export function overlayRecord(o: LiveOverlay, records: ReplayRecord[], horizon: number): ReplayRecord | null {
  const ref = [...records].sort((a, b) => a.issue_day.localeCompare(b.issue_day)).reverse()
    .find((r) => pointAt(r, horizon));
  const refPoint = ref && pointAt(ref, horizon);
  if (!ref || !refPoint || !(o.spot > 0)) return null;
  const simulated = o.simulatedReturnPct != null && Number.isFinite(o.simulatedReturnPct);
  const ret = simulated ? (o.simulatedReturnPct as number) : o.expectedReturnPct;
  const day = (o.asOf ?? ref.issue_day).slice(0, 10);
  const p50 = o.spot * (1 + ret / 100);
  const band = !simulated && o.rangeLow != null && o.rangeHigh != null;
  return {
    source_id: OVERLAY_SOURCE_ID,
    family: "overlay",
    issued_at: o.asOf ?? ref.issued_at,
    issue_day: day,
    provenance: "live",
    spot_at_issue: o.spot,
    approximate_path: true,
    meta: { overlay: true, simulated },
    points: [{
      horizon_days: horizon, target_date: refPoint.target_date, p50_level: p50,
      p10_level: band ? (o.rangeLow as number) : null, p90_level: band ? (o.rangeHigh as number) : null,
      matured: false, actual_level: null, error_pts: null, error_pct: null, in_band: null, direction_hit: null,
    }],
  };
}

/** Source ids grouped by family, keeping server order. */
export function groupByFamily(sources: ReplaySource[]): { family: string; sources: ReplaySource[] }[] {
  const m = new Map<string, ReplaySource[]>();
  for (const s of sources) m.set(s.family, [...(m.get(s.family) ?? []), s]);
  return [...m].map(([family, list]) => ({ family, sources: list }));
}

export function sourceNote(s: ReplaySource): string | null {
  if (s.status === "ok") return null;
  return s.status === "error" ? `error: ${s.reason ?? "unknown"}` : `no history: ${s.reason ?? "no records"}`;
}
