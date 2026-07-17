import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { IndexTrackChartPayload } from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { fmtPct, trackColor } from "@/lib/trackScoreboardUtils";
import { createLightweightChart, fitLightweightChart } from "@/lib/lightweightChartOptions";
import { LightweightChartZoomBar } from "@/components/charts/LightweightChartZoomBar";

interface Props {
  chart: IndexTrackChartPayload | null | undefined;
  height?: number;
  showCombiners?: boolean;
}

function toTime(date: string): Time {
  return date.slice(0, 10) as Time;
}

export function TrackScoreboardChart({ chart, height = 360, showCombiners = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const actualRef = useRef<ISeriesApi<"Line"> | null>(null);
  const trackRefs = useRef<ISeriesApi<"Line">[]>([]);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const { dark } = useDarkMode();
  const [chartEpoch, setChartEpoch] = useState(0);
  const [activeChart, setActiveChart] = useState<IChartApi | null>(null);

  const visibleTracks = useMemo(() => {
    if (!chart?.track_series) return [];
    return chart.track_series.filter(
      (t) => showCombiners || !t.track_id.startsWith("combiner:"),
    );
  }, [chart?.track_series, showCombiners]);

  const dates = useMemo(() => {
    if (!chart?.eval_dates?.length) return [];
    const out = [...chart.eval_dates];
    const liveDate = chart.live_point?.date;
    if (liveDate && !out.includes(liveDate)) out.push(liveDate);
    return out.sort();
  }, [chart?.eval_dates, chart?.live_point?.date]);

  const legendItems = useMemo(() => {
    const items: Array<{ id: string; label: string; color: string; dashed?: boolean }> = [
      { id: "actual", label: "Actual Nifty (forward return)", color: trackColor("actual") },
    ];
    for (const track of visibleTracks) {
      items.push({
        id: track.track_id,
        label: track.label ?? track.track_id,
        color: trackColor(track.track_id),
        dashed: track.track_id.startsWith("combiner:"),
      });
    }
    return items;
  }, [visibleTracks]);

  const mountChart = useCallback(
    (container: HTMLDivElement) => {
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        actualRef.current = null;
        trackRefs.current = [];
        markersRef.current = null;
      }

      if (container.clientWidth <= 0) return false;

      const t = getChartTheme();
      const instance = createLightweightChart(container, height);
      chartRef.current = instance;
      setActiveChart(instance);

      actualRef.current = instance.addSeries(LineSeries, {
        color: trackColor("actual"),
        lineWidth: 3,
        title: "Actual",
        lastValueVisible: true,
        priceLineVisible: false,
      });
      markersRef.current = createSeriesMarkers(actualRef.current, []);

      actualRef.current.createPriceLine({
        price: 0,
        color: `${t.textColor}44`,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "0%",
      });

      trackRefs.current = visibleTracks.map((track) =>
        instance.addSeries(LineSeries, {
          color: trackColor(track.track_id),
          lineWidth: track.track_id.startsWith("combiner:") ? 1 : 2,
          lineStyle: track.track_id.startsWith("combiner:") ? LineStyle.Dashed : LineStyle.Solid,
          title: track.label ?? track.track_id,
          lastValueVisible: false,
          priceLineVisible: false,
        }),
      );

      setChartEpoch((v) => v + 1);
      return true;
    },
    [height, dark, visibleTracks],
  );

  const syncData = useCallback(() => {
    const instance = chartRef.current;
    const actualSeries = actualRef.current;
    if (!instance || !actualSeries || !chart?.eval_dates?.length) return;

    const actualMap = new Map(
      (chart.actual_series ?? []).map((p) => [p.date, p.actual_pct]),
    );
    const live = chart.live_point;

    const actualData = dates
      .map((d) => {
        const v = actualMap.get(d);
        if (v == null || !Number.isFinite(v)) return null;
        return { time: toTime(d), value: v };
      })
      .filter(Boolean) as Array<{ time: Time; value: number }>;

    actualSeries.setData(actualData);

    const markers: SeriesMarker<Time>[] = actualData.map((pt) => ({
      time: pt.time,
      position: "inBar",
      shape: "circle",
      color: trackColor("actual"),
      size: 1,
    }));
    markersRef.current?.setMarkers(markers);

    trackRefs.current.forEach((series, idx) => {
      const track = visibleTracks[idx];
      if (!track) return;
      const pointMap = new Map((track.points ?? []).map((p) => [p.date, p.predicted_pct]));
      const data = dates
        .map((d) => {
          let v = pointMap.get(d);
          if (v == null && live?.date === d && live.tracks?.[track.track_id] != null) {
            v = live.tracks[track.track_id];
          }
          if (v == null || !Number.isFinite(v)) return null;
          return { time: toTime(d), value: v };
        })
        .filter(Boolean) as Array<{ time: Time; value: number }>;
      series.setData(data);
    });

    fitLightweightChart(instance);
  }, [chart, dates, visibleTracks]);

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
      setActiveChart(null);
    };
  }, [mountChart]);

  useEffect(() => {
    syncData();
  }, [syncData, chartEpoch]);

  if (!chart?.eval_dates?.length) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed text-[12px] text-muted-foreground"
        style={{ height }}
      >
        No walk-forward eval points yet — run Recompute scoreboard.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div ref={containerRef} className="w-full rounded-lg border border-border/40" style={{ height }} />
      <LightweightChartZoomBar chart={activeChart} />
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
        {legendItems.map((item) => (
          <span key={item.id} className="inline-flex items-center gap-1">
            <span
              className="inline-block h-0.5 w-4"
              style={{
                backgroundColor: item.color,
                borderBottom: item.dashed ? `2px dashed ${item.color}` : undefined,
                height: item.dashed ? 0 : undefined,
              }}
            />
            {item.label}
          </span>
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground">
        {chart.horizon_days ?? 14}d forward return % at each OOS eval date · {fmtPct(0).replace("+", "")} dashed
        reference line
      </p>
    </div>
  );
}
