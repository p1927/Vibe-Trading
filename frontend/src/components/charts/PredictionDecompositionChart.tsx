import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexPredictionHistoryRow } from "@/lib/api";

interface Props {
  rows: IndexPredictionHistoryRow[];
  height?: number;
}

export function PredictionDecompositionChart({ rows, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const sorted = useMemo(
    () =>
      [...rows]
        .filter((r) => r.bottom_up_return_pct != null || r.macro_delta_pct != null)
        .sort((a, b) => a.predicted_at.localeCompare(b.predicted_at)),
    [rows],
  );

  const labels = useMemo(
    () =>
      sorted.map((r) => {
        const d = new Date(r.predicted_at);
        return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
      }),
    [sorted],
  );

  useEffect(() => {
    if (!ref.current || !sorted.length) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    const bottom = sorted.map((r) => Number(r.bottom_up_return_pct ?? 0));
    const macro = sorted.map((r) => Number(r.macro_delta_pct ?? 0));

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: "Return decomposition by forecast date",
        subtext: "Bottom-up constituents vs macro model (percent)",
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
        subtextStyle: { fontSize: 9, color: t.textColor, opacity: 0.7 },
      },
      legend: {
        data: ["Bottom-up", "Macro"],
        top: 0,
        right: 0,
        textStyle: { fontSize: 9, color: t.textColor },
      },
      grid: { left: 44, right: 12, top: 32, bottom: 28 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { fontSize: 9, color: t.textColor, rotate: labels.length > 5 ? 30 : 0 },
      },
      yAxis: {
        type: "value",
        name: "Return %",
        nameTextStyle: { fontSize: 9, color: t.textColor },
        axisLabel: { fontSize: 10, color: t.textColor, formatter: "{value}%" },
      },
      series: [
        {
          name: "Bottom-up",
          type: "bar",
          stack: "total",
          data: bottom,
          itemStyle: { color: t.upColor, opacity: 0.85 },
        },
        {
          name: "Macro",
          type: "bar",
          stack: "total",
          data: macro,
          itemStyle: { color: t.infoColor, opacity: 0.85 },
        },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [sorted, labels, dark]);

  if (!sorted.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Decomposition appears after enriched ledger entries.
      </div>
    );
  }

  return <div ref={ref} style={{ height }} className="rounded-xl border bg-card p-2" />;
}
