import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { LightweightChartZoomBar } from "@/components/charts/LightweightChartZoomBar";
import { getChartTheme } from "@/lib/chart-theme";
import { createLightweightChart } from "@/lib/lightweightChartOptions";
import { useDarkMode } from "@/hooks/useDarkMode";
import { ForecastGapPrimitive } from "@/components/charts/forecastGapPrimitive";
import { GAP_ALPHA, maturedUntil, shadedRecords } from "@/lib/forecastReplay/gap";
import { band, recordPath } from "@/lib/forecastReplay/paths";
import type { SourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayActual, ReplayRecord } from "@/lib/forecastReplay/types";
import { forecastReturnPct } from "@/lib/forecastReplay/view";
import { fmtPct } from "@/lib/trackScoreboardUtils";

export interface ForecastReplayChartProps {
  /** Every record in the window (day markers); only `focusDays` are drawn as paths. */
  records: ReplayRecord[];
  actuals: ReplayActual[];
  anchorDay: string;
  /** Issue days drawn as paths (week view); defaults to the anchor day. Other days are faded. */
  focusDays?: string[];
  horizon: number;
  /** By `source_id`; a record whose source has no style is not drawn. */
  sourceStyles: Record<string, SourceStyle>;
  onAnchorChange?: (day: string) => void;
  height?: number;
  loading?: boolean;
  emptyHint?: string;
}

const LINE_STYLE = { solid: LineStyle.Solid, dashed: LineStyle.Dashed, dotted: LineStyle.Dotted, approx: LineStyle.LargeDashed };

/** Works for the `#rrggbb` theme colours and the `hsl(...)` hash colours. */
function withAlpha(color: string, alpha: number): string {
  if (color.startsWith("hsl(")) return color.replace("hsl(", "hsla(").replace(")", `, ${alpha})`);
  return `${color}${Math.round(alpha * 255).toString(16).padStart(2, "0")}`;
}

function timeToIsoDay(time: Time): string | null {
  if (typeof time === "string") return time.slice(0, 10);
  if (typeof time === "number") return new Date(time * 1000).toISOString().slice(0, 10);
  if (typeof time === "object" && time && "year" in time) {
    const { year, month, day } = time;
    return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }
  return null;
}

function lineStyleOf(style: SourceStyle, approximate: boolean): LineStyle {
  if (approximate) return LINE_STYLE.approx;
  return style.dash.length === 0 ? LINE_STYLE.solid : style.hatch ? LINE_STYLE.dotted : LINE_STYLE.dashed;
}

/** Ascending, de-duplicated line data (lightweight-charts requires strictly increasing times). */
function lineData(vertices: { date: string; level: number }[]) {
  const byDate = new Map(vertices.map((v) => [v.date, v.level]));
  return [...byDate].sort((a, b) => a[0].localeCompare(b[0])).map(([date, level]) => ({ time: date as Time, value: level }));
}

function focusWindow(chart: IChartApi, actuals: ReplayActual[], anchorDay: string, horizon: number, pathLen: number) {
  const idx = actuals.findIndex((a) => a.date === anchorDay);
  if (idx < 0) {
    chart.timeScale().fitContent();
    return;
  }
  try {
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, idx - 30), to: idx + Math.max(horizon + 2, pathLen) });
  } catch {
    chart.timeScale().fitContent();
  }
}

export function ForecastReplayChart({
  records, actuals, anchorDay, focusDays, horizon, sourceStyles, onAnchorChange, height = 380, loading = false, emptyHint,
}: ForecastReplayChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const baseRef = useRef<ISeriesApi<"Line"> | null>(null);
  const actualPathRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const gapRef = useRef<ForecastGapPrimitive | null>(null);
  const dynamicRef = useRef<ISeriesApi<"Line">[]>([]);
  const clickRef = useRef(onAnchorChange);
  clickRef.current = onAnchorChange;
  const { dark } = useDarkMode();
  const [epoch, setEpoch] = useState(0);
  const [activeChart, setActiveChart] = useState<IChartApi | null>(null);

  const focus = useMemo(() => new Set(focusDays ?? [anchorDay]), [focusDays, anchorDay]);
  const drawn = useMemo(
    () => records.filter((r) => focus.has(r.issue_day) && sourceStyles[r.source_id]),
    [records, focus, sourceStyles],
  );
  const hasActuals = actuals.length > 0;

  const mount = useCallback(
    (container: HTMLDivElement) => {
      chartRef.current?.remove();
      chartRef.current = null;
      dynamicRef.current = [];
      const width = container.clientWidth;
      if (width <= 0) return;
      const t = getChartTheme();
      const chart = createLightweightChart(container, height);
      chart.applyOptions({ width });
      baseRef.current = chart.addSeries(LineSeries, { color: t.infoColor, lineWidth: 2, title: "Nifty", lastValueVisible: true, priceLineVisible: false });
      markersRef.current = createSeriesMarkers(baseRef.current, []);
      gapRef.current = new ForecastGapPrimitive();
      baseRef.current.attachPrimitive(gapRef.current);
      actualPathRef.current = chart.addSeries(LineSeries, { color: t.upColor, lineWidth: 2, title: "Actual path", lastValueVisible: true, priceLineVisible: false });
      chart.subscribeClick((param) => {
        const day = param.time ? timeToIsoDay(param.time) : null;
        if (day) clickRef.current?.(day);
      });
      chartRef.current = chart;
      setActiveChart(chart);
      setEpoch((v) => v + 1);
    },
    // dark re-themes the chart: remount so grid/text colours are re-read
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [height, dark],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !hasActuals) return;
    mount(el);
    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      const w = containerRef.current.clientWidth;
      if (!chartRef.current && w > 0) mount(containerRef.current);
      else if (chartRef.current && w > 0) chartRef.current.applyOptions({ width: w });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
      dynamicRef.current = [];
      setActiveChart(null);
    };
  }, [mount, hasActuals]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !baseRef.current || !actualPathRef.current) return;
    const t = getChartTheme();
    for (const s of dynamicRef.current) chart.removeSeries(s);
    dynamicRef.current = [];

    baseRef.current.setData(actuals.map((a) => ({ time: a.date as Time, value: a.close })));
    actualPathRef.current.setData(actuals.filter((a) => a.date >= anchorDay).map((a) => ({ time: a.date as Time, value: a.close })));

    const closeByDay = new Map(actuals.map((a) => [a.date, a.close]));
    const sourcesInWindow = new Set(records.filter((r) => sourceStyles[r.source_id] && !r.meta?.overlay).map((r) => r.source_id));
    const dayMarkers: SeriesMarker<Time>[] = [];
    for (const r of records) {
      const style = sourceStyles[r.source_id];
      const ret = forecastReturnPct(r, horizon);
      if (!style || r.meta?.overlay || ret == null || !closeByDay.has(r.issue_day)) continue;
      const isAnchor = r.issue_day === anchorDay;
      dayMarkers.push({
        time: r.issue_day as Time,
        position: ret >= 0 ? "aboveBar" : "belowBar",
        shape: isAnchor ? "square" : "circle",
        color: style.color,
        text: sourcesInWindow.size === 1 || (isAnchor && sourcesInWindow.size <= 4) ? fmtPct(ret) : undefined,
      });
    }
    dayMarkers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    markersRef.current?.setMarkers(dayMarkers);

    let pathLen = 0;
    for (const r of drawn) {
      const style = sourceStyles[r.source_id];
      const faded = r.issue_day !== anchorDay;
      const { vertices, approximate } = recordPath(r);
      pathLen = Math.max(pathLen, vertices.length);
      const color = r.meta?.overlay ? t.warningColor : style.color;
      const series = chart.addSeries(LineSeries, {
        color: faded ? withAlpha(color, 0.4) : color,
        lineWidth: faded ? 1 : 2,
        lineStyle: lineStyleOf(style, approximate),
        lastValueVisible: false,
        priceLineVisible: false,
      });
      series.setData(lineData(vertices));
      dynamicRef.current.push(series);
      if (faded) continue;

      const errMarkers: SeriesMarker<Time>[] = r.points
        .filter((p) => p.matured && p.error_pct != null)
        .map((p) => ({
          time: p.target_date as Time,
          position: "inBar" as const,
          shape: "circle" as const,
          color: p.in_band === false ? t.downColor : p.in_band ? t.upColor : t.infoColor,
          text: fmtPct(p.error_pct),
        }));
      createSeriesMarkers(series, errMarkers.sort((a, b) => String(a.time).localeCompare(String(b.time))));

      const b = band(r);
      if (b) {
        for (const key of ["p10", "p90"] as const) {
          const s = chart.addSeries(LineSeries, {
            color: withAlpha(color, 0.5), lineWidth: 1, lineStyle: LineStyle.Dashed, lastValueVisible: false, priceLineVisible: false,
          });
          s.setData(lineData([{ date: r.issue_day, level: r.spot_at_issue }, ...b.map((x) => ({ date: x.date, level: x[key] }))]));
          dynamicRef.current.push(s);
        }
      }
    }
    gapRef.current?.setSpec({
      items: shadedRecords(drawn, anchorDay).map((record) => ({
        record, alpha: GAP_ALPHA[record.provenance] * (record.issue_day === anchorDay ? 1 : 0.5),
      })),
      actuals, above: t.upColor, below: t.downColor,
    });
    focusWindow(chart, actuals, anchorDay, horizon, pathLen);
  }, [actuals, records, drawn, anchorDay, horizon, sourceStyles, epoch]);

  const legend = useMemo(() => {
    const seen = new Map<string, { style: SourceStyle; approx: boolean }>();
    for (const r of drawn) if (!seen.has(r.source_id)) seen.set(r.source_id, { style: sourceStyles[r.source_id], approx: recordPath(r).approximate });
    return [...seen];
  }, [drawn, sourceStyles]);

  if (loading && !hasActuals) {
    return <div className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground" style={{ height }}>Loading Nifty price history…</div>;
  }
  if (!hasActuals) {
    return <div className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground" style={{ height }}>No Nifty price history for this window.</div>;
  }

  return (
    <div className="space-y-2" data-testid="forecast-replay-chart">
      <div ref={containerRef} className="w-full" style={{ height }} />
      <LightweightChartZoomBar chart={activeChart} />
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-4 bg-primary" />Nifty close</span>
        <span className="inline-flex items-center gap-1"><span className="inline-block h-0.5 w-4 bg-emerald-500" />Actual path from anchor</span>
        {legend.map(([id, { style, approx }]) => (
          <span key={id} className="inline-flex items-center gap-1">
            <span className="inline-block h-0.5 w-4" style={{ background: style.color, opacity: style.dash.length ? 0.7 : 1 }} />
            {style.label}
            {style.hatch && !/backtest/i.test(style.label) ? " (backtest)" : !style.hatch && style.dash.length ? " (replay)" : ""}
            {approx ? " · approx." : ""}
          </span>
        ))}
        {legend.some(([, l]) => l.approx) ? <span>approx. = straight line to the target, not a recorded path</span> : null}
        {shadedRecords(drawn, anchorDay).some((r) => maturedUntil(r)) ? (
          <span className="inline-flex items-center gap-1" data-testid="gap-legend">
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: getChartTheme().upColor, opacity: 0.45 }} />
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: getChartTheme().downColor, opacity: 0.45 }} />
            shaded = gap forecast vs actual, matured days only (green: actual above forecast, red: below)
          </span>
        ) : null}
        {legend.length ? <span>band = p10/p90 · dots at target dates show the realised error</span> : null}
      </div>
      {!drawn.length ? (
        <div className="rounded-lg border border-dashed border-muted-foreground/30 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
          {emptyHint ?? `No ${horizon}d forecast was recorded for ${anchorDay} by the selected sources.`}
        </div>
      ) : null}
    </div>
  );
}
