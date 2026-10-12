import { useEffect, useRef } from "react";
import { initEChart } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IssueTimelinePoint } from "@/lib/forecastReplay/timeline";

interface Props {
  rows: IssueTimelinePoint[];
  horizonDays?: number;
  height?: number;
}

function fmtDate(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
}

export function IndexForecastReturnChart({ rows, horizonDays = 14, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const sorted = rows; // issueTimeline() output: ascending by issue day

  useEffect(() => {
    if (!ref.current || !sorted.length) return;
    const t = getChartTheme();
    const chart = initEChart(ref.current);
    const labels = sorted.map((r) => fmtDate(r.issueDay));
    const returns = sorted.map((r) => r.expectedReturnPct);
    const actuals = sorted.map((r) => r.actualReturnPct);

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: `Expected return (${horizonDays}d horizon)`,
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
  }, [sorted, horizonDays, dark]);

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
