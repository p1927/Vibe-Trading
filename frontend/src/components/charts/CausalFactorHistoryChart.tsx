import { useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  LineSeries,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexFactorHistoryPoint } from "@/lib/api";
import { factorLabel } from "@/lib/factorEventMapping";
import { factorSeriesValues, niftyCloseSeries, pivotFactorHistoryWide } from "@/lib/factorHistoryUtils";

interface Props {
  activeFactor: string;
  factorHistory?: IndexFactorHistoryPoint[];
  height?: number;
}

export function CausalFactorHistoryChart({
  activeFactor,
  factorHistory = [],
  height = 280,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const { dark } = useDarkMode();

  const wide = useMemo(() => pivotFactorHistoryWide(factorHistory), [factorHistory]);
  const niftySeries = useMemo(() => niftyCloseSeries(wide), [wide]);
  const factorSeries = useMemo(
    () => factorSeriesValues(factorHistory, activeFactor),
    [factorHistory, activeFactor],
  );

  useEffect(() => {
    if (!containerRef.current || niftySeries.length < 2) return;
    const theme = getChartTheme();

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: theme.textColor,
      },
      grid: {
        vertLines: { color: `${theme.textColor}18` },
        horzLines: { color: `${theme.textColor}18` },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: `${theme.textColor}30` },
      leftPriceScale: { visible: factorSeries.length >= 2, borderColor: `${theme.textColor}30` },
      timeScale: { borderColor: `${theme.textColor}30`, timeVisible: true },
    });
    chartRef.current = chart;

    const niftyLine = chart.addSeries(LineSeries, {
      color: theme.infoColor,
      lineWidth: 2,
      title: "Nifty 50",
      priceScaleId: "right",
    });
    niftyLine.setData(
      niftySeries
        .filter((r) => r.close != null)
        .map((r) => ({ time: r.date as Time, value: r.close as number })),
    );

    if (factorSeries.length >= 2) {
      const factorLine = chart.addSeries(LineSeries, {
        color: theme.warningColor,
        lineWidth: 2,
        title: factorLabel(activeFactor),
        priceScaleId: "left",
      });
      factorLine.setData(
        factorSeries
          .filter((r) => r.value != null)
          .map((r) => ({ time: r.date as Time, value: r.value as number })),
      );
    }

    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [activeFactor, niftySeries, factorSeries, height, dark]);

  if (niftySeries.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Load factor history to compare Nifty vs this driver over time.
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div
        ref={containerRef}
        className="rounded-lg border border-border/40 bg-muted/10 overflow-hidden"
        style={{ height }}
      />
      <p className="text-[10px] text-muted-foreground">
        Past co-movement: Nifty close (right) vs {factorLabel(activeFactor)} (left) on the same calendar
        dates.
      </p>
    </div>
  );
}
