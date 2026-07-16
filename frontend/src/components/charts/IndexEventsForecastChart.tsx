import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexScenario, IndexUpcomingEvent } from "@/lib/api";

interface Props {
  spot: number;
  horizonDays: number;
  expectedReturnPct: number;
  rangeLow?: number | null;
  rangeHigh?: number | null;
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

  const baselineTarget = spot * (1 + expectedReturnPct / 100);
  const simulatedTarget =
    simulatedReturnPct != null && Number.isFinite(simulatedReturnPct)
      ? spot * (1 + simulatedReturnPct / 100)
      : null;

  const baselinePath = useMemo(
    () => days.map((d) => spot + (baselineTarget - spot) * (d / Math.max(horizonDays, 1))),
    [days, spot, baselineTarget, horizonDays],
  );

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
    const chart = echarts.init(ref.current);

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

    if (rangeLow != null && rangeHigh != null && Number.isFinite(rangeLow) && Number.isFinite(rangeHigh)) {
      series.push({
        name: "Range high",
        type: "line",
        data: days.map((d) => spot + (rangeHigh - spot) * (d / Math.max(horizonDays, 1))),
        smooth: true,
        showSymbol: false,
        lineStyle: { type: "dashed", color: t.upColor, width: 1, opacity: 0.5 },
        itemStyle: { color: t.upColor },
      });
      series.push({
        name: "Range low",
        type: "line",
        data: days.map((d) => spot + (rangeLow - spot) * (d / Math.max(horizonDays, 1))),
        smooth: true,
        showSymbol: false,
        lineStyle: { type: "dashed", color: t.downColor, width: 1, opacity: 0.5 },
        itemStyle: { color: t.downColor },
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
        text: "Forecast path with upcoming events",
        subtext: `Spot today → ${horizonDays}d target · drag factors below to shift the orange line`,
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
        subtextStyle: { fontSize: 9, color: t.textColor, opacity: 0.65 },
      },
      legend: {
        type: "scroll",
        bottom: 0,
        textStyle: { fontSize: 9, color: t.textColor },
      },
      grid: { left: 56, right: 16, top: 52, bottom: 48 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const day = rows[0]?.dataIndex ?? 0;
          const lines = [`Day +${day}`];
          for (const row of rows) {
            const val = row?.value;
            if (typeof val === "number") lines.push(`${row.seriesName}: ${fmtLevel(val)}`);
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
    rangeLow,
    rangeHigh,
    upcomingEvents,
    dark,
  ]);

  if (spot <= 0) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground">
        Run analysis to see forecast timeline.
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div ref={ref} style={{ height }} className="w-full" />
      {upcomingEvents.length > 0 ? (
        <ul className="mt-3 max-h-28 space-y-1 overflow-y-auto border-t pt-2 text-[10px] text-muted-foreground">
          {upcomingEvents.slice(0, 12).map((ev) => (
            <li key={`${ev.date}-${ev.label}`} className="flex gap-2">
              <span className="shrink-0 font-mono text-foreground/80">
                +{ev.days_from_now}d
              </span>
              <span>{ev.label}</span>
              {ev.weight != null ? (
                <span className="ml-auto opacity-60">{(ev.weight * 100).toFixed(1)}% wt</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-[10px] text-muted-foreground">
          No dated constituent events in horizon — expiry and macro flags still shown on chart when present.
        </p>
      )}
    </div>
  );
}
