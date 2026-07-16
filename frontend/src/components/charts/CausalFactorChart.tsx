import { useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexFactorHistoryPoint, IndexSimulationResult } from "@/lib/api";
import { factorLabel } from "@/lib/factorEventMapping";
import { factorSeriesValues, niftyCloseSeries, pivotFactorHistoryWide } from "@/lib/factorHistoryUtils";

interface SensitivityPoint {
  factor_delta_pct?: number;
  index_level?: number;
  return_pct?: number;
}

interface Props {
  mode: "history" | "shock";
  activeFactor: string;
  factorHistory?: IndexFactorHistoryPoint[];
  sensitivityPoints?: SensitivityPoint[];
  spot?: number;
  baselineReturnPct?: number;
  simulation?: IndexSimulationResult | null;
  height?: number;
}

function shockTimeLabels(points: SensitivityPoint[]): Array<{ time: Time; label: string }> {
  const base = new Date();
  base.setHours(12, 0, 0, 0);
  return points.map((p, i) => {
    const d = new Date(base);
    d.setDate(d.getDate() + i);
    return {
      time: d.toISOString().slice(0, 10) as Time,
      label: `${p.factor_delta_pct ?? 0}%`,
    };
  });
}

export function CausalFactorChart({
  mode,
  activeFactor,
  factorHistory = [],
  sensitivityPoints = [],
  spot = 0,
  baselineReturnPct = 0,
  simulation,
  height = 280,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const primaryRef = useRef<ISeriesApi<"Line"> | null>(null);
  const { dark } = useDarkMode();

  const wide = useMemo(() => pivotFactorHistoryWide(factorHistory), [factorHistory]);
  const niftySeries = useMemo(() => niftyCloseSeries(wide), [wide]);
  const factorSeries = useMemo(
    () => factorSeriesValues(factorHistory, activeFactor),
    [factorHistory, activeFactor],
  );

  useEffect(() => {
    if (!containerRef.current) return;
    const theme = getChartTheme();

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
      primaryRef.current = null;
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
      leftPriceScale: { visible: mode === "history", borderColor: `${theme.textColor}30` },
      timeScale: { borderColor: `${theme.textColor}30`, timeVisible: mode === "history" },
    });
    chartRef.current = chart;

    if (mode === "shock" && sensitivityPoints.length >= 2) {
      const times = shockTimeLabels(sensitivityPoints);
      const baselineLevel =
        sensitivityPoints.find((p) => p.factor_delta_pct === 0)?.index_level ??
        (spot > 0 ? spot * (1 + baselineReturnPct / 100) : null);

      const niftyLine = chart.addSeries(LineSeries, {
        color: theme.infoColor,
        lineWidth: 2,
        title: "Nifty at horizon",
        priceScaleId: "right",
      });
      primaryRef.current = niftyLine;
      niftyLine.setData(
        sensitivityPoints.map((p, i) => ({
          time: times[i].time,
          value: Number(p.index_level ?? 0),
        })),
      );

      if (simulation?.index_level != null) {
        const scenLine = chart.addSeries(LineSeries, {
          color: theme.upColor,
          lineWidth: 2,
          lineStyle: 2,
          title: "Your shock",
          priceScaleId: "right",
        });
        const scenIdx = Math.max(
          0,
          sensitivityPoints.findIndex((p) => Math.round(Number(p.factor_delta_pct ?? 0)) === 0),
        );
        scenLine.setData([
          {
            time: times[0].time,
            value: baselineLevel ?? Number(sensitivityPoints[0].index_level ?? 0),
          },
          { time: times[scenIdx]?.time ?? times[times.length - 1].time, value: simulation.index_level },
        ]);
      }

      chart.timeScale().fitContent();
    } else if (mode === "history" && niftySeries.length >= 2) {
      const niftyLine = chart.addSeries(LineSeries, {
        color: theme.infoColor,
        lineWidth: 2,
        title: "Nifty 50",
        priceScaleId: "right",
      });
      primaryRef.current = niftyLine;
      niftyLine.setData(
        niftySeries
          .filter((r) => r.close != null)
          .map((r) => ({ time: r.date as Time, value: r.close as number })),
      );

      if (factorSeries.length >= 2) {
        const secLine = chart.addSeries(LineSeries, {
          color: theme.warningColor,
          lineWidth: 2,
          title: factorLabel(activeFactor),
          priceScaleId: "left",
        });
        secLine.setData(
          factorSeries
            .filter((r) => r.value != null)
            .map((r) => ({ time: r.date as Time, value: r.value as number })),
        );
      }

      chart.timeScale().fitContent();
    }

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [
    mode,
    activeFactor,
    sensitivityPoints,
    niftySeries,
    factorSeries,
    spot,
    baselineReturnPct,
    simulation,
    height,
    dark,
  ]);

  const hasData =
    (mode === "shock" && sensitivityPoints.length >= 2) ||
    (mode === "history" && niftySeries.length >= 2);

  if (!hasData) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed text-[12px] text-muted-foreground"
        style={{ height }}
      >
        {mode === "history"
          ? "Load factor history to compare Nifty vs this driver."
          : "No reconciled sensitivity curve for this factor."}
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
      {mode === "shock" ? (
        <p className="text-[10px] text-muted-foreground">
          Isolated ± shock to {factorLabel(activeFactor)} → Nifty at horizon (reconciled to headline at
          0%).
        </p>
      ) : (
        <p className="text-[10px] text-muted-foreground">
          Historical co-movement: Nifty (right) vs {factorLabel(activeFactor)} (left).
        </p>
      )}
    </div>
  );
}
