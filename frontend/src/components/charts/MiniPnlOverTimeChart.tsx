import { useEffect, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";

export interface PnlOverTimePoint {
  days_to_expiry?: number;
  pnl: number;
  net_pnl?: number;
}

interface Props {
  samples: PnlOverTimePoint[];
  height?: number;
}

export function MiniPnlOverTimeChart({ samples, height = 100 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  useEffect(() => {
    if (!ref.current || samples.length < 2) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    const days = samples.map((s) => Number(s.days_to_expiry ?? 0));
    const pnls = samples.map((s) => Number(s.net_pnl ?? s.pnl ?? 0));
    const lastPnl = pnls[pnls.length - 1] ?? 0;
    const color = lastPnl >= 0 ? t.upColor : t.downColor;

    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 4, right: 4, top: 8, bottom: 4 },
      xAxis: { type: "category", data: days, show: false },
      yAxis: { type: "value", show: false, scale: true },
      series: [{
        type: "line",
        data: pnls,
        symbol: "circle",
        symbolSize: 4,
        smooth: true,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
      }],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [samples, dark]);

  if (samples.length < 2) return null;
  return <div ref={ref} style={{ height }} className="rounded-lg overflow-hidden border border-border/40 bg-muted/10" />;
}
