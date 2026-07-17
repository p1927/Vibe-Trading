import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createSeriesMarkers,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { LightweightChartZoomBar } from "@/components/charts/LightweightChartZoomBar";
import { getChartTheme } from "@/lib/chart-theme";
import { createLightweightChart } from "@/lib/lightweightChartOptions";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexBacktestDailyEval, IndexPredictionHistoryRow } from "@/lib/api";
import {
  buildForecastIndex,
  buildForwardPaths,
  listForecastAnchorPoints,
  mergePriceSeries,
  resolveForecastForDate,
  type ForecastAnchorPoint,
  type ForecastSnapshot,
  type LiveForecastInput,
  type PricePoint,
} from "@/lib/forecastReplayUtils";

interface Props {
  horizonDays: number;
  ledgerRows?: IndexPredictionHistoryRow[];
  backtestEvals?: IndexBacktestDailyEval[];
  priceSeries?: Array<{ date?: string; close?: number | null }>;
  liveForecast?: LiveForecastInput;
  /** When set, uses this index instead of ledger/backtest/live builders (track scoreboard). */
  forecastIndex?: Map<string, ForecastSnapshot>;
  predictedLineColor?: string;
  legendBacktestLabel?: string;
  emptyForecastHint?: string;
  priceLoading?: boolean;
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

function focusChartWindow(
  chart: IChartApi,
  prices: PricePoint[],
  anchorDate: string,
  horizonDays: number,
  predictedCount: number,
): void {
  if (!prices.length) return;
  const anchorIdx = prices.findIndex((p) => p.date === anchorDate);
  if (anchorIdx < 0) {
    chart.timeScale().fitContent();
    return;
  }
  const from = Math.max(0, anchorIdx - 30);
  const forward = Math.max(horizonDays + 2, predictedCount);
  const to = anchorIdx + forward;
  try {
    chart.timeScale().setVisibleLogicalRange({ from, to });
  } catch {
    chart.timeScale().fitContent();
  }
}

function markerColor(
  source: ForecastAnchorPoint["source"],
  isAnchor: boolean,
  theme: ReturnType<typeof getChartTheme>,
): string {
  if (isAnchor) return theme.warningColor;
  if (source === "ledger") return theme.warningColor;
  if (source === "live") return theme.infoColor;
  return `${theme.infoColor}99`;
}

function buildSeriesMarkers(
  points: ForecastAnchorPoint[],
  anchorDate: string,
  theme: ReturnType<typeof getChartTheme>,
): SeriesMarker<Time>[] {
  return points.map((p) => {
    const isAnchor = p.date === anchorDate;
    const ret = p.expectedReturnPct;
    return {
      time: p.date as Time,
      position: "atPriceMiddle",
      price: p.price,
      shape: isAnchor ? "square" : "circle",
      color: markerColor(p.source, isAnchor, theme),
      text: `${ret >= 0 ? "+" : ""}${ret.toFixed(1)}%`,
    };
  });
}

export function NiftyForecastReplayChart({
  horizonDays,
  ledgerRows = [],
  backtestEvals = [],
  priceSeries = [],
  liveForecast,
  forecastIndex: forecastIndexOverride,
  predictedLineColor,
  legendBacktestLabel = "Walk-forward backtest",
  emptyForecastHint,
  priceLoading = false,
  height = 380,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const historyRef = useRef<ISeriesApi<"Line"> | null>(null);
  const predictedRef = useRef<ISeriesApi<"Line"> | null>(null);
  const actualRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bandHighRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bandLowRef = useRef<ISeriesApi<"Line"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const { dark } = useDarkMode();
  const [chartEpoch, setChartEpoch] = useState(0);
  const [activeChart, setActiveChart] = useState<IChartApi | null>(null);

  const prices = useMemo(() => mergePriceSeries([priceSeries]), [priceSeries]);
  const lastPriceDate = prices.length ? prices[prices.length - 1].date : "";
  const firstPriceDate = prices.length ? prices[0].date : "";

  const forecastIndex = useMemo(() => {
    if (forecastIndexOverride) return forecastIndexOverride;
    return buildForecastIndex(ledgerRows, backtestEvals, liveForecast, {
      horizonDays,
      lastPriceDate,
    });
  }, [
    forecastIndexOverride,
    ledgerRows,
    backtestEvals,
    liveForecast,
    horizonDays,
    lastPriceDate,
  ]);

  const forecastAnchors = useMemo(
    () => listForecastAnchorPoints(forecastIndex, prices, firstPriceDate, lastPriceDate),
    [forecastIndex, prices, firstPriceDate, lastPriceDate],
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

  const stepForecastAnchor = useCallback(
    (direction: -1 | 1) => {
      if (!forecastAnchors.length) return;
      const dates = forecastAnchors.map((a) => a.date);
      let idx = dates.indexOf(anchorDate);
      if (idx < 0) {
        idx = dates.findIndex((d) => d > anchorDate);
        if (idx < 0) idx = dates.length - 1;
        else if (direction < 0) idx = Math.max(0, idx - 1);
      } else {
        idx = Math.min(dates.length - 1, Math.max(0, idx + direction));
      }
      setAnchorDate(dates[idx]);
    },
    [forecastAnchors, anchorDate],
  );

  const syncChartData = useCallback(() => {
    if (!historyRef.current || !predictedRef.current || !actualRef.current) return;

    historyRef.current.setData(prices.length ? toLineData(prices) : []);

    if (paths) {
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
    } else {
      predictedRef.current.setData([]);
      actualRef.current.setData([]);
      bandHighRef.current?.setData([]);
      bandLowRef.current?.setData([]);
    }

    if (markersRef.current) {
      const theme = getChartTheme();
      markersRef.current.setMarkers(buildSeriesMarkers(forecastAnchors, anchorDate, theme));
    }

    if (chartRef.current && prices.length) {
      focusChartWindow(
        chartRef.current,
        prices,
        anchorDate,
        horizonDays,
        paths?.predicted.length ?? 0,
      );
    }
  }, [prices, paths, anchorDate, horizonDays, forecastAnchors]);

  const mountChart = useCallback(
    (container: HTMLDivElement) => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        historyRef.current = null;
        predictedRef.current = null;
        actualRef.current = null;
        bandHighRef.current = null;
        bandLowRef.current = null;
        markersRef.current = null;
      }

      const width = container.clientWidth;
      if (width <= 0) return false;

      const t = getChartTheme();
      const chart = createLightweightChart(container, height);
      chart.applyOptions({ width });

      historyRef.current = chart.addSeries(LineSeries, {
        color: t.infoColor,
        lineWidth: 2,
        title: "Nifty",
        lastValueVisible: true,
        priceLineVisible: false,
      });

      markersRef.current = createSeriesMarkers(historyRef.current, []);

      bandLowRef.current = chart.addSeries(LineSeries, {
        color: `${t.infoColor}44`,
        lineWidth: 1,
        lineStyle: 2,
        title: "Band low",
        lastValueVisible: false,
        priceLineVisible: false,
      });

      bandHighRef.current = chart.addSeries(LineSeries, {
        color: `${t.infoColor}44`,
        lineWidth: 1,
        lineStyle: 2,
        title: "Band high",
        lastValueVisible: false,
        priceLineVisible: false,
      });

      predictedRef.current = chart.addSeries(LineSeries, {
        color: predictedLineColor ?? t.warningColor,
        lineWidth: 2,
        lineStyle: 2,
        title: "Forecast",
        lastValueVisible: true,
        priceLineVisible: false,
      });

      actualRef.current = chart.addSeries(LineSeries, {
        color: t.upColor,
        lineWidth: 2,
        title: "Actual path",
        lastValueVisible: true,
        priceLineVisible: false,
      });

      chartRef.current = chart;
      setActiveChart(chart);

      chart.subscribeClick((param) => {
        if (!param.time) return;
        const d = timeToIsoDay(param.time);
        if (d) setAnchorDate(d);
      });

      setChartEpoch((v) => v + 1);
      return true;
    },
    [height, dark, predictedLineColor],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    mountChart(container);

    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      const el = containerRef.current;
      if (!chartRef.current && el.clientWidth > 0) {
        mountChart(el);
        return;
      }
      if (chartRef.current && el.clientWidth > 0) {
        chartRef.current.applyOptions({ width: el.clientWidth });
      }
    });
    ro.observe(container);

    return () => {
      ro.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      setActiveChart(null);
      historyRef.current = null;
      predictedRef.current = null;
      actualRef.current = null;
      bandHighRef.current = null;
      bandLowRef.current = null;
      markersRef.current = null;
    };
  }, [mountChart]);

  useEffect(() => {
    syncChartData();
  }, [syncChartData, chartEpoch]);

  const anchorIdx = prices.findIndex((p) => p.date === anchorDate);
  const isLiveAnchor = anchorDate === lastPriceDate;
  const hasForecast = Boolean(anchorForecast);
  const errorPct =
    paths?.maturedActualReturnPct != null && anchorForecast
      ? paths.maturedActualReturnPct - anchorForecast.expectedReturnPct
      : null;

  if (priceLoading && !prices.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Loading Nifty price history…
      </div>
    );
  }

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
      <LightweightChartZoomBar chart={activeChart} />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[11px]">
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
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="rounded border px-2 py-0.5 text-[10px] hover:bg-muted disabled:opacity-40"
            disabled={!forecastAnchors.length}
            onClick={() => stepForecastAnchor(-1)}
          >
            ← Prev forecast
          </button>
          <button
            type="button"
            className="rounded border px-2 py-0.5 text-[10px] hover:bg-muted disabled:opacity-40"
            disabled={!forecastAnchors.length}
            onClick={() => stepForecastAnchor(1)}
          >
            Next forecast →
          </button>
        </div>
        <span className="text-muted-foreground">
          {forecastAnchors.length} prediction day{forecastAnchors.length === 1 ? "" : "s"} on chart
        </span>
      </div>

      {forecastAnchors.length ? (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-primary/70" />
            {legendBacktestLabel}
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
            Ledger snapshot
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-block h-2.5 w-2.5 rotate-45 bg-primary" />
            Selected anchor
          </span>
        </div>
      ) : null}

      {!hasForecast ? (
        <div className="rounded-lg border border-dashed border-muted-foreground/30 bg-muted/20 px-3 py-2 text-[11px] text-muted-foreground">
          {emptyForecastHint ??
            `No ${horizonDays}d forecast was recorded for ${anchorDate}. Pick a day with a ledger snapshot or backtest evaluation (every ~5 trading days in walk-forward history).`}
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
        Markers show every day a {horizonDays}d forecast was recorded (walk-forward backtest + ledger).
        Click a marker or use Prev/Next forecast — dashed orange is the forecast from that anchor; green is
        what Nifty actually did.
      </p>
    </div>
  );
}
