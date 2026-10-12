import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ReplayRecord } from "@/lib/forecastReplay/types";
import { anchorDays, forecastReturnPct } from "@/lib/forecastReplay/view";
import { fmtPct } from "@/lib/trackScoreboardUtils";

type Week = { week_start: string; records: ReplayRecord[] };

interface Props {
  weeks: Week[];
  anchor: string;
  horizon: number;
  onAnchorChange: (day: string) => void;
}

/** The week containing `anchor` (the last week starting on or before it). */
export function currentWeek(weeks: Week[], anchor: string): Week | undefined {
  return [...weeks].reverse().find((w) => w.week_start <= anchor) ?? weeks[0];
}

/** Week navigation + one chip per recorded day; the chart (not this component) draws the faded week's paths. */
export function WeekView({ weeks, anchor, horizon, onAnchorChange }: Props) {
  const week = currentWeek(weeks, anchor);
  if (!week) return <p className="text-[11px] text-muted-foreground">No recorded forecasts to group into weeks.</p>;
  const i = weeks.indexOf(week);
  const goto = (w?: Week) => {
    if (!w) return;
    const d = anchorDays(w.records);
    onAnchorChange(d[d.length - 1]);
  };
  return (
    <div className="space-y-2" data-testid="week-view">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <button type="button" disabled={i <= 0} onClick={() => goto(weeks[i - 1])} className="inline-flex items-center rounded border px-2 py-0.5 text-[10px] hover:bg-muted disabled:opacity-40">
          <ChevronLeft className="h-3 w-3" />Prev week
        </button>
        <span className="font-medium">Week of {week.week_start}</span>
        <button type="button" disabled={i >= weeks.length - 1} onClick={() => goto(weeks[i + 1])} className="inline-flex items-center rounded border px-2 py-0.5 text-[10px] hover:bg-muted disabled:opacity-40">
          Next week<ChevronRight className="h-3 w-3" />
        </button>
        <span className="text-muted-foreground">{week.records.length} forecast{week.records.length === 1 ? "" : "s"} issued</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {anchorDays(week.records).map((d) => {
          const rets = week.records.filter((r) => r.issue_day === d).map((r) => forecastReturnPct(r, horizon)).filter((v): v is number => v != null);
          const mean = rets.length ? rets.reduce((a, b) => a + b, 0) / rets.length : null;
          return (
            <button key={d} type="button" onClick={() => onAnchorChange(d)}
              className={cn("rounded-md border px-2 py-0.5 text-[10px] tabular-nums", d === anchor ? "border-primary bg-primary/10 text-primary" : "border-border/60 text-muted-foreground hover:bg-muted/50")}>
              {new Date(`${d}T00:00:00Z`).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })} · {fmtPct(mean)}
            </button>
          );
        })}
      </div>
    </div>
  );
}
