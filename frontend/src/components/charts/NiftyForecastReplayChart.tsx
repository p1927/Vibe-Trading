import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import type { IndexBacktestDailyEval, IndexPredictionHistoryRow } from "@/lib/api";
import {
  buildForecastIndex,
  buildForwardPaths,
  forecastVisibleRange,
  mergePriceSeries,
  resolveForecastForDate,
  type LiveForecastInput,
  type PricePoint,
} from "@/lib/forecastReplayUtils";

interface Props {
  horizonDays: number;
  ledgerRows?: IndexPredictionHistoryRow[];
  backtestEvals?: IndexBacktestDailyEval[];
  priceSeries?: Array<{ date?: string; close?: number | null }>;
  liveForecast?: LiveForecastInput;
  height?: number;
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function toLineData(points: PricePoint[]): Array<{ time: Time; value: number }> {
  return points.map((p) => ({ time: p.date as Time, value: p.close }));
}

function timeToIsoDay(time: Time): string | null {
  if (typeof time === "string") return time.slice(0, 10);
  if (typeof time === "number") return new Date(time * 1000).toISOString().slice(0, 10);
  if (typeof time === "object" && time && "year" in time) {
    const { year, month, day } = time;
    return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }
  return null;
}

export function NiftyForecastReplayChart({
  horizonDays,
  ledgerRows = [],
  backtestEvals = [],
  priceSeries = [],
  liveForecast,
  height = 380,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const historyRef = useRef<ISeriesApi<"Line"> | null>(null);
  const predictedRef = useRef<ISeriesApi<"Line"> | null>(null);
  const actualRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bandHighRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bandLowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const { dark } = useDarkMode();

  const prices = useMemo(() => mergePriceSeries([priceSeries]), [priceSeries]);
  const lastPriceDate = prices.length ? prices[prices.length - 1].date : "";
  const firstPriceDate = prices.length ? prices[0].date : "";

  const forecastIndex = useMemo(
    () =>
      buildForecastIndex(ledgerRows, backtestEvals, liveForecast, {
        horizonDays,
        lastPriceDate,
      }),
    [ledgerRows, backtestEvals, liveForecast, horizonDays, lastPriceDate],
  );

  const forecastDates = useMemo(
    () => new Set([...forecastIndex.keys()].filter((d) => d >= firstPriceDate && d <= lastPriceDate)),
    [forecastIndex, firstPriceDate, lastPriceDate],
  );

  const [anchorDate, setAnchorDate] = useState<string>(() => lastPriceDate);

  useEffect(() => {
    if (!lastPriceDate) return;
    setAnchorDate((prev) => {
      if (!prev || prev > lastPriceDate) return lastPriceDate;
      if (prev < firstPriceDate) return firstPriceDate;
      return prev;
    });
  }, [lastPriceDate, firstPriceDate]);

  const anchorForecast = useMemo(
    () => (anchorDate ? resolveForecastForDate(anchorDate, forecastIndex) : null),
    [anchorDate, forecastIndex],
  );

  const paths = useMemo(() => {
    if (!anchorForecast || !lastPriceDate) return null;
    return buildForwardPaths(anchorForecast, horizonDays, prices, lastPriceDate);
  }, [anchorForecast, horizonDays, prices, lastPriceDate]);

  const focusAnchor = useCallback(() => {
    if (!chartRef.current || !anchorDate) return;
    const range = forecastVisibleRange(prices, anchorDate, horizonDays);
    if (!range) return;
    try {
      chartRef.current.timeScale().setVisibleRange({
        from: range.from as Time,
        to: range.to as Time,
      });
    } catch {
      chartRef.current.timeScale().fitContent();
    }
  }, [anchorDate, horizonDays, prices]);

  const initChart = useCallback(() => {
    if (!containerRef.current) return;

    if (chartRef.current) {
      chartRef.current.remove();
      chartRef.current = null;
    }

    const t = getChartTheme();
    const container = containerRef.current;

    const chart = createChart(container, {
      width: container.clientWidth,
      height,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: t.textColor,
      },
      grid: {
        vertLines: { color: `${t.gridColor}88`, visible: true },
        horzLines: { color: `${t.gridColor}88`, visible: true },
      },
      rightPriceScale: {
        borderColor: `${t.axisColor}55`,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: `${t.axisColor}55`,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { labelVisible: true },
        horzLine: { labelVisible: true },
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    });

    const history = chart.addSeries(LineSeries, {
      color: t.infoColor,
      lineWidth: 2,
      title: "Nifty",
      lastValueVisible: true,
      priceLineVisible: false,
    });

    const bandLow = chart.addSeries(LineSeries, {
      color: `${t.infoColor}44`,
      lineWidth: 1,
      lineStyle: 2,
      title: "Band low",
      lastValueVisible: false,
      priceLineVisible: false,
    });

    const bandHigh = chart.addSeries(LineSeries, {
      color: `${t.infoColor}44`,
      lineWidth: 1,
      lineStyle: 2,
      title: "Band high",
      lastValueVisible: false,
      priceLineVisible: false,
    });

    const predicted = chart.addSeries(LineSeries, {
      color: t.warningColor,
      lineWidth: 2,
      lineStyle: 2,
      title: "Forecast",
      lastValueVisible: true,
      priceLineVisible: false,
    });

    const actual = chart.addSeries(LineSeries, {
      color: t.upColor,
      lineWidth: 2,
      title: "Actual path",
      lastValueVisible: true,
      priceLineVisible: false,
    });

    chartRef.current = chart;
    historyRef.current = history;
    predictedRef.current = predicted;
    actualRef.current = actual;
    bandHighRef.current = bandHigh;
    bandLowRef.current = bandLow;

    chart.subscribeClick((param) => {
      if (!param.time) return;
      const d = timeToIsoDay(param.time);
      if (d) setAnchorDate(d);
    });

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [height, dark]);

  useEffect(() => {
    const cleanup = initChart();
    return cleanup;
  }, [initChart]);

  useEffect(() => {
    if (!historyRef.current || !prices.length) return;
    historyRef.current.setData(toLineData(prices));
  }, [prices]);

  useEffect(() => {
    if (!predictedRef.current || !actualRef.current) return;

    if (!paths) {
      predictedRef.current.setData([]);
      actualRef.current.setData([]);
      bandHighRef.current?.setData([]);
      bandLowRef.current?.setData([]);
      return;
    }

    predictedRef.current.setData(toLineData(paths.predicted));
    actualRef.current.setData(toLineData(paths.actual));

    if (bandHighRef.current && bandLowRef.current) {
      if (paths.bandHigh.length > 1) {
        bandHighRef.current.setData(toLineData(paths.bandHigh));
        bandLowRef.current.setData(toLineData(paths.bandLow));
      } else {
        bandHighRef.current.setData([]);
        bandLowRef.current.setData([]);
      }
    }
  }, [paths]);

  useEffect(() => {
    focusAnchor();
  }, [focusAnchor, paths]);

  const anchorIdx = prices.findIndex((p) => p.date === anchorDate);
  const isLiveAnchor = anchorDate === lastPriceDate;
  const hasForecast = Boolean(anchorForecast);
  const errorPct =
    paths?.maturedActualReturnPct != null && anchorForecast
      ? paths.maturedActualReturnPct - anchorForecast.expectedReturnPct
      : null;

  if (!prices.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Run analysis to load Nifty history and forecasts.
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
      <div ref={containerRef} className="w-full" style={{ height }} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px]">
        <label className="flex min-w-[200px] flex-1 items-center gap-2">
          <span className="shrink-0 text-muted-foreground">Anchor</span>
          <input
            type="range"
            min={0}
            max={Math.max(0, prices.length - 1)}
            value={anchorIdx >= 0 ? anchorIdx : prices.length - 1}
            onChange={(e) => {
              const idx = Number(e.target.value);
              const row = prices[idx];
              if (row) setAnchorDate(row.date);
            }}
            className="flex-1 accent-primary"
          />
          <span className="shrink-0 tabular-nums font-medium">{anchorDate}</span>
        </label>
        <span className="text-muted-foreground">
          {forecastDates.size} day{forecastDates.size === 1 ? "" : "s"} with recorded forecasts
        </span>
      </div>

      {!hasForecast ? (
        <div className="rounded-lg border border-dashed border-muted-foreground/30 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
          No {horizonDays}d forecast was recorded for {anchorDate}. Pick a day with a ledger snapshot or
          backtest evaluation (every ~5 trading days in walk-forward history).
        </div>
      ) : null}

      {anchorForecast && paths ? (
        <div className="grid gap-2 text-[11px] sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <span className="text-muted-foreground">Spot at anchor</span>
            <p className="font-semibold tabular-nums">{fmtLevel(anchorForecast.spot)}</p>
          </div>
          <div>
            <span className="text-muted-foreground">{horizonDays}d forecast</span>
            <p className="font-semibold tabular-nums">{fmtLevel(paths.horizonTarget)}</p>
            <p className="text-muted-foreground">{fmtPct(anchorForecast.expectedReturnPct)}</p>
          </div>
          <div>
            <span className="text-muted-foreground">Actual (if matured)</span>
            <p className="font-semibold tabular-nums">
              {paths.maturedActualReturnPct != null
                ? fmtPct(paths.maturedActualReturnPct)
                : isLiveAnchor
                  ? "In progress"
                  : "Pending"}
            </p>
          </div>
          <div>
            <span className="text-muted-foreground">Forecast error</span>
            <p
              className={`font-semibold tabular-nums ${
                errorPct != null
                  ? Math.abs(errorPct) < 1
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-amber-700 dark:text-amber-400"
                  : ""
              }`}
            >
              {errorPct != null ? fmtPct(errorPct) : "—"}
            </p>
          </div>
        </div>
      ) : null}

      <p className="text-[10px] text-muted-foreground">
        Drag the slider or click the chart to pick an anchor — dashed orange is the {horizonDays}d forecast
        from that day; green is what Nifty actually did over the same window. The view auto-focuses on the
        anchor and forward path.
      </p>
    </div>
  );
}
