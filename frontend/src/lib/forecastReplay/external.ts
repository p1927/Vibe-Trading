// Analyst cards read their chart from the same server bundle as every other replay view (`external:<id>`
// sources). Nothing here builds a record, a target date or a close: it only picks the fetch window and
// slices the bundle per card.
import type { ExternalPredictionRecord } from "../api";
import type { ReplayActual, ReplayBundle, ReplayRecord } from "./types";
import { shiftDays, sourceNote } from "./view";

/** Server limit on the replay window (`_FORECAST_REPLAY_MAX_DAYS` is 400). */
const MAX_WINDOW_DAYS = 395;
const DEFAULT_WINDOW_DAYS = 90;
const LEAD_DAYS = 14;

export const externalSourceId = (sourceId: string) => `external:${sourceId}`;

/** What one card draws: its source's recorded forecasts, the shared actuals, and why there is nothing when there is not. */
export interface ExternalReplay {
  records: ReplayRecord[];
  actuals: ReplayActual[];
  /** Set whenever `records` is empty and the bundle has loaded: the reason, never a silent blank. */
  note: string | null;
  loading: boolean;
}

/** Fetch window shared by all cards: from two weeks before the oldest card's day to `today`, capped at the server limit. */
export function externalWindow(cards: Pick<ExternalPredictionRecord, "as_of" | "published_at">[], today: string) {
  const days = cards.map((c) => (c.as_of ?? c.published_at ?? "").slice(0, 10)).filter(Boolean).sort();
  if (!days.length) return { start: shiftDays(today, -DEFAULT_WINDOW_DAYS), end: today };
  const wanted = shiftDays(days[0], -LEAD_DAYS);
  const floor = shiftDays(today, -MAX_WINDOW_DAYS);
  return { start: wanted < floor ? floor : wanted, end: today };
}

/**
 * One card's slice of the shared bundle (`error` = the whole request failed). A forecast identical to another
 * source's is kept once by the server (`meta.deduped_with` on the kept record); the dropped source's card draws that one.
 */
export function externalReplayFor(bundle: ReplayBundle | null, sourceId: string, error: string | null, loading: boolean): ExternalReplay {
  const id = externalSourceId(sourceId);
  const all = bundle?.records ?? [];
  const own = all.filter((r) => r.source_id === id);
  const records = own.length ? own : all.filter((r) => (r.meta.deduped_with as string[] | undefined)?.includes(id));
  const info = bundle?.sources.find((s) => s.source_id === id);
  let note: string | null = null;
  if (!records.length) {
    if (error) note = `Forecast replay failed: ${error}`;
    else if (bundle) note = info ? (sourceNote(info) ?? "no history: no recorded forecast in this window") : "no history: source not in the replay registry";
  }
  return { records, actuals: bundle?.actuals ?? [], note, loading };
}
