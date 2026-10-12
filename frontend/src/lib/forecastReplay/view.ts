// Pure view helpers for the replay panel. Forecast dates always come from the server; the only
// calendar arithmetic here is the fetch window (`shiftDays`), never a forecast or target date.
import type { ReplayPoint, ReplayRecord, ReplaySource } from "./types";

/** Records that carry a point at `horizon`, plus multi-point paths (a fan has no exact 14d point but is the story). */
export function forHorizon(records: ReplayRecord[], horizon: number): ReplayRecord[] {
  return records.filter((r) => r.points.length > 1 || r.points.some((p) => p.horizon_days === horizon));
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

/** The point at `horizon`, else the record's furthest point (a fan's headline number). */
export function keyPoint(r: ReplayRecord, horizon: number): ReplayPoint | undefined {
  return pointAt(r, horizon) ?? r.points.reduce<ReplayPoint | undefined>((a, p) => (!a || p.horizon_days > a.horizon_days ? p : a), undefined);
}

/** Percent move from spot to the key point's p50, null when the record has no points. */
export function forecastReturnPct(r: ReplayRecord, horizon: number): number | null {
  const p = keyPoint(r, horizon);
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

/** The overlay's expected return: the workbench what-if when set, else the artifact's. */
function overlayReturn(o: LiveOverlay): { ret: number; simulated: boolean } {
  const simulated = o.simulatedReturnPct != null && Number.isFinite(o.simulatedReturnPct);
  return { ret: simulated ? (o.simulatedReturnPct as number) : o.expectedReturnPct, simulated };
}

/**
 * Today's artifact forecast (plus the workbench simulation) as a synthetic live record. `records` are every loaded
 * record, whatever horizons they carry. The browser never computes a target date and never borrows a stale one: it is
 * copied only from a recorded point of the SAME `horizon` on the latest issue day, and only if it falls after the
 * overlay's own day. Otherwise null (the panel then shows `overlayChip`, no dated point).
 */
export function overlayRecord(o: LiveOverlay, records: ReplayRecord[], horizon: number): ReplayRecord | null {
  const latest = anchorDays(records).pop();
  const ref = records.find((r) => r.issue_day === latest && pointAt(r, horizon));
  const refPoint = ref && pointAt(ref, horizon);
  if (!ref || !refPoint || !(o.spot > 0)) return null;
  const { ret, simulated } = overlayReturn(o);
  const day = (o.asOf ?? ref.issue_day).slice(0, 10);
  if (refPoint.target_date <= day) return null;
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

/** The overlay as a legend line when no dated point can be drawn (no recorded date for this horizon); null if unusable. */
export function overlayChip(o: LiveOverlay, horizon: number): string | null {
  const { ret, simulated } = overlayReturn(o);
  if (!(o.spot > 0) || !Number.isFinite(ret)) return null;
  return `${simulated ? "Live forecast (what-if)" : "Live forecast"}: ${ret >= 0 ? "+" : ""}${ret.toFixed(2)}% over ${horizon}d — no recorded date for this horizon`;
}

/** Source ids grouped by family, keeping server order. */
export function groupByFamily(sources: ReplaySource[]): { family: string; sources: ReplaySource[] }[] {
  const m = new Map<string, ReplaySource[]>();
  for (const s of sources) m.set(s.family, [...(m.get(s.family) ?? []), s]);
  return [...m].map(([family, list]) => ({ family, sources: list }));
}

/** The server's per-request source limit (`_FORECAST_REPLAY_MAX_SOURCES` in trade_routes.py); the picker stops here. */
export const MAX_REPLAY_SOURCES = 40;

/** First occurrence of each key wins; the server dedups today, the keys the UI renders must stay unique anyway. */
export function uniqueBy<T>(items: T[], key: (t: T) => string): T[] {
  const seen = new Set<string>();
  return items.filter((i) => { const k = key(i); return seen.has(k) ? false : (seen.add(k), true); });
}

/**
 * The selection after the single/compare switch: single keeps one source (the first), compare keeps what was selected
 * and adds the default sources that are missing (up to the server limit), so Compare never opens as a one-source view.
 */
export function selectionForMode(single: boolean, selected: string[], defaults: string[]): string[] {
  if (single) return selected.length > 1 ? selected.slice(0, 1) : selected;
  const missing = defaults.filter((d) => !selected.includes(d));
  return missing.length ? [...selected, ...missing].slice(0, MAX_REPLAY_SOURCES) : selected;
}

/** One picker click: single replaces; compare adds (refused at the limit) or removes (never the last one). */
export function toggleSelection(selected: string[], id: string, single: boolean): string[] {
  if (single) return [id];
  if (!selected.includes(id)) return selected.length >= MAX_REPLAY_SOURCES ? selected : [...selected, id];
  return selected.length > 1 ? selected.filter((x) => x !== id) : selected;
}

export function sourceNote(s: ReplaySource): string | null {
  if (s.status === "ok") return null;
  return s.status === "error" ? `error: ${s.reason ?? "unknown"}` : `no history: ${s.reason ?? "no records"}`;
}
