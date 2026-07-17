import { useMemo, useState } from "react";
import type { IndexTrackScoreboardReport } from "@/lib/api";
import {
  computeTrackEvalStats,
  fmtHitRate,
  fmtNum,
  fmtPct,
} from "@/lib/trackScoreboardUtils";
import {
  CANONICAL_TRACK_IDS,
  trackDisplayLabel,
} from "@/lib/trackScoreboardReplayUtils";
import { cn } from "@/lib/utils";

interface Props {
  report: IndexTrackScoreboardReport;
  horizonDays: number;
}

const TRACK_NOTES: Record<string, string> = {
  naive_zero:
    "Always predicts 0% horizon return — the dashed forecast stays flat at the anchor spot by design (sanity baseline, not a bug).",
  event_overlay:
    "Needs news shock calibration topics in hub; without calibration every eval is 0% and the track is marked unavailable on recompute.",
  bottom_up:
    "Only scores OOS days with enough constituent signals — n may be lower than other tracks.",
  headline_legacy:
    "Replay uses reconcile/finalize without live debate merge — compare to Analysis headline separately.",
  debate_numeric: "Live hub only — no historical OOS rows.",
};

export function TrackScoreboardEvalPanel({ report, horizonDays }: Props) {
  const trackIds = useMemo(
    () =>
      CANONICAL_TRACK_IDS.filter((tid) =>
        (report.daily_evaluations ?? []).some((r) => r.track_id === tid),
      ),
    [report.daily_evaluations],
  );

  const [selectedTrackId, setSelectedTrackId] = useState<string>(trackIds[0] ?? "quant_ridge");

  const activeTrackId = trackIds.includes(selectedTrackId as (typeof trackIds)[number])
    ? selectedTrackId
    : trackIds[0] ?? "";
  const summaryRow = report.tracks?.[activeTrackId];
  const stats = useMemo(() => {
    const fromDaily = computeTrackEvalStats(report.daily_evaluations, activeTrackId);
    return {
      evalCount: summaryRow?.eval_count ?? fromDaily.evalCount,
      hitCount: summaryRow?.direction_hit_count ?? fromDaily.hitCount,
      missCount: summaryRow?.direction_miss_count ?? fromDaily.missCount,
      hitRate: summaryRow?.direction_hit_rate ?? fromDaily.hitRate,
      maePct: summaryRow?.mae_pct ?? fromDaily.maePct,
    };
  }, [activeTrackId, report.daily_evaluations, summaryRow]);

  const daily = useMemo(() => {
    return [...(report.daily_evaluations ?? [])]
      .filter((r) => r.track_id === activeTrackId)
      .sort((a, b) => String(b.date).localeCompare(String(a.date)))
      .slice(0, 30);
  }, [report.daily_evaluations, activeTrackId]);

  if (!trackIds.length) {
    return null;
  }

  const trackNote = TRACK_NOTES[activeTrackId];

  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Per-track walk-forward evaluation
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Same OOS rows as Analysis backtest — each line is one eval date with direction ✓/✗ for that track only.
          </p>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
          Track
          <select
            value={activeTrackId}
            onChange={(e) => setSelectedTrackId(e.target.value)}
            className="rounded-md border border-border/60 bg-background px-2 py-1 text-[11px] text-foreground"
          >
            {trackIds.map((tid) => (
              <option key={tid} value={tid}>
                {trackDisplayLabel(tid)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {trackNote ? (
        <div className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">{trackDisplayLabel(activeTrackId)}:</span> {trackNote}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div className="text-[11px]">
          <span className="text-muted-foreground">Out-of-sample MAE</span>
          <p className="text-lg font-semibold tabular-nums">{fmtPct(stats.maePct)}</p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Direction hit rate</span>
          <p className="text-lg font-semibold tabular-nums">{fmtHitRate(stats.hitRate)}</p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Direction hits</span>
          <p className="text-lg font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
            {stats.hitCount}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">Direction misses</span>
          <p className="text-lg font-semibold tabular-nums text-red-600 dark:text-red-400">
            {stats.missCount}
          </p>
        </div>
        <div className="text-[11px]">
          <span className="text-muted-foreground">OOS eval dates</span>
          <p className="text-lg font-semibold tabular-nums">{stats.evalCount}</p>
        </div>
      </div>

      <div>
        <p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Recent eval dates — {trackDisplayLabel(activeTrackId)}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="py-1.5 pr-3">Date</th>
                <th className="py-1.5 pr-3">Nifty</th>
                <th className="py-1.5 pr-3">Predicted {horizonDays}d</th>
                <th className="py-1.5 pr-3">Actual {horizonDays}d</th>
                <th className="py-1.5 pr-3">Error</th>
                <th className="py-1.5">Hit</th>
              </tr>
            </thead>
            <tbody>
              {daily.map((row) => {
                const implied =
                  row.implied_level ??
                  (row.close != null && row.predicted_pct != null
                    ? row.close * (1 + row.predicted_pct / 100)
                    : null);
                return (
                  <tr key={`${row.date}-${row.track_id}`} className="border-b border-border/40">
                    <td className="py-1.5 pr-3 tabular-nums">{row.date}</td>
                    <td className="py-1.5 pr-3 tabular-nums">{fmtNum(row.close)}</td>
                    <td className="py-1.5 pr-3 tabular-nums">
                      {fmtPct(row.predicted_pct)}
                      {implied != null && Number.isFinite(implied) ? (
                        <span className="ml-1 text-muted-foreground">→ {fmtNum(implied)}</span>
                      ) : null}
                    </td>
                    <td className="py-1.5 pr-3 tabular-nums">{fmtPct(row.actual_pct)}</td>
                    <td className="py-1.5 pr-3 tabular-nums">{fmtPct(row.error_pct)}</td>
                    <td className="py-1.5">
                      <span
                        className={cn(
                          "font-medium",
                          row.direction_hit
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-red-600 dark:text-red-400",
                        )}
                      >
                        {row.direction_hit ? "✓" : "✗"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
