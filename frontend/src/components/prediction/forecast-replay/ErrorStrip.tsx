import { useEffect, useMemo, useRef } from "react";
import { initEChart } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { errorSeries } from "@/lib/forecastReplay/paths";
import type { SourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { ReplayRecord } from "@/lib/forecastReplay/types";
import { rollingMean } from "@/lib/forecastReplay/view";

export const MAX_STRIP_SOURCES = 4;
export const ROLLING_WINDOW = 5;

interface Props {
  records: ReplayRecord[];
  horizon: number;
  ids: string[];
  styles: Record<string, SourceStyle>;
}

/** Bars of the matured error per issue day plus a rolling mean, one pair of series per source (max 4). */
export function errorStripOption(records: ReplayRecord[], horizon: number, ids: string[], styles: Record<string, SourceStyle>) {
  const t = getChartTheme();
  const drawn = ids.slice(0, MAX_STRIP_SOURCES);
  const perSource = drawn.map((id) => ({ id, rows: errorSeries(records.filter((r) => r.source_id === id), horizon) }));
  const days = [...new Set(perSource.flatMap((p) => p.rows.map((r) => r.issue_day)))].sort();
  const series = perSource.flatMap(({ id, rows }) => {
    const color = styles[id]?.color ?? t.infoColor;
    const label = styles[id]?.label ?? id;
    const byDay = new Map(rows.map((r) => [r.issue_day, r.error_pct]));
    const mean = new Map(rollingMean(rows.map((r) => r.error_pct), ROLLING_WINDOW).map((m, i) => [rows[i].issue_day, m]));
    return [
      { name: label, type: "bar" as const, data: days.map((d) => byDay.get(d) ?? null), itemStyle: { color: `${color}aa` } },
      {
        name: `${label} · ${ROLLING_WINDOW}-run mean`, type: "line" as const, showSymbol: false, connectNulls: true,
        data: days.map((d) => mean.get(d) ?? null), lineStyle: { width: 2, color }, itemStyle: { color },
      },
    ];
  });
  return {
    days,
    option: {
      tooltip: { trigger: "axis" as const, valueFormatter: (v: unknown) => (typeof v === "number" ? `${v.toFixed(2)}%` : "—") },
      // one legend entry per source (its bars and mean line share the colour), kept clear of the plot area
      legend: {
        top: 0, left: 0, right: 0, type: "scroll" as const, itemWidth: 14, itemHeight: 8, selectedMode: false,
        data: drawn.map((id) => styles[id]?.label ?? id), textStyle: { color: t.textColor, fontSize: 10 },
      },
      grid: { left: 48, right: 16, top: 44, bottom: 28 },
      xAxis: { type: "category" as const, data: days.map((d) => d.slice(5)), axisLabel: { fontSize: 9, color: t.textColor } },
      yAxis: { type: "value" as const, axisLabel: { fontSize: 9, color: t.textColor, formatter: "{value}%" } },
      series,
    },
  };
}

export function ErrorStrip({ records, horizon, ids, styles }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const built = useMemo(() => errorStripOption(records, horizon, ids, styles), [records, horizon, ids, styles, dark]); // eslint-disable-line react-hooks/exhaustive-deps
  const hasData = built.days.length > 0;

  useEffect(() => {
    const el = ref.current;
    if (!el || !hasData) return;
    const chart = initEChart(el);
    chart.setOption(built.option);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [built, hasData]);

  return (
    <div className="space-y-1" data-testid="error-strip">
      <p className="text-[10px] text-muted-foreground">
        Forecast error per issue day at {horizon}d (actual minus forecast, %) with a {ROLLING_WINDOW}-run rolling mean
        {ids.length > MAX_STRIP_SOURCES ? ` — first ${MAX_STRIP_SOURCES} of ${ids.length} sources drawn, all are in the table` : ""}.
      </p>
      {hasData ? (
        <div ref={ref} className="h-[220px] w-full" />
      ) : (
        <p className="rounded-lg border border-dashed px-3 py-6 text-center text-[11px] text-muted-foreground">
          No matured {horizon}d forecasts in this window yet.
        </p>
      )}
    </div>
  );
}
