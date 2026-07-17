import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexTrackChartPayload } from "@/lib/api";
import { fmtPct, trackColor } from "@/lib/trackScoreboardUtils";

interface Props {
  chart: IndexTrackChartPayload | null | undefined;
  height?: number;
  showCombiners?: boolean;
}

export function TrackScoreboardChart({ chart, height = 360, showCombiners = false }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const theme = getChartTheme();

  const option = useMemo(() => {
    if (!chart?.eval_dates?.length) return null;

    let dates = [...chart.eval_dates];
    const live = chart.live_point;
    if (live?.date && !dates.includes(live.date)) {
      dates = [...dates, live.date];
    }

    const series: object[] = [];
    const actual = chart.actual_series ?? [];
    series.push({
      name: "Actual Nifty (forward return)",
      type: "line",
      data: dates.map((d) => actual.find((r) => r.date === d)?.actual_pct ?? null),
      lineStyle: { width: 3, color: trackColor("actual") },
      itemStyle: { color: trackColor("actual") },
      symbol: "diamond",
      symbolSize: 7,
      z: 10,
    });

    for (const track of chart.track_series ?? []) {
      if (!showCombiners && track.track_id.startsWith("combiner:")) continue;
      const lineData = dates.map((d) => {
        const pt = track.points?.find((p) => p.date === d);
        if (pt) return pt.predicted_pct;
        if (live?.date === d && live.tracks?.[track.track_id] != null) {
          return live.tracks[track.track_id];
        }
        return null;
      });
      series.push({
        name: track.label ?? track.track_id,
        type: "line",
        data: lineData,
        lineStyle: {
          width: track.track_id.startsWith("combiner:") ? 1.5 : 2,
          type: track.track_id.startsWith("combiner:") ? "dashed" : "solid",
          color: trackColor(track.track_id),
        },
        itemStyle: { color: trackColor(track.track_id) },
        symbol: live?.tracks?.[track.track_id] != null ? "triangle" : "circle",
        symbolSize: live?.tracks?.[track.track_id] != null ? 8 : 4,
      });
    }

    return {
      backgroundColor: "transparent",
      textStyle: { color: theme.textColor, fontSize: 11 },
      grid: { left: 48, right: 16, top: 36, bottom: 56 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const axis = rows[0] as { axisValue?: string };
          const day = axis?.axisValue ?? "";
          const body = rows
            .filter((p) => (p as { value?: number | null }).value != null)
            .map((p) => {
              const row = p as { seriesName?: string; value?: number };
              return `${row.seriesName}: ${fmtPct(row.value)}`;
            })
            .join("<br/>");
          return `${body}<br/><span style="opacity:0.7">${day}</span>`;
        },
      },
      legend: {
        type: "scroll",
        bottom: 0,
        textStyle: { color: theme.textColor, fontSize: 10 },
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { color: theme.textColor, rotate: 35, fontSize: 9 },
        axisLine: { lineStyle: { color: theme.gridColor } },
      },
      yAxis: {
        type: "value",
        name: `${chart.horizon_days ?? 14}d return %`,
        nameTextStyle: { color: theme.textColor, fontSize: 10 },
        axisLabel: { color: theme.textColor, formatter: (v: number) => `${v}%` },
        splitLine: { lineStyle: { color: theme.gridColor } },
      },
      series,
    };
  }, [chart, showCombiners, theme]);

  useEffect(() => {
    if (!ref.current || !option) return;
    const instance = echarts.init(ref.current, dark ? "dark" : undefined);
    instance.setOption(option);
    const onResize = () => instance.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      instance.dispose();
    };
  }, [option, dark]);

  if (!chart?.eval_dates?.length) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed text-[12px] text-muted-foreground"
        style={{ height }}
      >
        No walk-forward eval points yet — run Recompute scoreboard.
      </div>
    );
  }

  return <div ref={ref} style={{ width: "100%", height }} />;
}
