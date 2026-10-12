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

function fmtLevel(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function IndexForecastLevelChart({ rows, horizonDays = 14, height = 240 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const sorted = rows; // issueTimeline() output: ascending by issue day

  useEffect(() => {
    if (!ref.current || sorted.length < 2) return;
    const t = getChartTheme();
    const chart = initEChart(ref.current);
    const labels = sorted.map((r) => fmtDate(r.issueDay));
    const implied = sorted.map((r) => r.target);
    const bandBase = sorted.map((r) => r.low);
    const bandWidth = sorted.map((r) => (r.low != null && r.high != null ? Math.max(0, r.high - r.low) : null));
    const spot = sorted.map((r) => r.spot);

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: `Nifty ${horizonDays}d forecast levels`,
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
      },
      legend: {
        data: [`${horizonDays}d target`, "Spot", "Confidence band"],
        top: 4,
        right: 0,
        textStyle: { fontSize: 9, color: t.textColor },
      },
      grid: { left: 56, right: 12, top: 48, bottom: 32 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { fontSize: 10, color: t.textColor },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { fontSize: 10, color: t.textColor, formatter: (v: number) => fmtLevel(v) },
      },
      series: [
        {
          name: "_band_base",
          type: "line",
          data: bandBase,
          lineStyle: { opacity: 0 },
          stack: "confidence",
          symbol: "none",
          silent: true,
        },
        {
          name: "Confidence band",
          type: "line",
          data: bandWidth,
          lineStyle: { opacity: 0 },
          stack: "confidence",
          symbol: "none",
          areaStyle: { color: t.infoColor, opacity: 0.15 },
          silent: true,
        },
        {
          name: "Spot",
          type: "line",
          data: spot,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { color: t.textColor, width: 1.5, type: "dashed" },
        },
        {
          name: `${horizonDays}d target`,
          type: "line",
          data: implied,
          smooth: sorted.length > 2,
          symbol: "circle",
          symbolSize: 7,
          lineStyle: { color: t.infoColor, width: 2 },
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

  if (!sorted.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Run analysis to record forecast levels.
      </div>
    );
  }

  if (sorted.length === 1) {
    const last = sorted[0];
    return (
      <div
        className="flex flex-col justify-center rounded-xl border bg-card px-4 py-3"
        style={{ height }}
      >
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          {horizonDays}d forecast level
        </p>
        <p className="mt-2 text-2xl font-semibold tabular-nums">{fmtLevel(last.target)}</p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Spot {fmtLevel(last.spot)} · band {fmtLevel(last.low ?? undefined)} –{" "}
          {fmtLevel(last.high ?? undefined)}
        </p>
        <p className="mt-2 text-[10px] text-muted-foreground">
          One recorded forecast — run again on another day to see the level track chart.
        </p>
      </div>
    );
  }

  return <div ref={ref} style={{ height }} className="rounded-xl border bg-card p-2" />;
}
