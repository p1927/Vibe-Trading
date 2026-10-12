// Pure geometry for the shaded gap between a recorded predicted path and the actual closes.
// Works in "x" units supplied by the caller (pixels in the chart, indices in tests) and in price levels:
// the renderer maps levels to pixels, so nothing here knows about canvases or calendar arithmetic.
import { recordPath } from "./paths";
import type { ReplayActual, ReplayRecord } from "./types";

export interface GapSample {
  x: number;
  pred: number;
  act: number;
}

/** sign +1 = actual above the forecast (under-forecast), -1 = actual below it. */
export interface GapPolygon {
  sign: 1 | -1;
  pts: GapSample[];
}

/** Last matured target date of a record; null when nothing has matured (nothing to shade). */
export function maturedUntil(r: ReplayRecord): string | null {
  const days = r.points.filter((p) => p.matured).map((p) => p.target_date);
  return days.length ? days.sort()[days.length - 1] : null;
}

function interpolate(v: { x: number; level: number }[], x: number): number | null {
  for (let i = 1; i < v.length; i++) {
    if (x <= v[i].x) {
      const span = v[i].x - v[i - 1].x;
      return span === 0 ? v[i].level : v[i - 1].level + ((x - v[i - 1].x) / span) * (v[i].level - v[i - 1].level);
    }
  }
  return null;
}

/**
 * Polygons between the record's predicted path and the actual closes, from the issue day to its last matured
 * target date. A sign change of (actual - predicted) splits the area at the exact crossing. `xOf(date)` is the
 * chart's x of a date (null = not on the time scale; such dates are skipped).
 */
export function gapPolygons(r: ReplayRecord, actuals: ReplayActual[], xOf: (date: string) => number | null): GapPolygon[] {
  const to = maturedUntil(r);
  if (!to) return [];
  const verts = recordPath(r).vertices
    .flatMap((v) => {
      const x = xOf(v.date);
      return x == null ? [] : [{ x, level: v.level }];
    })
    .sort((a, b) => a.x - b.x);
  if (verts.length < 2) return [];

  const samples: GapSample[] = [];
  for (const a of actuals) {
    if (a.date < r.issue_day || a.date > to) continue;
    const x = xOf(a.date);
    if (x == null || x < verts[0].x) continue;
    const pred = interpolate(verts, x);
    if (pred != null) samples.push({ x, pred, act: a.close });
  }

  const out: GapPolygon[] = [];
  let cur: GapPolygon | null = null;
  let prev: GapSample | null = null;
  const flush = () => {
    if (cur && cur.pts.length >= 2) out.push(cur);
    cur = null;
  };
  for (const s of samples) {
    const d = s.act - s.pred;
    const sign: 0 | 1 | -1 = d > 0 ? 1 : d < 0 ? -1 : 0;
    const c = cur as GapPolygon | null;
    if (sign === 0) {
      c?.pts.push(s);
    } else if (!c) {
      // a sample where the paths touch starts the area it leads into
      cur = { sign, pts: prev && prev.act === prev.pred ? [prev, s] : [s] };
    } else if (c.sign === sign) {
      c.pts.push(s);
    } else if (prev) {
      const dp = prev.act - prev.pred;
      const t = dp / (dp - d);
      const lerp = (a: number, b: number) => a + t * (b - a);
      const cross = dp === 0 ? prev : { x: lerp(prev.x, s.x), pred: lerp(prev.pred, s.pred), act: lerp(prev.act, s.act) };
      if (dp !== 0) c.pts.push(cross);
      flush();
      cur = { sign, pts: [cross, s] };
    }
    prev = s;
  }
  flush();
  return out;
}

/** Records that get a shaded gap: all drawn ones (not overlays or implausible records), but with more than `maxSources` sources only the anchor record. */
export function shadedRecords(drawn: ReplayRecord[], anchorDay: string, maxSources = 4): ReplayRecord[] {
  const real = drawn.filter((r) => !r.meta?.overlay && r.meta?.return_implausible !== true);
  if (new Set(real.map((r) => r.source_id)).size <= maxSources) return real;
  const anchor = real.find((r) => r.issue_day === anchorDay);
  return anchor ? [anchor] : [];
}

/** Fill opacity by provenance: recorded-live is the strongest, reconstructed ones are never mistaken for it. */
export const GAP_ALPHA = { live: 0.22, replay: 0.14, backtest: 0.1 } as const;
