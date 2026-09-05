import { useEffect, useMemo, useRef } from "react";
import { initEChart } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { HubIndexHistoryBar } from "@/lib/api";

interface Props {
  day: string | null;
  bars: HubIndexHistoryBar[];
  currentSpot?: number | null;
  height?: number;
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
}

/** The prior trading day's real recorded intraday price path — the "prior-day actual graph"
 * half of the Command Center dashboard's range panel ask. Rendered as its own compact strip
 * rather than mixed onto IndexEventsForecastChart's forward-looking Day 0..+N axis: the two
 * series are on genuinely different time scales (yesterday's intraday minutes vs. today's
 * forward calendar days), and forcing them onto one x-axis would misrepresent both. */
export function PriorDayPriceStrip({ day, bars, currentSpot, height = 90 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const closes = useMemo(() => bars.map((b) => b.close), [bars]);
  const first = closes.length ? closes[0] : null;
  const last = closes.length ? closes[closes.length - 1] : null;
  const dayHigh = bars.length ? Math.max(...bars.map((b) => b.high)) : null;
  const dayLow = bars.length ? Math.min(...bars.map((b) => b.low)) : null;
  const changePct = first != null && last != null && first !== 0 ? ((last - first) / first) * 100 : null;

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const t = getChartTheme();
    const chart = initEChart(ref.current);
    const up = last != null && first != null && last >= first;
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 2, right: 2, top: 4, bottom: 2 },
      xAxis: { type: "category", show: false, data: bars.map((b) => b.ts_ist) },
      yAxis: { type: "value", show: false, scale: true },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const idx = rows[0]?.dataIndex ?? 0;
          const bar = bars[idx];
          return bar ? `${fmtTime(bar.ts_ist)} · ${fmtLevel(bar.close)}` : "";
        },
      },
      series: [
        {
          type: "line",
          data: closes,
          showSymbol: false,
          smooth: true,
          lineStyle: { color: up ? t.upColor : t.downColor, width: 1.5 },
          areaStyle: { opacity: 0.1, color: t.infoColor },
        },
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [bars, closes, first, last, dark]);

  if (!day || bars.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-[11px] text-muted-foreground">
        No recorded prior-day bars available.
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border/50 bg-muted/10 p-2">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-x-2 text-[11px] text-muted-foreground">
        <span>Yesterday ({day}) actual</span>
        <span
          className={
            changePct != null && changePct >= 0
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-red-600 dark:text-red-400"
          }
        >
          {fmtLevel(first)} → {fmtLevel(last)}{" "}
          {changePct != null ? `(${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%)` : ""}
        </span>
      </div>
      <div ref={ref} style={{ height }} className="w-full" />
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span>Low {fmtLevel(dayLow)}</span>
        <span>High {fmtLevel(dayHigh)}</span>
        {currentSpot != null ? <span>Today's spot {fmtLevel(currentSpot)}</span> : null}
      </div>
    </div>
  );
}
