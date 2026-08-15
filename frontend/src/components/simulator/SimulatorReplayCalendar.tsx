import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { type ReplayCalendarDay } from "@/lib/api";

// Rows are JS Sun..Sat (0..6). Show a label every other row so we render
// Mon/Wed/Fri exactly, matching GitHub's contribution graph.
const ROW_LABELS = ["", "Mon", "", "Wed", "", "Fri", ""];

// Cell + gap dimensions, shared by the weekday column, month-label row, and
// cell columns so a flex row/column layout keeps everything aligned without
// any manual grid-index math.
const CELL_PX = 12;
const CELL_GAP_PX = 3;
const WEEKS = 53;
const WINDOW_DAYS = WEEKS * 7;

type Underlying = "nifty" | "banknifty" | "sensex";

function toDateOnly(iso: string): Date {
  return new Date(iso + "T00:00:00");
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function addDays(d: Date, n: number): Date {
  const next = new Date(d);
  next.setDate(next.getDate() + n);
  return next;
}

function buildGrid(
  days: ReplayCalendarDay[],
  windowEnd: string,
): {
  weeks: { date: Date; day: ReplayCalendarDay | null }[][];
  months: { col: number; label: string; year: number }[];
  startSunday: Date;
} {
  const latest = toDateOnly(windowEnd);
  // Sunday 52 weeks before the window end (not Saturday of that week) so a
  // window always shows a clean 53-week span ending on `windowEnd`.
  const jsWeekdayLatest = latest.getDay(); // Sun=0..Sat=6
  const startSunday = addDays(latest, -(52 * 7) - jsWeekdayLatest);

  const byKey = new Map(days.map((d) => [d.date, d]));
  const weeks: { date: Date; day: ReplayCalendarDay | null }[][] = [];
  const months: { col: number; label: string; year: number }[] = [];
  const seenMonth = new Set<string>();

  for (let w = 0; w < WEEKS; w++) {
    const week: { date: Date; day: ReplayCalendarDay | null }[] = [];
    for (let d = 0; d < 7; d++) {
      const cell = addDays(startSunday, w * 7 + d);
      const key = isoDate(cell);
      const day = byKey.get(key) ?? null;
      // Label the first cell of every new month so columns stay grouped.
      if (d === 0) {
        const mkey = `${cell.getFullYear()}-${cell.getMonth()}`;
        if (!seenMonth.has(mkey)) {
          seenMonth.add(mkey);
          months.push({
            col: w,
            label: cell.toLocaleString(undefined, { month: "short" }),
            year: cell.getFullYear(),
          });
        }
      }
      week.push({ date: cell, day });
    }
    weeks.push(week);
  }
  return { weeks, months, startSunday };
}

function densityLevel(day: ReplayCalendarDay): 0 | 1 | 2 | 3 | 4 {
  const total = day.nifty_rows + day.banknifty_rows + day.sensex_rows;
  if (total === 0) return 1;
  if (total < 200) return 1;
  if (total < 1000) return 2;
  if (total < 5000) return 3;
  return 4;
}

// Per-underlying density. We stack NIFTY (green), BANKNIFTY (blue), SENSEX
// (amber) inside the same cell so the calendar shows *which* underlyings are
// available, not just how many bars total.
function perUnderlying(day: ReplayCalendarDay): Record<Underlying, 0 | 1 | 2 | 3 | 4> {
  function lvl(rows: number): 0 | 1 | 2 | 3 | 4 {
    if (rows <= 0) return 0;
    if (rows < 200) return 1;
    if (rows < 1000) return 2;
    if (rows < 5000) return 3;
    return 4;
  }
  return {
    nifty: lvl(day.nifty_rows),
    banknifty: lvl(day.banknifty_rows),
    sensex: lvl(day.sensex_rows),
  };
}

const UNDERLYING_PALETTE: Record<Underlying, string[]> = {
  // Index 0 is the underlying's "no data" shade; indices 1..4 are density tiers.
  nifty: [
    "border-emerald-500/20 bg-emerald-500/0",
    "border-emerald-500/40 bg-emerald-500/30",
    "border-emerald-500/50 bg-emerald-500/50",
    "border-emerald-500/60 bg-emerald-500/70",
    "border-emerald-500/70 bg-emerald-500/90",
  ],
  banknifty: [
    "border-blue-500/20 bg-blue-500/0",
    "border-blue-500/40 bg-blue-500/30",
    "border-blue-500/50 bg-blue-500/50",
    "border-blue-500/60 bg-blue-500/70",
    "border-blue-500/70 bg-blue-500/90",
  ],
  sensex: [
    "border-amber-500/20 bg-amber-500/0",
    "border-amber-500/40 bg-amber-500/30",
    "border-amber-500/50 bg-amber-500/50",
    "border-amber-500/60 bg-amber-500/70",
    "border-amber-500/70 bg-amber-500/90",
  ],
};

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export interface ReplayRange {
  start: string;
  end: string;
}

/**
 * GitHub-contribution-style heatmap calendar with two-click range selection:
 * click a day to anchor the range start, click a second day to set the end
 * (order-normalized — clicking "before" the anchor flips start/end). A third
 * click starts a fresh single-day range. Cells strictly between the anchor
 * and the hovered cell preview the pending range before the second click.
 *
 * The visible 53-week window can be paged backward/forward a year at a time;
 * clicking a day with data also reports it via `onDaySelect` so a caller can
 * show a details panel for that date.
 */
export function SimulatorReplayCalendar({
  days,
  range,
  armedRange,
  onRangeSelect,
  onDaySelect,
  selectedDate,
}: {
  days: ReplayCalendarDay[];
  range: ReplayRange | null;
  armedRange: ReplayRange | null;
  onRangeSelect: (range: ReplayRange) => void;
  onDaySelect?: (day: ReplayCalendarDay) => void;
  selectedDate?: string | null;
}) {
  const latestDate = days.length > 0 ? days[0].date : null;
  const earliestDate = useMemo(
    () => (days.length > 0 ? days.reduce((min, d) => (d.date < min ? d.date : min), days[0].date) : null),
    [days],
  );

  const [windowEnd, setWindowEnd] = useState<string | null>(latestDate);
  const effectiveWindowEnd = windowEnd ?? latestDate;

  const { weeks, months, startSunday } = useMemo(
    () => (effectiveWindowEnd ? buildGrid(days, effectiveWindowEnd) : { weeks: [], months: [], startSunday: new Date() }),
    [days, effectiveWindowEnd],
  );

  const today = useMemo(() => new Date(), []);
  const [anchor, setAnchor] = useState<string | null>(range ? range.start : null);
  const [hovered, setHovered] = useState<string | null>(null);

  if (days.length === 0 || !effectiveWindowEnd) {
    return (
      <div className="rounded-lg border bg-background/60 p-4 text-center text-sm text-muted-foreground">
        No replay data on disk yet. Record a full trading day (above) to enable replay.
      </div>
    );
  }

  const canGoPrev = !earliestDate || isoDate(startSunday) > earliestDate;
  const canGoNext = !latestDate || effectiveWindowEnd < latestDate;

  function goPrevYear() {
    const next = addDays(toDateOnly(effectiveWindowEnd!), -WINDOW_DAYS);
    setWindowEnd(isoDate(next));
  }
  function goNextYear() {
    const next = addDays(toDateOnly(effectiveWindowEnd!), WINDOW_DAYS);
    const capped = latestDate && isoDate(next) > latestDate ? latestDate : isoDate(next);
    setWindowEnd(capped);
  }
  function goToLatest() {
    setWindowEnd(latestDate);
  }

  function handleClick(day: ReplayCalendarDay) {
    const key = day.date;
    onDaySelect?.(day);
    if (anchor === null || (range && range.start !== range.end)) {
      // Starting a fresh selection (nothing anchored, or a full range from a
      // prior selection is already showing).
      setAnchor(key);
      onRangeSelect({ start: key, end: key });
      return;
    }
    // Second click completes the range, normalizing order.
    const start = key < anchor ? key : anchor;
    const end = key < anchor ? anchor : key;
    setAnchor(null);
    onRangeSelect({ start, end });
  }

  const previewEnd = anchor !== null ? hovered ?? anchor : null;
  const previewStart = anchor !== null && previewEnd !== null ? (previewEnd < anchor ? previewEnd : anchor) : null;
  const previewLast = anchor !== null && previewEnd !== null ? (previewEnd < anchor ? anchor : previewEnd) : null;

  const windowLabel = (() => {
    if (months.length === 0) return "";
    const first = months[0];
    const last = months[months.length - 1];
    if (first.year === last.year) return `${first.label} – ${last.label} ${last.year}`;
    return `${first.label} ${first.year} – ${last.label} ${last.year}`;
  })();

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>
          {anchor
            ? `Range start ${anchor} — click an end day.`
            : "Click a day for the range start, then click another for the end."}
        </span>
        <div className="flex items-center gap-1.5">
          <span className="tabular-nums text-foreground/80">{windowLabel}</span>
          <button
            type="button"
            onClick={goPrevYear}
            disabled={!canGoPrev}
            title="Previous year"
            className="rounded border p-0.5 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={goNextYear}
            disabled={!canGoNext}
            title="Next year"
            className="rounded border p-0.5 hover:bg-muted disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          {canGoNext ? (
            <button
              type="button"
              onClick={goToLatest}
              className="rounded border px-1.5 py-0.5 text-[10px] font-medium hover:bg-muted"
            >
              Latest
            </button>
          ) : null}
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border bg-background/60 p-3">
        {/*
          Flex layout: a fixed weekday-label column next to a scroll-free
          column containing the month-label row above the week columns. Each
          week is its own flex column of 7 day cells, so cell stacking and
          label alignment fall out of normal flex flow instead of manual
          CSS-grid index math.
        */}
        <div className="flex items-start gap-[3px]" onMouseLeave={() => setHovered(null)}>
          <div
            className="flex shrink-0 flex-col gap-[3px]"
            style={{ paddingTop: `${CELL_PX + CELL_GAP_PX}px` }}
          >
            {ROW_LABELS.map((label, di) => (
              <div
                key={`wd-${di}`}
                className="flex items-center text-[9px] leading-none text-muted-foreground/70"
                style={{ height: `${CELL_PX}px` }}
              >
                {label}
              </div>
            ))}
          </div>

          <div className="flex flex-col gap-[3px]">
            <div className="flex gap-[3px]">
              {weeks.map((_, col) => {
                const label = months.find((m) => m.col === col)?.label ?? "";
                return (
                  <div
                    key={`m-${col}`}
                    className="shrink-0 self-end pb-[2px] text-[9px] leading-none text-muted-foreground/70"
                    style={{ width: `${CELL_PX}px` }}
                  >
                    {label}
                  </div>
                );
              })}
            </div>

            <div className="flex gap-[3px]">
              {weeks.map((week, wi) => (
                <div key={wi} className="flex shrink-0 flex-col gap-[3px]">
                  {week.map((cell, di) => {
                    const key = isoDate(cell.date);
                    const day = cell.day;
                    const total = day ? densityLevel(day) : 0;
                    const underlying = day ? perUnderlying(day) : null;
                    const inSelectedRange = Boolean(range && key >= range.start && key <= range.end);
                    const isRangeEdge = Boolean(range && (key === range.start || key === range.end));
                    const inPreview = Boolean(
                      previewStart && previewLast && key >= previewStart && key <= previewLast,
                    );
                    const isArmed = Boolean(
                      armedRange && key >= armedRange.start && key <= armedRange.end,
                    );
                    const isFuture = cell.date > today;
                    const isToday = sameDay(cell.date, today);
                    const isSelected = Boolean(selectedDate && key === selectedDate);
                    const noData = !day || total === 0;

                    // Pick the dominant underlying (highest density tier
                    // present) for the cell background; the other two
                    // contribute as borders so all three are visible at a
                    // glance.
                    const dom: Underlying = underlying
                      ? (["nifty", "banknifty", "sensex"] as Underlying[]).reduce(
                          (best, u) => (underlying[u] > underlying[best] ? u : best),
                          "nifty",
                        )
                      : "nifty";
                    const domLvl = underlying ? underlying[dom] : 0;

                    return (
                      <button
                        key={`${wi}-${di}`}
                        type="button"
                        disabled={noData || isFuture}
                        onClick={() => day && handleClick(day)}
                        onMouseEnter={() => day && setHovered(day.date)}
                        title={
                          day
                            ? `${day.date} · NIFTY ${day.nifty_rows} · BANKNIFTY ${day.banknifty_rows} · SENSEX ${day.sensex_rows}`
                            : key
                        }
                        className={cn(
                          "rounded-[2px] border transition-colors",
                          "h-[12px] w-[12px]",
                          noData && "border-border/40 bg-muted/40",
                          !noData && UNDERLYING_PALETTE[dom][domLvl],
                          // Underlying presence strips. When a cell has any
                          // data, paint a thin top/left/right border in the
                          // other two underlyings' colours so all three
                          // stack visibly.
                          !noData && underlying?.nifty && underlying.nifty > 0 && "border-l-2 border-l-emerald-500/70",
                          !noData && underlying?.banknifty && underlying.banknifty > 0 && "border-r-2 border-r-blue-500/70",
                          !noData && underlying?.sensex && underlying.sensex > 0 && "border-t-2 border-t-amber-500/70",
                          isToday && !isArmed && !inSelectedRange && "ring-1 ring-amber-400/60",
                          inPreview && !inSelectedRange && "ring-1 ring-primary/50",
                          inSelectedRange && !isRangeEdge && "ring-1 ring-primary/70",
                          isRangeEdge && "ring-2 ring-primary",
                          isArmed && "ring-2 ring-amber-400",
                          isSelected && "ring-2 ring-foreground",
                          isFuture && "opacity-40",
                        )}
                        aria-label={day ? `Replay ${day.date}` : `No data ${key}`}
                        data-testid={`replay-day-${key}`}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[10px] text-muted-foreground">
          <div className="flex items-center gap-2">
            <span>Less</span>
            {[1, 2, 3, 4].map((lvl) => (
              <span
                key={lvl}
                className={cn("h-[10px] w-[10px] rounded-[2px] border", UNDERLYING_PALETTE.nifty[lvl])}
              />
            ))}
            <span>More</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="h-[10px] w-[10px] rounded-[2px] border border-l-2 border-l-emerald-500/70 bg-emerald-500/40" />
            NIFTY
            <span className="ml-2 h-[10px] w-[10px] rounded-[2px] border border-r-2 border-r-blue-500/70 bg-blue-500/40" />
            BANKNIFTY
            <span className="ml-2 h-[10px] w-[10px] rounded-[2px] border border-t-2 border-t-amber-500/70 bg-amber-500/40" />
            SENSEX
          </div>
          <span className="inline-flex items-center gap-1">
            <span className="h-[10px] w-[10px] rounded-[2px] ring-2 ring-primary" /> range
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-[10px] w-[10px] rounded-[2px] ring-2 ring-amber-400" /> armed
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-[10px] w-[10px] rounded-[2px] ring-2 ring-foreground" /> selected
          </span>
        </div>
      </div>
    </div>
  );
}
