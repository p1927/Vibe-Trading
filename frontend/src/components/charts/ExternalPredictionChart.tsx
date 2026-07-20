import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { ExternalPredictionRecord } from "@/lib/api";

interface Props {
  record: ExternalPredictionRecord;
  sourceName?: string;
  height?: number;
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function buildPathPoints(record: ExternalPredictionRecord): {
  dates: string[];
  spotLine: (number | null)[];
  midLine: (number | null)[];
  bandLow: (number | null)[];
  bandHigh: (number | null)[];
} {
  const spot = record.spot_at_fetch ?? null;
  const mid = record.target?.mid ?? record.target?.high ?? record.target?.low ?? null;
  const low = record.target?.low ?? mid;
  const high = record.target?.high ?? mid;
  const start = (record.as_of || new Date().toISOString()).slice(0, 10);
  const end = record.target_date?.slice(0, 10) || addDays(start, record.horizon_days ?? 14);
  const dates = [start, end];
  return {
    dates,
    spotLine: [spot, spot],
    midLine: [spot, mid],
    bandLow: [spot, low],
    bandHigh: [spot, high],
  };
}

export function ExternalPredictionChart({ record, sourceName, height = 220 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const theme = getChartTheme();

  const path = useMemo(() => buildPathPoints(record), [record]);
  const hasData = record.fetch_status === "ok" && path.midLine[1] != null;

  useEffect(() => {
    if (!ref.current || !hasData) return;
    const chart = echarts.init(ref.current, dark ? "dark" : undefined);
    const color =
      record.direction === "bullish"
        ? "#22c55e"
        : record.direction === "bearish"
          ? "#ef4444"
          : "#3b82f6";

    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 48, right: 16, top: 24, bottom: 28 },
      tooltip: {
        trigger: "axis",
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const date = String((rows[0] as { axisValue?: string })?.axisValue ?? "");
          const lines = rows
            .map((row) => {
              const p = row as { seriesName?: string; value?: number | null };
              if (p.value == null) return "";
              return `${p.seriesName}: ${fmtLevel(p.value)}`;
            })
            .filter(Boolean);
          return [date, ...lines].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: path.dates.map((d) =>
          new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", {
            day: "numeric",
            month: "short",
          }),
        ),
        axisLine: { lineStyle: { color: theme.gridColor } },
        axisLabel: { color: theme.textColor, fontSize: 10 },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLine: { show: false },
        splitLine: { lineStyle: { color: theme.gridColor, type: "dashed" } },
        axisLabel: {
          color: theme.textColor,
          fontSize: 10,
          formatter: (v: number) => fmtLevel(v),
        },
      },
      series: [
        {
          name: "Range high",
          type: "line",
          data: path.bandHigh,
          lineStyle: { opacity: 0 },
          stack: "band",
          symbol: "none",
          areaStyle: { opacity: 0 },
        },
        {
          name: "Range",
          type: "line",
          data: path.bandLow.map((low, i) => {
            const hi = path.bandHigh[i];
            if (low == null || hi == null) return null;
            return hi - low;
          }),
          lineStyle: { opacity: 0 },
          stack: "band",
          symbol: "none",
          areaStyle: { color: `${color}33` },
        },
        {
          name: sourceName || "Forecast",
          type: "line",
          data: path.midLine,
          smooth: true,
          showSymbol: true,
          symbolSize: 6,
          lineStyle: { width: 2.5, color },
          itemStyle: { color },
        },
        {
          name: "Spot",
          type: "line",
          data: path.spotLine,
          smooth: false,
          showSymbol: false,
          lineStyle: { width: 1, type: "dashed", color: theme.textColor },
        },
      ],
    });

    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [dark, hasData, path, record.direction, sourceName, theme]);

  if (!hasData) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed border-border/60 bg-muted/20 text-[11px] text-muted-foreground"
        style={{ height }}
      >
        {record.error_message || "No forecast data for this source"}
      </div>
    );
  }

  return <div ref={ref} style={{ width: "100%", height }} />;
}
