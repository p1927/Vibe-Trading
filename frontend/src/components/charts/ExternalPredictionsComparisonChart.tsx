import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { ExternalPredictionSnapshot } from "@/lib/api";
import {
  effectiveChartHorizonDays,
  filterVisiblePredictions,
  hasHorizonMismatch,
} from "@/lib/externalPredictionsUtils";

const COLORS = ["#3b82f6", "#8b5cf6", "#f59e0b", "#14b8a6", "#ec4899", "#6366f1", "#22c55e", "#ef4444"];

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtDate(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

interface ChartItem {
  name: string;
  mid: number | null;
  targetDate?: string;
  chartHorizonDays: number;
  mismatch: boolean;
  isInternal?: boolean;
}

interface Props {
  snapshot: ExternalPredictionSnapshot | null;
  horizonDays: number;
  height?: number;
}

export function ExternalPredictionsComparisonChart({ snapshot, horizonDays, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const theme = getChartTheme();

  const { items, spot } = useMemo(() => {
    const preds = filterVisiblePredictions(snapshot?.predictions);
    const spotVal = preds.find((p) => p.spot_at_fetch)?.spot_at_fetch ?? snapshot?.internal_forecast?.spot;
    const rows: ChartItem[] = preds.map((p) => {
      const src = snapshot?.sources?.find((s) => s.id === p.source_id);
      return {
        name: src?.display_name || p.source_id,
        mid: p.target?.mid ?? null,
        targetDate: p.target_date,
        chartHorizonDays: effectiveChartHorizonDays(p, horizonDays),
        mismatch: hasHorizonMismatch(p),
      };
    });
    const internalReturn = snapshot?.internal_forecast?.expected_return_pct;
    const internalMid =
      typeof spotVal === "number" && typeof internalReturn === "number"
        ? spotVal * (1 + internalReturn / 100)
        : null;
    if (internalMid != null) {
      rows.push({
        name: "Our model",
        mid: internalMid,
        chartHorizonDays: horizonDays,
        mismatch: false,
        isInternal: true,
      });
    }
    return { items: rows, spot: typeof spotVal === "number" ? spotVal : null };
  }, [horizonDays, snapshot]);

  const labels = items.map((item) => item.name);
  const series = items.map((item) => item.mid);

  useEffect(() => {
    if (!ref.current || !labels.length) return;
    const chart = echarts.init(ref.current, dark ? "dark" : undefined);
    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: "Target comparison (mid)",
        subtext:
          items.some((item) => item.mismatch)
            ? `Selected ${horizonDays}d tab · mismatched sources use article target-date horizon`
            : undefined,
        left: 0,
        textStyle: { fontSize: 11, color: theme.textColor, fontWeight: 600 },
        subtextStyle: { fontSize: 9, color: theme.textColor, opacity: 0.75 },
      },
      grid: { left: 48, right: 12, top: items.some((item) => item.mismatch) ? 52 : 36, bottom: 48 },
      tooltip: {
        trigger: "axis",
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
        formatter: (params: unknown) => {
          const row = Array.isArray(params) ? params[0] : params;
          const p = row as { dataIndex?: number; name?: string; value?: number };
          const idx = p.dataIndex ?? 0;
          const item = items[idx];
          if (!item) return `${p.name}<br/>Target: ${fmtLevel(p.value)}`;
          const lines = [
            item.name,
            `Target: ${fmtLevel(item.mid)}`,
            `Target date: ${fmtDate(item.targetDate)}`,
            `Chart horizon: ${item.chartHorizonDays}d`,
          ];
          if (item.mismatch) {
            lines.push("Horizon mismatch (uses article target date)");
          }
          return lines.join("<br/>");
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
          data: series.map((v, i) => {
            const item = items[i];
            const baseColor = item?.isInternal
              ? dark
                ? "#e2e8f0"
                : "#1e293b"
              : COLORS[i % COLORS.length];
            return {
              value: v,
              itemStyle: {
                color: baseColor,
                borderRadius: [4, 4, 0, 0],
                borderColor: item?.mismatch ? "#f59e0b" : undefined,
                borderWidth: item?.mismatch ? 2 : 0,
              },
            };
          }),
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [dark, items, labels, series, spot, theme, horizonDays]);

  if (!labels.length) return null;

  return <div ref={ref} style={{ width: "100%", height }} />;
}
