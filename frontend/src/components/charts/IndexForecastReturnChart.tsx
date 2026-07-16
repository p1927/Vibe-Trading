import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexPredictionHistoryRow } from "@/lib/api";

interface Props {
  rows: IndexPredictionHistoryRow[];
  height?: number;
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function IndexForecastReturnChart({ rows, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const sorted = useMemo(
    () => [...rows].sort((a, b) => a.predicted_at.localeCompare(b.predicted_at)),
    [rows],
  );

  useEffect(() => {
    if (!ref.current || !sorted.length) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    const labels = sorted.map((r) => fmtDate(r.predicted_at));
    const returns = sorted.map((r) => r.expected_return_pct);
    const actuals = sorted.map((r) =>
      r.actual_return_pct != null && Number.isFinite(r.actual_return_pct) ? r.actual_return_pct : null,
    );

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: "Expected return (14d horizon)",
        subtext: "Percent move from spot at forecast date",
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
        subtextStyle: { fontSize: 9, color: t.textColor, opacity: 0.7 },
      },
      legend: {
        data: ["Forecast %", "Actual % (matured)"],
        top: 4,
        right: 0,
        textStyle: { fontSize: 9, color: t.textColor },
      },
      grid: { left: 44, right: 12, top: 48, bottom: 32 },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v: number) => `${Number(v).toFixed(2)}%`,
      },
      xAxis: {
        type: "category",
        data: labels,
        name: "Forecast date",
        nameLocation: "middle",
        nameGap: 24,
        axisLabel: { fontSize: 10, color: t.textColor },
      },
      yAxis: {
        type: "value",
        name: "Return %",
        nameTextStyle: { fontSize: 9, color: t.textColor },
        axisLabel: { fontSize: 10, color: t.textColor, formatter: "{value}%" },
      },
      series: [
        {
          name: "Forecast %",
          type: "bar",
          data: returns,
          itemStyle: { color: t.upColor, opacity: 0.85 },
        },
        {
          name: "Actual % (matured)",
          type: "scatter",
          data: actuals,
          symbolSize: 10,
          itemStyle: { color: t.downColor },
        },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [sorted, dark]);

  if (!rows.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Return history appears after forecasts are saved.
      </div>
    );
  }

  return <div ref={ref} style={{ height }} className="rounded-xl border bg-card p-2" />;
}
