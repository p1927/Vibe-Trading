import { ChevronLeft, ChevronRight } from "lucide-react";
import type { Maturity } from "@/lib/forecastReplay/view";

interface Props {
  /** Ascending days that have a recorded forecast. */
  days: string[];
  anchor: string;
  onChange: (day: string) => void;
  /** Runs recorded for the anchor day (the last one is shown). */
  revisions?: number;
  buckets?: Record<Maturity, number>;
  latestMatured?: string | null;
}

/** Previous / next recorded day relative to `anchor`; stays put at the ends. */
export function stepDay(days: string[], anchor: string, dir: -1 | 1): string {
  const next = dir < 0 ? [...days].reverse().find((d) => d < anchor) : days.find((d) => d > anchor);
  return next ?? anchor;
}

export function DayStepper({ days, anchor, onChange, revisions, buckets, latestMatured }: Props) {
  const recorded = days.includes(anchor);
  const idx = recorded ? days.indexOf(anchor) : Math.max(0, days.filter((d) => d <= anchor).length - 1);
  const atStart = !days.length || stepDay(days, anchor, -1) === anchor;
  const atEnd = !days.length || stepDay(days, anchor, 1) === anchor;
  return (
    <div
      role="group"
      aria-label="Forecast day stepper"
      tabIndex={0}
      className="space-y-1 rounded-lg outline-none focus-visible:ring-1 focus-visible:ring-primary"
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        onChange(stepDay(days, anchor, e.key === "ArrowLeft" ? -1 : 1));
      }}
    >
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <button type="button" disabled={atStart} onClick={() => onChange(stepDay(days, anchor, -1))}
          className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] hover:bg-muted disabled:opacity-40">
          <ChevronLeft className="h-3 w-3" />Prev forecast
        </button>
        <input
          type="range" min={0} max={Math.max(0, days.length - 1)} value={idx} disabled={!days.length}
          aria-label="Forecast day"
          onChange={(e) => days[Number(e.target.value)] && onChange(days[Number(e.target.value)])}
          className="min-w-[8rem] flex-1 accent-primary"
        />
        <button type="button" disabled={atEnd} onClick={() => onChange(stepDay(days, anchor, 1))}
          className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[10px] hover:bg-muted disabled:opacity-40">
          Next forecast<ChevronRight className="h-3 w-3" />
        </button>
        <span className="tabular-nums font-medium">{anchor || "—"}</span>
      </div>
      <p className="text-[10px] text-muted-foreground">
        {days.length} day{days.length === 1 ? "" : "s"} with a recorded forecast
        {!recorded && anchor ? ` · none recorded on ${anchor}` : ""}
        {revisions && revisions > 1 ? ` · ${revisions} runs that day (last shown)` : ""}
        {buckets ? ` · matured ${buckets.matured} / partly ${buckets.partial} / pending ${buckets.pending}` : ""}
        {latestMatured ? ` · latest matured ${latestMatured}` : ""}
      </p>
    </div>
  );
}
