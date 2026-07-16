import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type {
  IndexSimulationResult,
  IndexUpcomingEvent,
} from "@/lib/api";
import { factorLabel } from "@/lib/factorEventMapping";

interface SensitivityPoint {
  factor_delta_pct?: number;
  index_level?: number;
  return_pct?: number;
}

interface SensitivityCurve {
  factor?: string;
  label?: string;
  points?: SensitivityPoint[];
}

interface Props {
  spot: number;
  horizonDays: number;
  baselineReturnPct: number;
  simulation: IndexSimulationResult | null;
  activeFactor: string;
  sensitivity?: SensitivityCurve[];
  upcomingEvents?: IndexUpcomingEvent[];
  chartMode?: "horizon" | "shock_sweep";
  height?: number;
}

function fmtLevel(v: number): string {
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function FactorImpactInteractiveChart({
  spot,
  horizonDays,
  baselineReturnPct,
  simulation,
  activeFactor,
  sensitivity = [],
  upcomingEvents = [],
  chartMode = "horizon",
  height = 280,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const forecastPath = simulation?.forecast_path ?? [];
  const activeCurve = sensitivity.find((c) => c.factor === activeFactor);

  const contribDeltas = useMemo(() => {
    const base = simulation?.baseline_factor_explanation?.contributors ?? [];
    const scen = simulation?.factor_explanation?.contributors ?? [];
    const scenMap = new Map(scen.map((r) => [r.factor, r.contribution_pct ?? 0]));
    return base
      .map((r) => {
        const factor = r.factor || "";
        const before = r.contribution_pct ?? 0;
        const after = scenMap.get(factor) ?? before;
        return { factor, label: r.label || factorLabel(factor), delta: after - before, after };
      })
      .filter((r) => r.factor && Math.abs(r.delta) > 0.001)
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
      .slice(0, 6);
  }, [simulation]);

  const option = useMemo(() => {
    const t = getChartTheme();

    if (chartMode === "shock_sweep" && activeCurve?.points?.length) {
      const pts = activeCurve.points;
      const xs = pts.map((p) => `${p.factor_delta_pct ?? 0}%`);
      const ys = pts.map((p) => Number(p.index_level ?? 0));
      const baselineLevel =
        pts.find((p) => p.factor_delta_pct === 0)?.index_level ??
        spot * (1 + baselineReturnPct / 100);
      return {
        backgroundColor: "transparent",
        title: {
          text: `${activeCurve.label || factorLabel(activeFactor)} shock sweep → Nifty at horizon`,
          left: 0,
          textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
        },
        grid: { left: 56, right: 16, top: 40, bottom: 36 },
        tooltip: { trigger: "axis" as const },
        xAxis: {
          type: "category" as const,
          data: xs,
          name: "Factor shock",
          axisLabel: { fontSize: 9, color: t.textColor },
        },
        yAxis: {
          type: "value" as const,
          scale: true,
          axisLabel: { fontSize: 9, color: t.textColor, formatter: (v: number) => fmtLevel(v) },
        },
        series: [
          {
            name: "Nifty at horizon",
            type: "line" as const,
            smooth: true,
            data: ys,
            lineStyle: { color: t.infoColor, width: 2 },
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { type: "dashed", color: t.textColor },
              data: [{ yAxis: baselineLevel, name: "Baseline" }],
            },
          },
        ],
      };
    }

    const days =
      forecastPath.length > 0
        ? forecastPath.map((p) => String(p.day ?? 0))
        : Array.from({ length: horizonDays + 1 }, (_, i) => String(i));

    const baselineLevels =
      forecastPath.length > 0
        ? forecastPath.map((p) => p.baseline_level ?? null)
        : days.map((_, i) => {
            const tFrac = Number(days[i]) / Math.max(horizonDays, 1);
            return spot * (1 + (baselineReturnPct / 100) * tFrac);
          });

    const scenarioLevels =
      forecastPath.length > 0
        ? forecastPath.map((p) => p.scenario_level ?? null)
        : simulation
          ? days.map((_, i) => {
              const tFrac = Number(days[i]) / Math.max(horizonDays, 1);
              const ret = simulation.expected_return_pct ?? baselineReturnPct;
              return spot * (1 + (ret / 100) * tFrac);
            })
          : null;

    const cascadeFactors = (simulation?.cascade_applied ?? [])
      .filter((r) => r.factor && r.before != null && r.after != null && r.before !== 0)
      .slice(0, 5);

    const factorTraceSeries =
      chartMode === "horizon" && cascadeFactors.length > 0
        ? cascadeFactors.map((row, idx) => {
            const before = Number(row.before);
            const after = Number(row.after);
            const pctEnd = ((after / before) - 1) * 100;
            const colors = ["#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#ec4899"];
            return {
              name: factorLabel(row.factor || ""),
              type: "line" as const,
              yAxisIndex: 1,
              smooth: true,
              showSymbol: false,
              data: days.map((_, i) => {
                const tFrac = Number(days[i]) / Math.max(horizonDays, 1);
                return pctEnd * tFrac;
              }),
              lineStyle: { width: 1.2, color: colors[idx % colors.length] },
              itemStyle: { color: colors[idx % colors.length] },
            };
          })
        : [];

    const eventMarks = (upcomingEvents ?? [])
      .filter((e) => e.days_from_now != null && e.days_from_now >= 0 && e.days_from_now <= horizonDays)
      .map((e) => ({
        name: e.label || e.event_type,
        coord: [String(e.days_from_now), baselineLevels[e.days_from_now ?? 0]],
      }));

    const series: Array<Record<string, unknown> & { name: string }> = [
      {
        name: "Baseline Nifty",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: baselineLevels,
        lineStyle: { color: t.textColor, width: 2, opacity: 0.7 },
      },
    ];

    if (scenarioLevels) {
      series.push({
        name: "Your scenario",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: scenarioLevels,
        lineStyle: { color: t.upColor, width: 2.5, type: "dashed" },
      });
    }

    series.push(...factorTraceSeries);

    const legendItems = [
      ...(scenarioLevels ? ["Baseline Nifty", "Your scenario"] : ["Baseline Nifty"]),
      ...factorTraceSeries.map((s) => s.name),
    ];

    return {
      backgroundColor: "transparent",
      title: {
        text: "Nifty 50 path to horizon (baseline vs your factor shock)",
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
      },
      legend: {
        data: legendItems,
        top: 4,
        right: 0,
        textStyle: { fontSize: 9, color: t.textColor },
      },
      grid: { left: 56, right: factorTraceSeries.length ? 48 : 16, top: 44, bottom: 36 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const day = (items[0] as { axisValue?: string })?.axisValue ?? "";
          const lines = items.map((p) => {
            const pt = p as { seriesName?: string; value?: number; marker?: string; seriesIndex?: number };
            if (pt.value == null) return "";
            const isFactorTrace = pt.seriesName && pt.seriesName !== "Baseline Nifty" && pt.seriesName !== "Your scenario";
            const formatted = isFactorTrace
              ? `${Number(pt.value).toFixed(2)}%`
              : fmtLevel(Number(pt.value));
            return `${pt.marker ?? ""} ${pt.seriesName}: ${formatted}`;
          });
          return [`Day ${day}`, ...lines.filter(Boolean)].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: days.map((d) => `D${d}`),
        name: "Days ahead",
        axisLabel: { fontSize: 9, color: t.textColor },
      },
      yAxis: [
        {
          type: "value",
          scale: true,
          axisLabel: { fontSize: 9, color: t.textColor, formatter: (v: number) => fmtLevel(v) },
        },
        ...(factorTraceSeries.length
          ? [
              {
                type: "value" as const,
                scale: true,
                position: "right" as const,
                name: "Factor Δ%",
                nameTextStyle: { fontSize: 8, color: t.textColor },
                axisLabel: {
                  fontSize: 8,
                  color: t.textColor,
                  formatter: (v: number) => `${v.toFixed(1)}%`,
                },
                splitLine: { show: false },
              },
            ]
          : []),
      ],
      series: series.map((s) => ({
        ...s,
        yAxisIndex: (s as { yAxisIndex?: number }).yAxisIndex ?? 0,
        markPoint:
          eventMarks.length && s.name === "Baseline Nifty"
            ? {
                symbol: "pin",
                symbolSize: 28,
                data: eventMarks,
                label: { fontSize: 8, formatter: (p: { name?: string }) => (p.name ?? "").slice(0, 12) },
              }
            : undefined,
      })),
    };
  }, [
    chartMode,
    activeCurve,
    activeFactor,
    forecastPath,
    horizonDays,
    spot,
    baselineReturnPct,
    simulation,
    upcomingEvents,
    dark,
  ]);

  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [option, dark]);

  return (
    <div className="space-y-2">
      <div ref={ref} style={{ height }} className="rounded-lg border bg-muted/10 p-1" />
      {contribDeltas.length > 0 ? (
        <div className="rounded-lg border bg-muted/20 px-3 py-2">
          <p className="text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
            Contribution shift vs baseline
          </p>
          <div className="mt-1 flex flex-wrap gap-2">
            {contribDeltas.map((row) => (
              <span
                key={row.factor}
                className="rounded-full bg-background px-2 py-0.5 text-[10px] tabular-nums"
              >
                {row.label}: {row.delta >= 0 ? "+" : ""}
                {row.delta.toFixed(2)}%
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
