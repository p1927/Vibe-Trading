import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexTrackChartPayload } from "@/lib/api";

interface Props {
  chart: IndexTrackChartPayload | null | undefined;
  height?: number;
}

export function NiftyCloseScoreboardChart({ chart, height = 220 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const theme = getChartTheme();

  const evalSet = useMemo(
    () => new Set(chart?.eval_dates ?? []),
    [chart?.eval_dates],
  );

  useEffect(() => {
    if (!ref.current || !chart?.nifty_close_series?.length) return;
    const instance = echarts.init(ref.current, dark ? "dark" : undefined);
    const series = chart.nifty_close_series;
    const dates = series.map((p) => p.date);
    const closes = series.map((p) => p.close);

    instance.setOption({
      backgroundColor: "transparent",
      textStyle: { color: theme.textColor, fontSize: 11 },
      grid: { left: 56, right: 12, top: 28, bottom: 40 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const row = (Array.isArray(params) ? params[0] : params) as {
            axisValue?: string;
            value?: number;
          };
          return `${row.axisValue}<br/>Nifty: ${Number(row.value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { color: theme.textColor, fontSize: 9, rotate: 30 },
        axisLine: { lineStyle: { color: theme.gridColor } },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: {
          color: theme.textColor,
          formatter: (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 0 }),
        },
        splitLine: { lineStyle: { color: theme.gridColor } },
      },
      series: [
        {
          name: "Nifty 50 close",
          type: "line",
          data: closes,
          lineStyle: { width: 2, color: theme.infoColor },
          itemStyle: { color: theme.infoColor },
          showSymbol: false,
          markPoint: {
            symbol: "pin",
            symbolSize: 36,
            data: dates
              .map((d, i) => (evalSet.has(d) ? { name: d, coord: [d, closes[i]] } : null))
              .filter(Boolean) as { name: string; coord: [string, number] }[],
            label: { show: false },
            itemStyle: { color: theme.warningColor },
          },
        },
      ],
    });

    const onResize = () => instance.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      instance.dispose();
    };
  }, [chart, dark, theme, evalSet]);

  if (!chart?.nifty_close_series?.length) {
    return null;
  }

  return <div ref={ref} style={{ width: "100%", height }} />;
}
