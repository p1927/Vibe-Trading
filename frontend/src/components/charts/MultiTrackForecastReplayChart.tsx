import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import {
  buildForwardPaths,
  listForecastAnchorPoints,
  resolveForecastForDate,
  type ForecastSnapshot,
  type PricePoint,
} from "@/lib/forecastReplayUtils";
import { trackColor } from "@/lib/trackScoreboardUtils";

export interface MultiTrackSeriesInput {
  trackId: string;
  label: string;
  forecastIndex: Map<string, ForecastSnapshot>;
}

interface Props {
  horizonDays: number;
  prices: PricePoint[];
  tracks: MultiTrackSeriesInput[];
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
  chart.timeScale().setVisibleLogicalRange({ from, to: anchorIdx + forward });
}

export function MultiTrackForecastReplayChart({
  horizonDays,
  prices,
  tracks,
  height = 400,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const historyRef = useRef<ISeriesApi<"Line"> | null>(null);
  const actualRef = useRef<ISeriesApi<"Line"> | null>(null);
  const predictedRefs = useRef<ISeriesApi<"Line">[]>([]);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const { dark } = useDarkMode();
  const [chartEpoch, setChartEpoch] = useState(0);

  const lastPriceDate = prices.length ? prices[prices.length - 1].date : "";
  const firstPriceDate = prices.length ? prices[0].date : "";

  const unionAnchors = useMemo(() => {
    const dateSet = new Set<string>();
    for (const t of tracks) {
      for (const d of t.forecastIndex.keys()) dateSet.add(d);
    }
    return [...dateSet].sort();
  }, [tracks]);

  const [anchorDate, setAnchorDate] = useState(() => lastPriceDate);

  useEffect(() => {
    if (!lastPriceDate) return;
    setAnchorDate((prev) => {
      if (!prev || prev > lastPriceDate) return lastPriceDate;
      if (prev < firstPriceDate) return firstPriceDate;
      return prev;
    });
  }, [lastPriceDate, firstPriceDate]);

  const stepForecastAnchor = useCallback(
    (direction: -1 | 1) => {
      if (!unionAnchors.length) return;
      let idx = unionAnchors.indexOf(anchorDate);
      if (idx < 0) {
        idx = unionAnchors.findIndex((d) => d > anchorDate);
        if (idx < 0) idx = unionAnchors.length - 1;
        else if (direction < 0) idx = Math.max(0, idx - 1);
      } else {
        idx = Math.min(unionAnchors.length - 1, Math.max(0, idx + direction));
      }
      setAnchorDate(unionAnchors[idx]);
    },
    [unionAnchors, anchorDate],
  );

  const trackPaths = useMemo(() => {
    return tracks.map((t) => {
      const anchor = resolveForecastForDate(anchorDate, t.forecastIndex);
      if (!anchor || !lastPriceDate) {
        return { trackId: t.trackId, label: t.label, paths: null, anchor: anchor ?? null };
      }
      return {
        trackId: t.trackId,
        label: t.label,
        anchor,
        paths: buildForwardPaths(anchor, horizonDays, prices, lastPriceDate),
      };
    });
  }, [tracks, anchorDate, horizonDays, prices, lastPriceDate]);

  const markerPoints = useMemo(() => {
    const theme = getChartTheme();
    const markers: SeriesMarker<Time>[] = [];
    for (const t of tracks) {
      const points = listForecastAnchorPoints(t.forecastIndex, prices, firstPriceDate, lastPriceDate);
      for (const p of points) {
        const isAnchor = p.date === anchorDate;
        markers.push({
          time: p.date as Time,
          position: "atPriceMiddle",
          price: p.price,
          shape: isAnchor ? "square" : "circle",
          color: isAnchor ? theme.warningColor : trackColor(t.trackId),
          text: `${p.expectedReturnPct >= 0 ? "+" : ""}${p.expectedReturnPct.toFixed(1)}%`,
        });
      }
    }
    return markers;
  }, [tracks, prices, firstPriceDate, lastPriceDate, anchorDate]);

  const syncChartData = useCallback(() => {
    if (!historyRef.current || !actualRef.current) return;
    historyRef.current.setData(prices.length ? toLineData(prices) : []);

    const primaryPaths = trackPaths.find((t) => t.paths)?.paths ?? null;
    if (primaryPaths) actualRef.current.setData(toLineData(primaryPaths.actual));
    else actualRef.current.setData([]);

    for (let i = 0; i < predictedRefs.current.length; i += 1) {
      const series = predictedRefs.current[i];
      const row = trackPaths[i];
      if (!series) continue;
      if (row?.paths) series.setData(toLineData(row.paths.predicted));
      else series.setData([]);
    }

    markersRef.current?.setMarkers(markerPoints);

    if (chartRef.current && prices.length && primaryPaths) {
      focusChartWindow(
        chartRef.current,
        prices,
        anchorDate,
        horizonDays,
        primaryPaths.predicted.length,
      );
    }
  }, [prices, trackPaths, markerPoints, anchorDate, horizonDays]);

  const mountChart = useCallback(
    (container: HTMLDivElement) => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        historyRef.current = null;
        actualRef.current = null;
        predictedRefs.current = [];
        markersRef.current = null;
      }

      const width = container.clientWidth;
      if (width <= 0) return false;

      const t = getChartTheme();
      const chart = createChart(container, {
        width,
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
        crosshair: { mode: CrosshairMode.Normal },
        handleScroll: { mouseWheel: true, pressedMouseMove: true },
        handleScale: { mouseWheel: true, pinch: true },
      });

      historyRef.current = chart.addSeries(LineSeries, {
        color: t.infoColor,
        lineWidth: 2,
        title: "Nifty 50",
        lastValueVisible: true,
        priceLineVisible: false,
      });
      markersRef.current = createSeriesMarkers(historyRef.current, []);

      actualRef.current = chart.addSeries(LineSeries, {
        color: t.upColor,
        lineWidth: 2,
        title: "Actual path",
        lastValueVisible: true,
        priceLineVisible: false,
      });

      predictedRefs.current = tracks.map((track) =>
        chart.addSeries(LineSeries, {
          color: trackColor(track.trackId),
          lineWidth: 2,
          lineStyle: 2,
          title: track.label,
          lastValueVisible: false,
          priceLineVisible: false,
        }),
      );

      chartRef.current = chart;
      chart.subscribeClick((param) => {
        if (!param.time) return;
        const d = timeToIsoDay(param.time);
        if (d) setAnchorDate(d);
      });
      setChartEpoch((v) => v + 1);
      return true;
    },
    [height, dark, tracks],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    mountChart(container);
    const ro = new ResizeObserver(() => {
      if (!containerRef.current) return;
      const el = containerRef.current;
      if (!chartRef.current && el.clientWidth > 0) mountChart(el);
      else if (chartRef.current && el.clientWidth > 0) {
        chartRef.current.applyOptions({ width: el.clientWidth });
      }
    });
    ro.observe(container);
    return () => {
      ro.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, [mountChart]);

  useEffect(() => {
    syncChartData();
  }, [syncChartData, chartEpoch]);

  if (!prices.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Recompute scoreboard to load Nifty history and track forecasts.
      </div>
    );
  }

  const anchorIdx = prices.findIndex((p) => p.date === anchorDate);

  return (
    <div className="space-y-3">
      <div ref={containerRef} className="w-full" style={{ height }} />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[11px]">
        <label className="flex min-w-[200px] flex-1 items-center gap-2">
          <span className="shrink-0 text-muted-foreground">Anchor</span>
          <input
            type="range"
            min={0}
            max={Math.max(0, prices.length - 1)}
            value={anchorIdx >= 0 ? anchorIdx : prices.length - 1}
            onChange={(e) => {
              const row = prices[Number(e.target.value)];
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
            disabled={!unionAnchors.length}
            onClick={() => stepForecastAnchor(-1)}
          >
            ← Prev forecast
          </button>
          <button
            type="button"
            className="rounded border px-2 py-0.5 text-[10px] hover:bg-muted disabled:opacity-40"
            disabled={!unionAnchors.length}
            onClick={() => stepForecastAnchor(1)}
          >
            Next forecast →
          </button>
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {trackPaths.map((row) => {
          const err =
            row.paths?.maturedActualReturnPct != null && row.anchor
              ? row.paths.maturedActualReturnPct - row.anchor.expectedReturnPct
              : null;
          return (
            <div
              key={row.trackId}
              className="rounded-lg border border-border/50 px-3 py-2 text-[11px]"
              style={{ borderLeftColor: trackColor(row.trackId), borderLeftWidth: 3 }}
            >
              <p className="font-medium">{row.label}</p>
              {row.anchor ? (
                <>
                  <p className="tabular-nums text-muted-foreground">
                    Forecast {fmtPct(row.anchor.expectedReturnPct)} → {fmtLevel(row.paths?.horizonTarget)}
                  </p>
                  <p className="tabular-nums">
                    Actual{" "}
                    {row.paths?.maturedActualReturnPct != null
                      ? fmtPct(row.paths.maturedActualReturnPct)
                      : "pending"}{" "}
                    · error {err != null ? fmtPct(err) : "—"}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground">No forecast on {anchorDate}</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
