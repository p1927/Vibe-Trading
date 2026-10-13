// Pure: the replay bundle's records as one point per issue day (target level, band, expected vs realised return).
// The history charts of the track-record section read this, so the headline history has one read path (the replay).
import { actualReturnPct, pointAt } from "./view";
import type { ReplayRecord } from "./types";

export interface IssueTimelinePoint {
  issueDay: string;
  issuedAt: string;
  spot: number;
  /** p50 at the horizon. */
  target: number;
  low: number | null;
  high: number | null;
  expectedReturnPct: number;
  /** Realised move from the issue spot to the close on the target date; null until it matures. */
  actualReturnPct: number | null;
}

/**
 * One point per issue day, ascending: the latest run of that day that has a point at `horizon`. Records the server
 * flags `return_implausible` are left out, like the error strip does.
 */
export function issueTimeline(records: ReplayRecord[], horizon: number): IssueTimelinePoint[] {
  const latest = new Map<string, { r: ReplayRecord; p: NonNullable<ReturnType<typeof pointAt>> }>();
  for (const r of records) {
    const p = pointAt(r, horizon);
    if (!p || r.meta?.return_implausible === true || !(r.spot_at_issue > 0)) continue;
    const prev = latest.get(r.issue_day);
    if (!prev || r.issued_at > prev.r.issued_at) latest.set(r.issue_day, { r, p });
  }
  return [...latest.values()]
    .sort((a, b) => a.r.issue_day.localeCompare(b.r.issue_day))
    .map(({ r, p }) => ({
      issueDay: r.issue_day,
      issuedAt: r.issued_at,
      spot: r.spot_at_issue,
      target: p.p50_level,
      low: p.p10_level,
      high: p.p90_level,
      expectedReturnPct: (p.p50_level / r.spot_at_issue - 1) * 100,
      actualReturnPct: actualReturnPct(r, p),
    }));
}
