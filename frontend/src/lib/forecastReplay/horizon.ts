// Horizon choice for the track-record views: only horizons the loaded records actually carry are offered.
import type { ReplayRecord, ReplaySource } from "./types";

/** Sorted union of every point's horizon_days across `records`. */
export function horizonOptions(records: ReplayRecord[]): number[] {
  return [...new Set(records.flatMap((r) => r.points.map((p) => p.horizon_days)))].sort((a, b) => a - b);
}

/** `preferred` when offered, else the nearest option (the shorter one on a tie); null when nothing is offered. */
export function defaultHorizon(options: number[], preferred: number): number | null {
  if (!options.length) return null;
  if (options.includes(preferred)) return preferred;
  return options.reduce((best, h) => (Math.abs(h - preferred) < Math.abs(best - preferred) ? h : best));
}

/** Whether any record of `sourceId` carries a point at `horizon` (false = "no <h>d point", not "n=0"). */
export function hasPointAt(records: ReplayRecord[], sourceId: string, horizon: number): boolean {
  return records.some((r) => r.source_id === sourceId && r.points.some((p) => p.horizon_days === horizon));
}

/**
 * Why a source's row at `horizon` has no accuracy numbers. The server's own verdict on the source comes first:
 * `error` (it failed to read the source; its reason is shown), `empty` (it has no records and says why, unless a loaded
 * record carries a point at this horizon). Then `no-point` (no forecast at that horizon), `pending` (points exist,
 * none matured yet) or `no-eval` (the server evaluated nothing); `ok` otherwise.
 */
export type RowState = "error" | "empty" | "no-point" | "pending" | "no-eval" | "ok";
export function rowState(
  records: ReplayRecord[], stats: Record<string, number | string | null> | undefined, sourceId: string, horizon: number, source?: ReplaySource,
): RowState {
  if (source?.status === "error") return "error";
  if (source?.status === "empty" && !hasPointAt(records, sourceId, horizon)) return "empty";
  if (!hasPointAt(records, sourceId, horizon)) return "no-point";
  const evalCount = stats?.eval_count;
  if (typeof evalCount === "number" && evalCount > 0) return "ok";
  const matured = records.some((r) => r.source_id === sourceId && r.points.some((p) => p.horizon_days === horizon && p.matured));
  return matured ? "no-eval" : "pending";
}

/** Matured points at `horizon` that carry a band, and how many of them landed inside it. */
export function bandCount(records: ReplayRecord[], sourceId: string, horizon: number): { inside: number; total: number } {
  const pts = records
    .filter((r) => r.source_id === sourceId)
    .flatMap((r) => r.points.filter((p) => p.horizon_days === horizon && p.matured && p.in_band != null));
  return { inside: pts.filter((p) => p.in_band).length, total: pts.length };
}

/** Flagged-record count and the largest recorded-spot vs issue-day-close gap among them; null when none flagged. */
export function spotSuspectSummary(records: ReplayRecord[]): { count: number; gapPct: number | null } | null {
  const flagged = records.filter((r) => r.meta?.spot_suspect === true);
  if (!flagged.length) return null;
  const gaps = flagged.map((r) => r.meta.spot_close_gap_pct).filter((g): g is number => typeof g === "number" && Number.isFinite(g));
  return { count: flagged.length, gapPct: gaps.length ? gaps.reduce((a, g) => (Math.abs(g) > Math.abs(a) ? g : a)) : null };
}

/** Records flagged `meta.return_implausible` and the largest |`meta.return_pct`| among them; null when none flagged. */
export function returnImplausibleSummary(records: ReplayRecord[]): { count: number; returnPct: number | null } | null {
  const flagged = records.filter((r) => r.meta?.return_implausible === true);
  if (!flagged.length) return null;
  const rets = flagged.map((r) => r.meta.return_pct).filter((g): g is number => typeof g === "number" && Number.isFinite(g));
  return { count: flagged.length, returnPct: rets.length ? rets.reduce((a, g) => (Math.abs(g) > Math.abs(a) ? g : a)) : null };
}
