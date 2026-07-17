import { useCallback, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { IndexTrackScoreboardReport } from "@/lib/api";
import { NiftyForecastReplayChart } from "@/components/charts/NiftyForecastReplayChart";
import { MultiTrackForecastReplayChart } from "@/components/charts/MultiTrackForecastReplayChart";
import { fmtHitRate, fmtPct, trackColor } from "@/lib/trackScoreboardUtils";
import {
  buildTrackForecastIndex,
  listScoreboardTrackIds,
  scoreboardPriceSeries,
  trackDisplayLabel,
} from "@/lib/trackScoreboardReplayUtils";
import { cn } from "@/lib/utils";

interface Props {
  report: IndexTrackScoreboardReport;
  horizonDays: number;
}

type ViewMode = "single" | "compare";

export function TrackScoreboardReplaySection({ report, horizonDays }: Props) {
  const trackIds = useMemo(() => listScoreboardTrackIds(report, false), [report]);
  const combinerIds = useMemo(
    () => listScoreboardTrackIds(report, true).filter((id) => id.startsWith("combiner:")),
    [report],
  );

  const [viewMode, setViewMode] = useState<ViewMode>("single");
  const [trackIdx, setTrackIdx] = useState(0);
  const [includeCombiners, setIncludeCombiners] = useState(false);
  const [compareIds, setCompareIds] = useState<Set<string>>(() => new Set(["quant_ridge", "macro_only"]));

  const activeTrackList = useMemo(
    () => (includeCombiners ? [...trackIds, ...combinerIds] : trackIds),
    [trackIds, combinerIds, includeCombiners],
  );

  const safeTrackIdx = Math.min(trackIdx, Math.max(0, activeTrackList.length - 1));
  const selectedTrackId = activeTrackList[safeTrackIdx] ?? trackIds[0] ?? "";
  const combinerKey = selectedTrackId.replace(/^combiner:/, "");
  const selectedMetrics =
    report.tracks?.[selectedTrackId] ?? report.combiners?.[combinerKey];

  const prices = useMemo(() => scoreboardPriceSeries(report), [report]);

  const selectedForecastIndex = useMemo(
    () => buildTrackForecastIndex(report, selectedTrackId),
    [report, selectedTrackId],
  );

  const compareTracks = useMemo(() => {
    const ids = [...compareIds].filter((id) => activeTrackList.includes(id));
    if (!ids.length) return [];
    return ids.map((trackId) => ({
      trackId,
      label: trackDisplayLabel(trackId),
      forecastIndex: buildTrackForecastIndex(report, trackId),
    }));
  }, [compareIds, activeTrackList, report]);

  const stepTrack = useCallback(
    (direction: -1 | 1) => {
      if (!activeTrackList.length) return;
      setTrackIdx((prev) => {
        const idx = Math.min(prev, activeTrackList.length - 1);
        return (idx + direction + activeTrackList.length) % activeTrackList.length;
      });
    },
    [activeTrackList.length],
  );

  const toggleCompare = useCallback((trackId: string) => {
    setCompareIds((prev) => {
      const next = new Set(prev);
      if (next.has(trackId)) {
        if (next.size > 1) next.delete(trackId);
      } else {
        next.add(trackId);
      }
      return next;
    });
  }, []);

  if (!trackIds.length) {
    return (
      <div className="rounded-xl border bg-card p-6 text-[12px] text-muted-foreground">
        No forecast tracks configured — scoreboard is recomputing.
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold">Track forecast replay vs Nifty 50</p>
          <p className="text-[10px] text-muted-foreground">
            Same TradingView lightweight-charts replay as Analysis — scroll/wheel to zoom; dashed = track forecast; solid green = actual Nifty.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg border border-border/60 p-0.5 text-[10px]">
            <button
              type="button"
              onClick={() => setViewMode("single")}
              className={cn(
                "rounded-md px-2.5 py-1 font-medium",
                viewMode === "single" ? "bg-primary text-primary-foreground" : "text-muted-foreground",
              )}
            >
              Single track
            </button>
            <button
              type="button"
              onClick={() => setViewMode("compare")}
              className={cn(
                "rounded-md px-2.5 py-1 font-medium",
                viewMode === "compare" ? "bg-primary text-primary-foreground" : "text-muted-foreground",
              )}
            >
              Compare
            </button>
          </div>
          <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
            <input
              type="checkbox"
              checked={includeCombiners}
              onChange={(e) => {
                setIncludeCombiners(e.target.checked);
                setTrackIdx(0);
              }}
              className="rounded border-border"
            />
            Include combiners
          </label>
        </div>
      </div>

      {viewMode === "single" ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/50 bg-muted/20 px-3 py-2">
            <button
              type="button"
              onClick={() => stepTrack(-1)}
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] hover:bg-background disabled:opacity-40"
              disabled={activeTrackList.length <= 1}
            >
              <ChevronLeft className="h-4 w-4" />
              Prev track
            </button>
            <div className="min-w-0 flex-1 text-center">
              <p
                className="truncate text-sm font-semibold"
                style={{ color: trackColor(selectedTrackId) }}
              >
                {trackDisplayLabel(selectedTrackId)}
              </p>
              <p className="text-[10px] text-muted-foreground">
                {safeTrackIdx + 1} / {activeTrackList.length}
                {selectedMetrics
                  ? ` · MAE ${fmtPct(selectedMetrics.mae_pct)} · direction ${fmtHitRate(selectedMetrics.direction_hit_rate)} · ${selectedMetrics.direction_hit_count ?? "—"}✓ / ${selectedMetrics.direction_miss_count ?? "—"}✗`
                  : ""}
              </p>
            </div>
            <button
              type="button"
              onClick={() => stepTrack(1)}
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] hover:bg-background disabled:opacity-40"
              disabled={activeTrackList.length <= 1}
            >
              Next track
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          <div className="flex flex-wrap gap-1.5">
            {activeTrackList.map((tid, i) => (
              <button
                key={tid}
                type="button"
                onClick={() => setTrackIdx(i)}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-[10px] font-medium transition-colors",
                  i === safeTrackIdx
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border/60 text-muted-foreground hover:bg-muted/50",
                )}
                style={i === safeTrackIdx ? { borderColor: trackColor(tid) } : undefined}
              >
                {trackDisplayLabel(tid)}
              </button>
            ))}
          </div>

          <NiftyForecastReplayChart
            horizonDays={horizonDays}
            priceSeries={prices}
            forecastIndex={selectedForecastIndex}
            predictedLineColor={trackColor(selectedTrackId)}
            legendBacktestLabel="Track walk-forward eval"
            emptyForecastHint={`No ${horizonDays}d ${trackDisplayLabel(selectedTrackId)} forecast on this day. Use Prev/Next forecast to step through OOS eval dates (~every 5 sessions).`}
            height={400}
          />
        </>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5">
            {activeTrackList.map((tid) => {
              const active = compareIds.has(tid);
              return (
                <button
                  key={tid}
                  type="button"
                  onClick={() => toggleCompare(tid)}
                  className={cn(
                    "rounded-full border px-2.5 py-0.5 text-[10px] font-medium transition-colors",
                    active
                      ? "bg-primary/10 text-primary"
                      : "border-border/60 text-muted-foreground hover:bg-muted/50",
                  )}
                  style={active ? { borderColor: trackColor(tid) } : undefined}
                >
                  {trackDisplayLabel(tid)}
                </button>
              );
            })}
          </div>

          <MultiTrackForecastReplayChart
            horizonDays={horizonDays}
            prices={prices}
            tracks={compareTracks}
            height={420}
          />
        </>
      )}
    </div>
  );
}
