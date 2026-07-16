import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexFactorHistoryPoint } from "@/lib/api";
import { pivotFactorHistoryWide } from "@/lib/factorHistoryUtils";

interface Props {
  series: IndexFactorHistoryPoint[];
  forecastTarget?: number | null;
  height?: number;
}

function fmtLevel(v: number): string {
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function NiftyMarketContextChart({ series = [], forecastTarget, height = 220 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const wide = useMemo(() => pivotFactorHistoryWide(series), [series]);

  const closes = useMemo(
    () =>
      wide
        .filter((r) => typeof r.nifty_close === "number" && Number.isFinite(r.nifty_close))
        .map((r) => ({ date: String(r.date), close: r.nifty_close as number })),
    [wide],
  );

  useEffect(() => {
    if (!ref.current || !closes.length) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    const labels = closes.map((r) => r.date.slice(5));
    const values = closes.map((r) => r.close);

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: "Nifty 50 — actual closes (calibration context)",
        subtext: "Historical index levels used to fit the macro model",
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
        subtextStyle: { fontSize: 9, color: t.textColor, opacity: 0.7 },
      },
      grid: { left: 56, right: 12, top: 48, bottom: 32 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const idx = (Array.isArray(params) ? params[0] : params)?.dataIndex ?? 0;
          const row = closes[idx];
          if (!row) return "";
          return `${row.date}<br/>Close ${fmtLevel(row.close)}`;
        },
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { fontSize: 9, color: t.textColor, rotate: labels.length > 14 ? 35 : 0 },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: "Nifty close",
        nameTextStyle: { fontSize: 9, color: t.textColor },
        axisLabel: { fontSize: 10, color: t.textColor, formatter: (v: number) => fmtLevel(v) },
      },
      series: [
        {
          name: "Nifty close",
          type: "line",
          data: values,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: t.infoColor, width: 2 },
          itemStyle: { color: t.infoColor },
          markLine:
            forecastTarget != null && Number.isFinite(forecastTarget)
              ? {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed", color: t.upColor },
                  label: { formatter: "Current 14d target", fontSize: 9 },
                  data: [{ yAxis: forecastTarget }],
                }
              : undefined,
        },
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [closes, forecastTarget, dark]);

  if (!closes.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Market history loading…
      </div>
    );
  }

  return <div ref={ref} style={{ height }} className="rounded-xl border bg-card p-2" />;
}
