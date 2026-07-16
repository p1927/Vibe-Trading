import { useEffect, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";

export interface PayoffPoint {
  spot: number;
  pnl: number;
  net_pnl?: number;
}

interface Props {
  samples: PayoffPoint[];
  spot?: number | null;
  height?: number;
}

export function MiniPayoffChart({ samples, spot, height = 120 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  useEffect(() => {
    if (!ref.current || samples.length < 2) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);
    const spots = samples.map((s) => Number(s.spot));
    const pnls = samples.map((s) => Number(s.net_pnl ?? s.pnl ?? 0));
    const lastPnl = pnls[pnls.length - 1] ?? 0;
    const color = lastPnl >= 0 ? t.upColor : t.downColor;

    const markLine = spot != null && Number.isFinite(Number(spot))
      ? { silent: true, symbol: "none", lineStyle: { color: "#888", type: "dashed", width: 1 }, data: [{ xAxis: Number(spot) }] }
      : undefined;

    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 4, right: 4, top: 8, bottom: 4 },
      xAxis: { type: "category", data: spots, show: false },
      yAxis: { type: "value", show: false, scale: true },
      series: [{
        type: "line",
        data: pnls,
        symbol: "none",
        smooth: true,
        lineStyle: { color, width: 2 },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: color + "35" },
              { offset: 1, color: color + "05" },
            ],
          },
        },
        markLine,
      }],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [samples, spot, dark]);

  if (samples.length < 2) return null;
  return <div ref={ref} style={{ height }} className="rounded-lg overflow-hidden border border-border/40 bg-muted/10" />;
}
