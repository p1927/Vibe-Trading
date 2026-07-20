import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { ExternalPredictionSnapshot } from "@/lib/api";

const COLORS = ["#3b82f6", "#8b5cf6", "#f59e0b", "#14b8a6", "#ec4899", "#6366f1", "#22c55e", "#ef4444"];

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

interface Props {
  snapshot: ExternalPredictionSnapshot | null;
  height?: number;
}

export function ExternalPredictionsComparisonChart({ snapshot, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const theme = getChartTheme();

  const { labels, series, spot } = useMemo(() => {
    const preds = (snapshot?.predictions ?? []).filter((p) => p.fetch_status === "ok" && p.target?.mid);
    const spotVal = preds.find((p) => p.spot_at_fetch)?.spot_at_fetch ?? snapshot?.internal_forecast?.spot;
    const names = preds.map((p) => {
      const src = snapshot?.sources?.find((s) => s.id === p.source_id);
      return src?.display_name || p.source_id;
    });
    const mids = preds.map((p) => p.target?.mid ?? null);
    const internalReturn = snapshot?.internal_forecast?.expected_return_pct;
    const internalMid =
      typeof spotVal === "number" && typeof internalReturn === "number"
        ? spotVal * (1 + internalReturn / 100)
        : null;
    if (internalMid != null) {
      names.push("Our model");
      mids.push(internalMid);
    }
    return { labels: names, series: mids, spot: typeof spotVal === "number" ? spotVal : null };
  }, [snapshot]);

  useEffect(() => {
    if (!ref.current || !labels.length) return;
    const chart = echarts.init(ref.current, dark ? "dark" : undefined);
    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: "Target comparison (mid)",
        left: 0,
        textStyle: { fontSize: 11, color: theme.textColor, fontWeight: 600 },
      },
      grid: { left: 48, right: 12, top: 36, bottom: 48 },
      tooltip: {
        trigger: "axis",
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
        formatter: (params: unknown) => {
          const row = Array.isArray(params) ? params[0] : params;
          const p = row as { name?: string; value?: number };
          return `${p.name}<br/>Target: ${fmtLevel(p.value)}`;
        },
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { fontSize: 9, color: theme.textColor, rotate: labels.length > 4 ? 25 : 0 },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { fontSize: 10, color: theme.textColor, formatter: (v: number) => fmtLevel(v) },
        splitLine: { lineStyle: { color: theme.gridColor, type: "dashed" } },
      },
      series: [
        ...(spot != null
          ? [
              {
                name: "Spot",
                type: "line" as const,
                markLine: {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed", color: theme.textColor },
                  data: [{ yAxis: spot, label: { formatter: "Spot", fontSize: 9 } }],
                },
              },
            ]
          : []),
        {
          type: "bar",
          data: series.map((v, i) => ({
            value: v,
            itemStyle: {
              color: labels[i] === "Our model" ? (dark ? "#e2e8f0" : "#1e293b") : COLORS[i % COLORS.length],
              borderRadius: [4, 4, 0, 0],
            },
          })),
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [dark, labels, series, spot, theme]);

  if (!labels.length) return null;

  return <div ref={ref} style={{ width: "100%", height }} />;
}
