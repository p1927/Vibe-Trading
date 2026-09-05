import { useEffect, useMemo, useRef } from "react";
import { initEChart } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexScenario, IndexUpcomingEvent } from "@/lib/api";

interface DailyBandPoint {
  days_ahead: number;
  p10: number;
  p50: number;
  p90: number;
}

interface Props {
  spot: number;
  horizonDays: number;
  expectedReturnPct: number;
  rangeLow?: number | null;
  rangeHigh?: number | null;
  /** Real day-by-day p10/p50/p90 level band (GBM-calibrated from `rangeLow`/`rangeHigh`'s
   * own terminal endpoints — see `compute_daily_range_band` in predictor.py), one row per
   * trading day. When present, this drives both the shaded band AND the median line —
   * a real geometric-drift path, not the linear interpolation used as a fallback when
   * absent (an older cached artifact predating this field). */
  dailyBand?: DailyBandPoint[];
  upcomingEvents?: IndexUpcomingEvent[];
  scenarios?: IndexScenario[];
  simulatedReturnPct?: number | null;
  height?: number;
}

function fmtLevel(v: number): string {
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

const EVENT_COLORS: Record<string, string> = {
  results: "#f59e0b",
  earnings: "#f59e0b",
  monthly_expiry: "#8b5cf6",
  rbi_policy: "#ef4444",
  union_budget: "#3b82f6",
  results_season: "#10b981",
  corporate: "#64748b",
};

export function IndexEventsForecastChart({
  spot,
  horizonDays,
  expectedReturnPct,
  rangeLow,
  rangeHigh,
  dailyBand,
  upcomingEvents = [],
  scenarios = [],
  simulatedReturnPct,
  height = 300,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const days = useMemo(
    () => Array.from({ length: Math.max(horizonDays, 1) + 1 }, (_, i) => i),
    [horizonDays],
  );

  // Real per-day band only counts as usable when it actually covers every day this chart
  // renders (0..horizonDays) — a shorter/gappy band (e.g. a stale cached artifact from a
  // smaller horizon) falls back to the linear approximation rather than silently truncating.
  const bandByDay = useMemo(() => {
    if (!dailyBand || dailyBand.length === 0) return null;
    const map = new Map(dailyBand.map((row) => [row.days_ahead, row]));
    return days.every((d) => map.has(d)) ? map : null;
  }, [dailyBand, days]);

  const baselineTarget = spot * (1 + expectedReturnPct / 100);
  const simulatedTarget =
    simulatedReturnPct != null && Number.isFinite(simulatedReturnPct)
      ? spot * (1 + simulatedReturnPct / 100)
      : null;

  const baselinePath = useMemo(
    () =>
      bandByDay
        ? days.map((d) => bandByDay.get(d)!.p50)
        : days.map((d) => spot + (baselineTarget - spot) * (d / Math.max(horizonDays, 1))),
    [bandByDay, days, spot, baselineTarget, horizonDays],
  );

  const hasRange =
    rangeLow != null && rangeHigh != null && Number.isFinite(rangeLow) && Number.isFinite(rangeHigh);
  const rangeLowPath = useMemo(() => {
    if (bandByDay) return days.map((d) => bandByDay.get(d)!.p10);
    return hasRange
      ? days.map((d) => spot + ((rangeLow as number) - spot) * (d / Math.max(horizonDays, 1)))
      : null;
  }, [bandByDay, hasRange, days, spot, rangeLow, horizonDays]);
  const rangeHighPath = useMemo(() => {
    if (bandByDay) return days.map((d) => bandByDay.get(d)!.p90);
    return hasRange
      ? days.map((d) => spot + ((rangeHigh as number) - spot) * (d / Math.max(horizonDays, 1)))
      : null;
  }, [bandByDay, hasRange, days, spot, rangeHigh, horizonDays]);

  const simulatedPath = useMemo(() => {
    if (simulatedTarget == null) return null;
    return days.map((d) => spot + (simulatedTarget - spot) * (d / Math.max(horizonDays, 1)));
  }, [days, spot, simulatedTarget, horizonDays]);

  const scenarioPaths = useMemo(() => {
    if (!scenarios.length || spot <= 0) return [];
    return scenarios.slice(0, 4).map((s) => {
      const rng = s.index_range;
      if (!Array.isArray(rng) || rng.length < 2) return null;
      const mid = (Number(rng[0]) + Number(rng[1])) / 2;
      const ret = ((mid / spot) - 1) * 100;
      const target = spot * (1 + ret / 100);
      return {
        label: [s.event, s.outcome].filter(Boolean).join(" · "),
        prob: s.probability,
        path: days.map((d) => spot + (target - spot) * (d / Math.max(horizonDays, 1))),
      };
    }).filter(Boolean) as Array<{ label: string; prob?: number; path: number[] }>;
  }, [scenarios, spot, days, horizonDays]);

  useEffect(() => {
    if (!ref.current || spot <= 0) return;
    const t = getChartTheme();
    const chart = initEChart(ref.current);

    const eventMarks = upcomingEvents.flatMap((e) => {
      const day = e.days_from_now;
      if (day == null || day < 0 || day > horizonDays) return [];
      return [
        {
          name: e.label || e.event_type || "Event",
          coord: [day, baselinePath[day] ?? spot] as [number, number],
          itemStyle: { color: EVENT_COLORS[e.event_type || ""] || t.warningColor || "#f59e0b" },
        },
      ];
    });

    const series: Record<string, unknown>[] = [
      {
        name: "Baseline forecast",
        type: "line",
        data: baselinePath,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: t.infoColor, width: 2.5 },
        itemStyle: { color: t.infoColor },
        markPoint: eventMarks.length
          ? {
              symbol: "pin",
              symbolSize: 36,
              data: eventMarks,
              label: { show: false },
            }
          : undefined,
      },
    ];

    if (rangeLowPath && rangeHighPath) {
      // Stacked-area trick for a sloped shaded band: an invisible baseline series
      // at the low path, then a visible area series holding the (high - low) gap
      // stacked on top of it — the area's visible top edge is exactly the high
      // path, its bottom edge exactly the low path.
      series.push({
        id: "range-band-base",
        name: "Range low (band base)",
        type: "line",
        data: rangeLowPath,
        stack: "range-band",
        smooth: true,
        showSymbol: false,
        lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0 },
      });
      series.push({
        id: "range-band-fill",
        name: bandByDay ? "p10–p90 forecast band" : "Projected range",
        type: "line",
        data: rangeHighPath.map((h, i) => h - rangeLowPath[i]),
        stack: "range-band",
        smooth: true,
        showSymbol: false,
        lineStyle: { opacity: 0 },
        areaStyle: { opacity: 0.18, color: t.infoColor },
      });
    }

    for (const sc of scenarioPaths) {
      series.push({
        name: sc.label,
        type: "line",
        data: sc.path,
        smooth: true,
        showSymbol: false,
        lineStyle: { type: "dotted", width: 1, opacity: 0.45 },
      });
    }

    if (simulatedPath) {
      series.push({
        name: "Your factor scenario",
        type: "line",
        data: simulatedPath,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: t.upColor, width: 2.5 },
        itemStyle: { color: t.upColor },
      });
    }

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: `Spot → ${horizonDays}d`,
        left: 0,
        textStyle: { fontSize: 10, color: t.textColor, fontWeight: 600 },
      },
      legend: {
        type: "scroll",
        bottom: 0,
        textStyle: { fontSize: 9, color: t.textColor },
        data: series
          .map((s) => s.name as string)
          .filter((n) => n !== "Range low (band base)"),
      },
      grid: { left: 56, right: 16, top: 52, bottom: 48 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const day = rows[0]?.dataIndex ?? 0;
          const lines = [`Day +${day}`];
          for (const row of rows) {
            if (row?.seriesId === "range-band-base" || row?.seriesId === "range-band-fill") continue;
            const val = row?.value;
            if (typeof val === "number") lines.push(`${row.seriesName}: ${fmtLevel(val)}`);
          }
          if (rangeLowPath && rangeHighPath) {
            const label = bandByDay ? "p10–p90" : "Projected range";
            lines.push(`${label}: ${fmtLevel(rangeLowPath[day])} – ${fmtLevel(rangeHighPath[day])}`);
          }
          const ev = upcomingEvents.find((e) => e.days_from_now === day);
          if (ev?.label) lines.push(`📅 ${ev.label}`);
          return lines.join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: days.map((d) => (d === 0 ? "Today" : `+${d}d`)),
        name: "Horizon",
        nameLocation: "middle",
        nameGap: 28,
        axisLabel: { fontSize: 9, color: t.textColor },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: "NIFTY level",
        nameTextStyle: { fontSize: 9, color: t.textColor },
        axisLabel: { fontSize: 10, color: t.textColor, formatter: (v: number) => fmtLevel(v) },
      },
      series,
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [
    spot,
    horizonDays,
    baselinePath,
    simulatedPath,
    scenarioPaths,
    rangeLowPath,
    rangeHighPath,
    bandByDay,
    upcomingEvents,
    dark,
  ]);

  if (spot <= 0) {
    return (
      <div className="rounded-xl border bg-card p-2 text-[12px] text-muted-foreground">
        Run analysis to see forecast timeline.
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card p-2 shadow-sm">
      <div ref={ref} style={{ height }} className="w-full" />
    </div>
  );
}
