import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Pause, Play, RefreshCw, Square, Timer } from "lucide-react";
import { api, type MarketReplayCalendarDay, type MultiMarketStatusResponse } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useMarketReplayCalendar } from "./useMarketReplayCalendar";

// Same weekday/cell-sizing convention as India's `StockHistoryCoveragePanel` `YearHeatmap` and
// `MarketCoveragePanel`, so all three grids read as the same kind of calendar.
const ROW_LABELS = ["", "Mon", "", "Wed", "", "Fri", ""];
const CELL_PX = 12;
const CELL_GAP_PX = 3;
const LABEL_COL_PX = 28;
const HEADER_ROW_PX = 14;

/** Per-country Replay panel — the non-India analog of `SimulatorReplayCalendar` +
 * `SimulatorReplayClock`, scoped to one market instead of India's per-week/minute-bar model.
 * Non-India markets only have **daily-close** granularity in `market_ticks` (live tick_recorder
 * polls plus `tick_backfill.py`'s daily-close backfill — see that module's docstring), so the
 * right parity target is a day-presence calendar (green/white), not India's density heatmap.
 * Arm/pause/resume/seek/speed reuse `MultiMarketReplayService` scoped to `markets: [country]`.
 *
 * Supports arming a single day (click once) or a date range (click a second, different day —
 * the range normalizes to [min, max] regardless of click order) with an optional loop, mirroring
 * India's start+end+loop replay controls. Looping only replays whatever backfilled/recorded days
 * fall in the range — there's no deep historical archive behind this clock, just `market_ticks`
 * rows, so a range with gaps just holds the last known tick through them like any other gap. */
export function MarketReplayPanel({ country, label }: { country: string; label: string }) {
  const {
    days,
    indices,
    loading,
    error,
    backfillingDay,
    reload: loadCalendar,
    backfill,
    grid,
    weeks,
    monthLabels,
    windowDays,
    isLatestWindow,
    goOlder,
    goNewer,
    goToLatest,
  } = useMarketReplayCalendar(country);

  const [rangeStart, setRangeStart] = useState<string | null>(null);
  const [rangeEnd, setRangeEnd] = useState<string | null>(null);
  const [loop, setLoop] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [status, setStatus] = useState<MultiMarketStatusResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [armError, setArmError] = useState<string | null>(null);

  const selectDay = (date: string) => {
    if (!rangeStart || rangeEnd) {
      // Nothing selected yet, or a complete range already exists — start a fresh single-day pick.
      setRangeStart(date);
      setRangeEnd(null);
      return;
    }
    // One day already picked — this click completes the range, in either click order.
    if (date === rangeStart) {
      setRangeStart(null);
      return;
    }
    setRangeStart(date < rangeStart ? date : rangeStart);
    setRangeEnd(date < rangeStart ? rangeStart : date);
  };

  const clearSelection = () => {
    setRangeStart(null);
    setRangeEnd(null);
  };

  const refreshStatus = useCallback(() => {
    api
      .getMultiMarketStatus()
      .then((res) => setStatus(res.markets.includes(country) ? res : null))
      .catch(() => setStatus(null));
  }, [country]);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    if (!status) return;
    const interval = window.setInterval(refreshStatus, 5_000);
    return () => window.clearInterval(interval);
  }, [status, refreshStatus]);

  const byDate = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);

  const rowsFor = (day: MarketReplayCalendarDay | undefined): number => {
    if (!day) return 0;
    return indices.reduce((sum, idx) => sum + (Number(day[`${idx.toLowerCase()}_rows`]) || 0), 0);
  };

  const arm = async () => {
    if (!rangeStart) return;
    setBusy(true);
    setArmError(null);
    try {
      const res = await api.armMultiMarketReplay({
        markets: [country],
        start_utc: `${rangeStart}T00:00:00Z`,
        end_utc: rangeEnd ? `${rangeEnd}T23:59:59Z` : undefined,
        speed,
        loop: rangeEnd ? loop : false,
      });
      setStatus(res);
    } catch (err) {
      setArmError(err instanceof Error ? err.message : "Failed to arm replay");
    } finally {
      setBusy(false);
    }
  };

  const pause = async () => {
    setBusy(true);
    try {
      setStatus(await api.pauseMultiMarketReplay());
    } catch (err) {
      setArmError(err instanceof Error ? err.message : "Failed to pause");
    } finally {
      setBusy(false);
    }
  };

  const resume = async () => {
    setBusy(true);
    try {
      setStatus(await api.resumeMultiMarketReplay());
    } catch (err) {
      setArmError(err instanceof Error ? err.message : "Failed to resume");
    } finally {
      setBusy(false);
    }
  };

  const applySpeed = async (next: number) => {
    setSpeed(next);
    if (!status) return;
    setBusy(true);
    try {
      setStatus(await api.setMultiMarketReplaySpeed(next));
    } catch (err) {
      setArmError(err instanceof Error ? err.message : "Failed to change speed");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    setArmError(null);
    try {
      await api.stopMultiMarketReplay();
      setStatus(null);
    } catch (err) {
      setArmError(err instanceof Error ? err.message : "Failed to stop");
    } finally {
      setBusy(false);
    }
  };

  const seek = async (date: string) => {
    setBusy(true);
    setArmError(null);
    try {
      setStatus(await api.seekMultiMarketReplay(`${date}T00:00:00Z`));
    } catch (err) {
      setArmError(err instanceof Error ? err.message : "Failed to seek");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-[11px] text-muted-foreground">
          {label} only has daily-close depth (no free vendor gives real historical intraday bars
          for this market) — each cell below is one trading day, not a per-minute heatmap like
          India's. Click a white (missing) day to backfill it, then select a day and arm replay.
          Once armed, click any recorded day to seek there directly.
          {" "}{grid[0]} → {grid[grid.length - 1]} ({windowDays} days) shown.
        </p>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={goOlder}
            title="Older"
            className="inline-flex h-6 w-6 items-center justify-center rounded border text-muted-foreground hover:bg-accent"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={isLatestWindow ? undefined : goNewer}
            disabled={isLatestWindow}
            title="Newer"
            className="inline-flex h-6 w-6 items-center justify-center rounded border text-muted-foreground hover:bg-accent disabled:opacity-30"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          {!isLatestWindow ? (
            <button
              type="button"
              onClick={goToLatest}
              className="rounded border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-accent"
            >
              Latest
            </button>
          ) : null}
        </div>
      </div>

      {error ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
          <span>{error}</span>
          <button type="button" onClick={loadCalendar} className="shrink-0 rounded border border-destructive/40 px-2 py-0.5 font-medium hover:bg-destructive/10">
            Retry
          </button>
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-lg border bg-background/60 p-3">
        <div
          className="grid"
          style={{
            gridTemplateColumns: `${LABEL_COL_PX}px repeat(${weeks.length}, ${CELL_PX}px)`,
            gridTemplateRows: `${HEADER_ROW_PX}px repeat(7, ${CELL_PX}px)`,
            columnGap: `${CELL_GAP_PX}px`,
            rowGap: `${CELL_GAP_PX}px`,
          }}
        >
          {ROW_LABELS.map((label, di) =>
            label ? (
              <div
                key={`wd-${di}`}
                className="text-[9px] leading-none text-muted-foreground/70"
                style={{ gridColumn: 1, gridRow: di + 2, alignSelf: "center" }}
              >
                {label}
              </div>
            ) : null,
          )}
          {monthLabels.map((m) => (
            <div
              key={`m-${m.col}`}
              className="shrink-0 text-[9px] leading-none text-muted-foreground/70"
              style={{ gridColumn: m.col + 2, gridRow: 1, alignSelf: "end" }}
            >
              {m.label}
            </div>
          ))}
          {weeks.map((week, wi) =>
            week.map((cell, di) => {
              if (!cell) {
                return <div key={`${wi}-${di}`} style={{ gridColumn: wi + 2, gridRow: di + 2 }} />;
              }
              const date = cell.date;
              const day = byDate.get(date);
              const rows = rowsFor(day);
              const hasData = rows > 0;
              const isEndpoint = date === rangeStart || date === rangeEnd;
              const isInRange = Boolean(rangeStart && rangeEnd && date > rangeStart && date < rangeEnd);
              const isArmed = Boolean(status && status.clock.sim_now_utc.slice(0, 10) === date);
              return (
                <button
                  key={date}
                  type="button"
                  title={`${date}${hasData ? ` · ${rows} row${rows === 1 ? "" : "s"}` : " · not recorded"}${hasData && status ? " · click to seek" : ""}`}
                  onClick={() => {
                    if (!hasData) {
                      backfill(date);
                      return;
                    }
                    if (status) {
                      seek(date);
                      return;
                    }
                    selectDay(date);
                  }}
                  disabled={backfillingDay === date || (Boolean(status) && busy)}
                  data-testid={`market-replay-day-${date}`}
                  style={{ gridColumn: wi + 2, gridRow: di + 2 }}
                  className={cn(
                    "h-full w-full rounded-[2px] border transition-colors",
                    hasData ? "border-emerald-500/50 bg-emerald-500/50 hover:bg-emerald-500/70" : "border-border/40 bg-muted/40 hover:bg-muted",
                    isInRange && "bg-foreground/30",
                    isEndpoint && "ring-2 ring-foreground",
                    isArmed && "ring-2 ring-amber-400",
                    backfillingDay === date && "opacity-50",
                  )}
                />
              );
            }),
          )}
        </div>
        <div className="mt-2 flex items-center gap-3 text-[10px] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <span className="h-[10px] w-[10px] rounded-[2px] border border-emerald-500/50 bg-emerald-500/50" /> recorded
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-[10px] w-[10px] rounded-[2px] border border-border/40 bg-muted/40" /> missing — click to backfill
          </span>
          {loading ? <RefreshCw className="h-3 w-3 animate-spin" /> : null}
        </div>
      </div>

      {armError ? <p className="text-[11px] text-destructive">{armError}</p> : null}

      {!status ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-background/60 p-3">
          <span className="text-xs text-muted-foreground">
            {rangeStart && rangeEnd
              ? `${rangeStart} → ${rangeEnd}`
              : rangeStart
                ? `${rangeStart} · click a second day for a range`
                : "Select a recorded day (click a second day for a range)"}
          </span>
          {rangeStart ? (
            <button
              type="button"
              onClick={clearSelection}
              className="rounded border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-accent"
            >
              Clear
            </button>
          ) : null}
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Speed
            <input
              type="number"
              min={0}
              step={0.5}
              value={speed}
              onChange={(e) => setSpeed(Math.max(0, Number(e.target.value) || 0))}
              className="w-16 rounded border bg-background px-1.5 py-1 text-xs"
            />
            ×
          </label>
          {rangeEnd ? (
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={loop}
                onChange={(e) => setLoop(e.target.checked)}
                data-testid="market-replay-loop"
              />
              Loop range
            </label>
          ) : null}
          <button
            type="button"
            onClick={arm}
            disabled={busy || !rangeStart}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5" />
            {rangeStart
              ? `Arm replay · ${rangeStart}${rangeEnd ? ` → ${rangeEnd}` : ""}`
              : "Select a recorded day"}
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background/60 p-2.5 text-xs">
          <div className="flex items-center gap-2">
            <Timer className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="font-mono">{new Date(status.clock.sim_now_utc).toUTCString()}</span>
            <span className="text-muted-foreground">
              · {status.clock.paused ? "paused" : `${status.clock.speed}×`}
              {status.clock.end_utc ? (status.clock.loop ? " · looping range" : " · range") : ""}
              {status.clock.completed ? " · range complete" : ""}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {status.clock.paused ? (
              <button type="button" onClick={resume} disabled={busy} className="inline-flex h-7 items-center gap-1 rounded border px-2 text-[11px] hover:bg-accent disabled:opacity-50">
                <Play className="h-3 w-3" />
                Resume
              </button>
            ) : (
              <button type="button" onClick={pause} disabled={busy} className="inline-flex h-7 items-center gap-1 rounded border px-2 text-[11px] hover:bg-accent disabled:opacity-50">
                <Pause className="h-3 w-3" />
                Pause
              </button>
            )}
            <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
              Speed
              <input
                type="number"
                min={0}
                step={0.5}
                value={speed}
                onChange={(e) => applySpeed(Math.max(0, Number(e.target.value) || 0))}
                className="w-14 rounded border bg-background px-1 py-0.5 text-[11px]"
              />
              ×
            </label>
            <button type="button" onClick={stop} disabled={busy} className="inline-flex h-7 items-center gap-1 rounded border border-red-500/40 px-2 text-[11px] text-red-600 hover:bg-red-500/10 disabled:opacity-50">
              <Square className="h-3 w-3" />
              Stop
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
