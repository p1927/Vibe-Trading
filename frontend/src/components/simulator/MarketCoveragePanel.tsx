import { useMemo } from "react";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { type MarketReplayCalendarDay } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useMarketReplayCalendar } from "./useMarketReplayCalendar";

// Per-index presence stripes, one side + color per index position (not per index name — the
// index set differs by market, so this is assigned positionally, same convention
// `SimulatorReplayCalendar`'s `UNDERLYING_PALETTE` uses for India's 3 fixed underlyings). Only
// meaningful once a market has 3+ indices (US/CN/ME) -- below that, "some vs. all present" and
// "all present" read the same either way, so 1-2 index markets keep the plain solid cell.
const INDEX_STRIPE_CLASSES = [
  "border-t-2 border-t-emerald-500/70",
  "border-r-2 border-r-blue-500/70",
  "border-b-2 border-b-amber-500/70",
  "border-l-2 border-l-violet-500/70",
] as const;
const INDEX_LEGEND_SWATCH_CLASSES = [
  "border border-t-2 border-t-emerald-500/70 bg-emerald-500/30",
  "border border-r-2 border-r-blue-500/70 bg-blue-500/30",
  "border border-b-2 border-b-amber-500/70 bg-amber-500/30",
  "border border-l-2 border-l-violet-500/70 bg-violet-500/30",
] as const;
const MIN_INDICES_FOR_STRIPES = 3;

// Same weekday/cell-sizing convention as India's `StockHistoryCoveragePanel` `YearHeatmap`, so
// the two grids read as the same kind of calendar.
const ROW_LABELS = ["", "Mon", "", "Wed", "", "Fri", ""];
const CELL_PX = 12;
const CELL_GAP_PX = 3;
const LABEL_COL_PX = 28;
const HEADER_ROW_PX = 14;

/** Per-country "Data coverage" — the non-India analog of `StockHistoryCoveragePanel`'s
 * per-week bucket grid, scoped to what actually exists for these markets: a day-presence
 * calendar over `market_ticks` (see `MarketReplayPanel`'s docstring for why daily, not
 * per-minute). Click a missing day to backfill it via `tick_backfill.py`'s idempotent
 * daily-close writer — same data source `MarketReplayPanel` reads for its calendar. */
export function MarketCoveragePanel({ country }: { country: string }) {
  const {
    days,
    indices,
    loading,
    error,
    backfillingDay,
    reload,
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

  const byDate = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);

  const showStripes = indices.length >= MIN_INDICES_FOR_STRIPES;

  const indexRowsFor = (day: MarketReplayCalendarDay | undefined, idx: string): number => {
    if (!day) return 0;
    return Number(day[`${idx.toLowerCase()}_rows`]) || 0;
  };

  const rowsFor = (day: MarketReplayCalendarDay | undefined): number =>
    indices.reduce((sum, idx) => sum + indexRowsFor(day, idx), 0);

  const missingCount = grid.filter((date) => rowsFor(byDate.get(date)) === 0).length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-muted-foreground">
          Per-day availability, {grid[0]} → {grid[grid.length - 1]} ({windowDays} days). White
          cells = data missing; click a cell to backfill it from the same vendor `Recording`
          uses.
          {missingCount > 0 ? ` ${missingCount} day${missingCount === 1 ? "" : "s"} missing.` : " All days covered."}
        </p>
        <div className="flex items-center gap-1">
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
          <button type="button" onClick={reload} className="shrink-0 rounded border border-destructive/40 px-2 py-0.5 font-medium hover:bg-destructive/10">
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
              const presentIndices = showStripes ? indices.filter((idx) => indexRowsFor(day, idx) > 0) : [];
              const allPresent = showStripes && presentIndices.length === indices.length;
              const title = !hasData
                ? `${date} · missing — click to backfill`
                : showStripes
                  ? `${date} · ${indices.map((idx) => `${idx} ${indexRowsFor(day, idx)}`).join(" · ")}`
                  : `${date} · ${rows} row${rows === 1 ? "" : "s"}`;
              return (
                <button
                  key={date}
                  type="button"
                  title={title}
                  onClick={() => !hasData && backfill(date)}
                  disabled={hasData || backfillingDay === date}
                  data-testid={`market-coverage-day-${date}`}
                  style={{ gridColumn: wi + 2, gridRow: di + 2 }}
                  className={cn(
                    "h-full w-full rounded-[2px] border transition-colors",
                    hasData
                      ? cn(
                          "cursor-default",
                          showStripes && !allPresent
                            ? "border-emerald-500/30 bg-emerald-500/25"
                            : "border-emerald-500/50 bg-emerald-500/50",
                        )
                      : "border-border/40 bg-muted/40 hover:bg-muted",
                    // Per-index presence stripes -- only rendered for 3+ index markets (see
                    // MIN_INDICES_FOR_STRIPES), so a partially-backfilled day (some indices
                    // present, others not) is visually distinct from a fully-covered one instead
                    // of both just reading as "green".
                    showStripes &&
                      indices.map((idx, i) =>
                        indexRowsFor(day, idx) > 0 ? INDEX_STRIPE_CLASSES[i % INDEX_STRIPE_CLASSES.length] : null,
                      ),
                    backfillingDay === date && "opacity-50",
                  )}
                />
              );
            }),
          )}
        </div>
        {showStripes ? (
          <div className="mt-2 flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground">
            {indices.map((idx, i) => (
              <span key={idx} className="inline-flex items-center gap-1">
                <span className={cn("h-[10px] w-[10px] rounded-[2px]", INDEX_LEGEND_SWATCH_CLASSES[i % INDEX_LEGEND_SWATCH_CLASSES.length])} />
                {idx}
              </span>
            ))}
          </div>
        ) : null}
        <div className="mt-2 flex items-center gap-2 text-[10px] text-muted-foreground">
          {loading ? <RefreshCw className="h-3 w-3 animate-spin" /> : null}
        </div>
      </div>
    </div>
  );
}
