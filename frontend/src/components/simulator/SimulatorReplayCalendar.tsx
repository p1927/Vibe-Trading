import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { type ReplayCalendarDay } from "@/lib/api";

const WEEKDAYS = ["Mon", "Wed", "Fri"]; // visual anchor labels (Sun..Sat = 0..6)

function buildGrid(days: ReplayCalendarDay[]): {
  weeks: { date: Date; day: ReplayCalendarDay | null }[][];
  months: { col: number; label: string }[];
} {
  // Render 53 weeks ending at the Saturday of the week containing the latest
  // day. This produces a stable GitHub-style grid regardless of dataset shape.
  if (days.length === 0) {
    return { weeks: [], months: [] };
  }
  const latest = new Date(days[0].date + "T00:00:00");
  // Saturday of the latest week's row
  const endSaturday = new Date(latest);
  endSaturday.setDate(endSaturday.getDate() + (6 - endSaturday.getDay()));
  // Sunday 52 weeks before endSaturday
  const startSunday = new Date(endSaturday);
  startSunday.setDate(startSunday.getDate() - 52 * 7 - endSaturday.getDay());

  const byKey = new Map(days.map((d) => [d.date, d]));
  const weeks: { date: Date; day: ReplayCalendarDay | null }[][] = [];
  const months: { col: number; label: string }[] = [];
  const seenMonth = new Set<string>();

  for (let w = 0; w < 53; w++) {
    const week: { date: Date; day: ReplayCalendarDay | null }[] = [];
    for (let d = 0; d < 7; d++) {
      const cell = new Date(startSunday);
      cell.setDate(cell.getDate() + w * 7 + d);
      // Trim cells after endSaturday
      if (cell > endSaturday) {
        week.push({ date: cell, day: null });
        continue;
      }
      const key = cell.toISOString().slice(0, 10);
      const day = byKey.get(key) ?? null;
      // Mark first weekday-of-week of a new month
      if (d === 0) {
        const mkey = `${cell.getFullYear()}-${cell.getMonth()}`;
        if (!seenMonth.has(mkey)) {
          seenMonth.add(mkey);
          months.push({ col: w, label: cell.toLocaleString(undefined, { month: "short" }) });
        }
      }
      week.push({ date: cell, day });
    }
    weeks.push(week);
  }
  return { weeks, months };
}

function densityLevel(day: ReplayCalendarDay): 0 | 1 | 2 | 3 | 4 {
  const total = day.nifty_rows + day.banknifty_rows + day.sensex_rows;
  if (total === 0) return 1;
  if (total < 200) return 1;
  if (total < 1000) return 2;
  if (total < 5000) return 3;
  return 4;
}

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
 */
export function SimulatorReplayCalendar({
  days,
  range,
  armedRange,
  onRangeSelect,
}: {
  days: ReplayCalendarDay[];
  range: ReplayRange | null;
  armedRange: ReplayRange | null;
  onRangeSelect: (range: ReplayRange) => void;
}) {
  const { weeks, months } = useMemo(() => buildGrid(days), [days]);

  const today = useMemo(() => new Date(), []);
  const [anchor, setAnchor] = useState<string | null>(range ? range.start : null);
  const [hovered, setHovered] = useState<string | null>(null);

  if (days.length === 0) {
    return (
      <div className="rounded-lg border bg-background/60 p-4 text-center text-sm text-muted-foreground">
        No replay data on disk yet. Record a full trading day (above) to enable replay.
      </div>
    );
  }

  function handleClick(key: string) {
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

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          {anchor
            ? `Range start ${anchor} — click an end day.`
            : "Click a day for the range start, then click another for the end. Most recent ~12 months shown."}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border bg-background/60 p-3">
        <div className="inline-flex flex-col gap-1">
          {/* Month labels */}
          <div className="flex pl-7">
            <div className="grid grid-flow-col gap-[3px]" style={{ gridTemplateColumns: `repeat(${weeks.length}, 1fr)` }}>
              {Array.from({ length: weeks.length }).map((_, col) => {
                const label = months.find((m) => m.col === col)?.label ?? "";
                return (
                  <div key={col} className="w-[12px] text-[9px] text-muted-foreground/70">
                    {label}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex gap-[3px]">
            {/* Weekday labels */}
            <div className="flex w-7 flex-col gap-[3px] pt-[1px] text-[9px] text-muted-foreground/70">
              {WEEKDAYS.map((w, i) => (
                <div key={w} className="h-[12px] leading-[12px]" style={{ marginTop: i === 0 ? 0 : i * 15 }}>
                  {w}
                </div>
              ))}
            </div>

            {/* Cells */}
            <div
              className="grid grid-flow-col gap-[3px]"
              onMouseLeave={() => setHovered(null)}
              style={{ gridTemplateColumns: `repeat(${weeks.length}, 1fr)` }}
            >
              {weeks.map((week, wi) =>
                week.map((cell, di) => {
                  const key = cell.date.toISOString().slice(0, 10);
                  const day = cell.day;
                  const lvl = day ? densityLevel(day) : 0;
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
                  const noData = !day || lvl === 0;

                  return (
                    <button
                      key={`${wi}-${di}`}
                      type="button"
                      disabled={noData || isFuture}
                      onClick={() => day && handleClick(day.date)}
                      onMouseEnter={() => day && setHovered(day.date)}
                      title={
                        day
                          ? `${day.date} · NIFTY ${day.nifty_rows} · BANKNIFTY ${day.banknifty_rows} · SENSEX ${day.sensex_rows}`
                          : key
                      }
                      className={cn(
                        "h-[12px] w-[12px] rounded-[2px] border transition-colors",
                        noData && "border-border/40 bg-muted/40",
                        !noData && lvl === 1 && "border-emerald-500/30 bg-emerald-500/20",
                        !noData && lvl === 2 && "border-emerald-500/40 bg-emerald-500/40",
                        !noData && lvl === 3 && "border-emerald-500/50 bg-emerald-500/60",
                        !noData && lvl === 4 && "border-emerald-500/60 bg-emerald-500/80",
                        isToday && !isArmed && !inSelectedRange && "ring-1 ring-amber-400/60",
                        inPreview && !inSelectedRange && "ring-1 ring-primary/50",
                        inSelectedRange && !isRangeEdge && "ring-1 ring-primary/70",
                        isRangeEdge && "ring-2 ring-primary",
                        isArmed && "ring-2 ring-amber-400",
                        isFuture && "opacity-40",
                      )}
                      aria-label={day ? `Replay ${day.date}` : `No data ${key}`}
                      data-testid={`replay-day-${key}`}
                    />
                  );
                }),
              )}
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
          <span>Less</span>
          {[0, 1, 2, 3, 4].map((lvl) => (
            <span
              key={lvl}
              className={cn(
                "h-[10px] w-[10px] rounded-[2px] border",
                lvl === 0 && "border-border/40 bg-muted/40",
                lvl === 1 && "border-emerald-500/30 bg-emerald-500/20",
                lvl === 2 && "border-emerald-500/40 bg-emerald-500/40",
                lvl === 3 && "border-emerald-500/50 bg-emerald-500/60",
                lvl === 4 && "border-emerald-500/60 bg-emerald-500/80",
              )}
            />
          ))}
          <span>More</span>
          <span className="ml-3 inline-flex items-center gap-1">
            <span className="h-[10px] w-[10px] rounded-[2px] ring-2 ring-primary" /> range
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-[10px] w-[10px] rounded-[2px] ring-2 ring-amber-400" /> armed
          </span>
        </div>
      </div>
    </div>
  );
}
