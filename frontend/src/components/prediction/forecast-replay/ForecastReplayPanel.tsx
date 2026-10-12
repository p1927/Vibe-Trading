import { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { ForecastReplayChart } from "@/components/charts/ForecastReplayChart";
import { weekGroups } from "@/lib/forecastReplay/paths";
import { baseSeriesColors, type SourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { Provenance, ReplayRecord } from "@/lib/forecastReplay/types";
import {
  anchorDays, latestMaturedDay, maturityBuckets, OVERLAY_SOURCE_ID, overlayChip, overlayRecord, sourceNote, type LiveOverlay,
} from "@/lib/forecastReplay/view";
import { useDarkMode } from "@/hooks/useDarkMode";
import { cn } from "@/lib/utils";
import { DayDetail } from "./DayDetail";
import { DayStepper } from "./DayStepper";
import { SourcePicker } from "./SourcePicker";
import { useReplayView } from "./useReplayView";
import { currentWeek, WeekView } from "./WeekView";

type Mode = "day" | "week";

export interface ForecastReplayPanelProps {
  ticker?: string;
  horizon: number;
  defaultSources: string[];
  defaultProvenance?: Provenance[];
  /** Today's artifact forecast (+ factor-workbench what-if) drawn as a synthetic live record. */
  liveOverlay?: LiveOverlay;
  modes?: Mode[];
  /** Single-select picker (scoreboard single-track mode). */
  single?: boolean;
  height?: number;
  emptyHint?: string;
  windowDays?: number;
}

const WINDOWS = [30, 90, 180, 365];

export function ForecastReplayPanel({
  ticker = "NIFTY", horizon, defaultSources, defaultProvenance = ["live"], liveOverlay, modes = ["day", "week"],
  single, height = 380, emptyHint, windowDays,
}: ForecastReplayPanelProps) {
  const v = useReplayView({ ticker, horizon, defaultSources, defaultProvenance, windowDays, single });
  const { dark } = useDarkMode();
  const [mode, setMode] = useState<Mode>("day");
  const [anchor, setAnchor] = useState<string | null>(null);

  // a fresh Run analysis changes the artifact's as_of: refetch so today's recorded forecast shows up
  const firstAsOf = useRef(liveOverlay?.asOf);
  useEffect(() => {
    if (liveOverlay?.asOf !== firstAsOf.current) {
      firstAsOf.current = liveOverlay?.asOf;
      v.refetch();
    }
  }, [liveOverlay?.asOf]); // eslint-disable-line react-hooks/exhaustive-deps

  const dated = useMemo(() => (liveOverlay ? overlayRecord(liveOverlay, v.loaded, horizon) : null), [liveOverlay, v.loaded, horizon]);
  // an unsimulated overlay duplicates today's recorded headline: draw it only when it adds information
  const overlay = dated && (dated.meta.simulated || !v.records.some((r) => r.issue_day === dated.issue_day && r.source_id === "headline")) ? dated : null;
  // no recorded date at this horizon: say what the what-if is, draw nothing
  const chip = liveOverlay && !dated ? overlayChip(liveOverlay, horizon) : null;

  const records: ReplayRecord[] = useMemo(() => (overlay ? [...v.records, overlay] : v.records), [v.records, overlay]);
  const styles = useMemo(() => {
    const s: Record<string, SourceStyle> = { ...v.styles };
    if (overlay) {
      s[OVERLAY_SOURCE_ID] = {
        label: overlay.meta.simulated ? "Live forecast (what-if)" : "Live forecast", color: baseSeriesColors().overlay, dash: [], hatch: false,
      };
    }
    return s;
  }, [v.styles, overlay, dark]);

  const actuals = v.bundle?.actuals ?? [];
  const days = useMemo(() => anchorDays(records), [records]);
  const anchorDay = anchor ?? days[days.length - 1] ?? actuals[actuals.length - 1]?.date ?? "";
  const weeks = useMemo(() => weekGroups(records), [records]);
  const focusDays = useMemo(
    () => (mode === "week" ? anchorDays(currentWeek(weeks, anchorDay)?.records ?? []) : [anchorDay]),
    [mode, weeks, anchorDay],
  );
  const dayRecords = records.filter((r) => r.issue_day === anchorDay);
  const notes = v.allSources.filter((s) => v.selected.includes(s.source_id) && sourceNote(s));
  const empty = v.selected.filter((id) => !v.bundle?.records.some((r) => r.source_id === id) && !notes.some((n) => n.source_id === id));

  return (
    <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm" data-testid="forecast-replay-panel">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[10px]">
        <div className="flex rounded-lg border border-border/60 p-0.5">
          {modes.map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={cn("rounded-md px-2.5 py-1 font-medium capitalize", mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>
              {m} view
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 text-muted-foreground">
          <label className="inline-flex items-center gap-1">
            Window
            <select value={v.windowDays} onChange={(e) => v.setWindowDays(Number(e.target.value))} className="rounded border bg-background px-1 py-0.5">
              {WINDOWS.map((w) => <option key={w} value={w}>{w}d</option>)}
            </select>
          </label>
          <button type="button" onClick={v.refetch} title="Reload" className="inline-flex items-center rounded border px-1.5 py-0.5 hover:bg-muted">
            <RefreshCw className={cn("h-3 w-3", v.loading && "animate-spin")} />
          </button>
        </div>
      </div>

      <SourcePicker
        sources={v.allSources} selected={v.selected} provenance={v.provenance} styles={v.styles} single={single} atCap={v.atSourceCap}
        onToggleSource={v.toggleSource} onToggleProvenance={v.toggleProvenance}
      />

      {v.error ? (
        <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[11px] text-red-700 dark:text-red-300">
          Forecast replay failed: {v.error}
        </p>
      ) : null}
      {notes.map((s) => (
        <p key={s.source_id} className="text-[11px] text-amber-700 dark:text-amber-400">{s.label}: {sourceNote(s)}</p>
      ))}
      {empty.length && v.bundle ? (
        <p className="text-[11px] text-muted-foreground">
          No {horizon}d forecasts in this window for {empty.map((id) => v.styles[id]?.label ?? id).join(", ")}.
        </p>
      ) : null}

      <DayStepper
        days={days} anchor={anchorDay} onChange={setAnchor}
        revisions={Math.max(1, ...dayRecords.map((r) => Number(r.meta?.revisions ?? 1)))}
        buckets={dayRecords.length ? maturityBuckets(dayRecords) : undefined}
        latestMatured={latestMaturedDay(records)}
      />

      <ForecastReplayChart
        records={records} actuals={actuals} anchorDay={anchorDay} focusDays={focusDays} horizon={horizon}
        sourceStyles={styles} onAnchorChange={setAnchor} height={height} loading={v.loading} emptyHint={emptyHint}
      />

      {chip ? <p className="text-[10px] text-muted-foreground" data-testid="overlay-chip">{chip}</p> : null}

      {mode === "week" ? <WeekView weeks={weeks} anchor={anchorDay} horizon={horizon} onAnchorChange={setAnchor} /> : null}
      <DayDetail records={dayRecords} horizon={horizon} styles={styles} />
      <p className="text-[10px] text-muted-foreground">
        Markers = days a {horizon}d forecast was recorded (above the line = predicted up). Click a marker or any day, or use the stepper;
        the green line is what Nifty actually did from the anchor.
      </p>
    </div>
  );
}
