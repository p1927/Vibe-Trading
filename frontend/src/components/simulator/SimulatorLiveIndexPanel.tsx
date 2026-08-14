/**
 * SimulatorLiveIndexPanel — Phase 9
 *
 * Small always-visible panel showing the selected underlying's live spot
 * (LTP, prev-close delta) plus a TradingView Lightweight Chart line series
 * of recent price ticks. Polls /trade/hub/market-data/{ticks,spot} at
 * 2 s when a recording is active, 5 s when idle.
 *
 * Auto-switches underlying when `symbol` prop changes (driven by the
 * Record section's underlying checkboxes).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { createLightweightChart } from "@/lib/lightweightChartOptions";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api } from "@/lib/api";

interface Props {
  symbol: string;
  exchange?: string;
  isRecordingActive?: boolean;
  height?: number;
}

interface Tick {
  ts: string;
  price: number;
  volume?: number | null;
  open?: number | null;
}

function formatLtp(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function formatPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

export function SimulatorLiveIndexPanel({
  symbol,
  exchange = "NSE_INDEX",
  isRecordingActive = false,
  height = 200,
}: Props) {
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [spot, setSpot] = useState<{
    ltp: number;
    prev_close: number | null;
    source: string;
    as_of: string | null;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const { dark } = useDarkMode();

  // Poll cadence: 2 s while recording, 5 s otherwise.
  const pollMs = isRecordingActive ? 2000 : 5000;
  const prevCloseRef = useRef<number | null>(null);

  // Reset chart when symbol changes (avoids stale data bleed-over).
  const symbolKey = `${symbol}:${exchange}`;

  useEffect(() => {
    setTicks([]);
    setSpot(null);
    setError(null);
    setLoading(true);
    prevCloseRef.current = null;
    if (seriesRef.current) {
      seriesRef.current.setData([]);
    }
  }, [symbolKey]);

  // Fetch loop.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const [ticksRes, spotRes] = await Promise.all([
          api.getHubMarketDataTicks({
            symbol,
            exchange,
            since_minutes: 240,
            limit: 500,
          }),
          api.getHubMarketDataSpot({ symbol, exchange }),
        ]);
        if (cancelled) return;
        if (ticksRes.status === "ok") {
          const next: Tick[] = ticksRes.ticks.map((t) => ({
            ts: t.ts,
            price: t.price,
            volume: t.volume ?? null,
            open: t.open ?? null,
          }));
          setTicks(next);
          if (next.length && prevCloseRef.current == null && next[0].open != null) {
            prevCloseRef.current = next[0].open ?? null;
          }
        }
        if (spotRes.status === "ok" && spotRes.spot) {
          setSpot({
            ltp: spotRes.spot.ltp,
            prev_close: spotRes.spot.prev_close ?? null,
            source: spotRes.spot.source,
            as_of: spotRes.spot.as_of ?? null,
          });
          if (spotRes.spot.prev_close != null && prevCloseRef.current == null) {
            prevCloseRef.current = spotRes.spot.prev_close;
          }
        }
        setError(null);
        setLoading(false);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "fetch failed");
          setLoading(false);
        }
      }
    };
    tick();
    const handle = window.setInterval(tick, pollMs);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [symbolKey, pollMs]);

  // Mount chart once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createLightweightChart(container, height);
    const t = getChartTheme();
    seriesRef.current = chart.addSeries(LineSeries, {
      color: t.infoColor,
      lineWidth: 2,
      title: symbol,
      lastValueVisible: true,
      priceLineVisible: false,
    });
    chartRef.current = chart;
    const ro = new ResizeObserver(() => {
      if (!container) return;
      const w = container.clientWidth;
      if (w > 0) chart.applyOptions({ width: w });
    });
    ro.observe(container);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height, dark, symbolKey]);

  // Sync ticks → series. Lightweight Charts wants ascending time.
  // We use UTCTimestamp (seconds since epoch) for sub-day resolution —
  // string formats get auto-validated as BusinessDay which rejects ISO strings.
  useEffect(() => {
    if (!seriesRef.current) return;
    const series = seriesRef.current;
    if (ticks.length === 0) {
      series.setData([]);
      return;
    }
    const sorted = [...ticks].sort((a, b) => a.ts.localeCompare(b.ts));
    const mapped = sorted
      .filter((t) => Number.isFinite(t.price))
      .map((t) => {
        const ms = Date.parse(t.ts);
        return { time: (ms / 1000) as Time, value: t.price };
      })
      .filter((p) => Number.isFinite(p.time));
    // Lightweight Charts requires strictly ascending, unique times — two
    // ticks landing in the same second (sub-second polling) would tie.
    // Keep the latest value for each timestamp.
    const data = mapped.filter((p, i) => i === mapped.length - 1 || p.time !== mapped[i + 1].time);
    series.setData(data);
  }, [ticks]);

  const change = useMemo(() => {
    if (!spot || spot.prev_close == null) return null;
    return spot.ltp - spot.prev_close;
  }, [spot]);

  const changePct = useMemo(() => {
    if (!spot || spot.prev_close == null || spot.prev_close === 0) return null;
    return ((spot.ltp - spot.prev_close) / spot.prev_close) * 100;
  }, [spot]);

  const positive = (change ?? 0) >= 0;

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          {symbol} · LIVE
          {isRecordingActive && (
            <span className="ml-2 inline-block h-2 w-2 animate-pulse rounded-full bg-red-500 align-middle" />
          )}
        </p>
        {spot?.source && (
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            via {spot.source}
          </span>
        )}
      </div>
      <div className="mt-2 flex items-baseline gap-3">
        <span className="text-3xl font-semibold tabular-nums" data-testid="live-spot-ltp">
          {formatLtp(spot?.ltp)}
        </span>
        {change != null && (
          <span
            className={`text-sm tabular-nums ${
              positive ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"
            }`}
            data-testid="live-spot-change"
          >
            {positive ? "▲" : "▼"} {formatLtp(Math.abs(change))} ({formatPct(changePct)})
          </span>
        )}
      </div>
      {loading && !spot && (
        <p className="mt-1 text-xs text-muted-foreground">Loading live data…</p>
      )}
      {error && (
        <p className="mt-1 text-xs text-destructive" data-testid="live-spot-error">
          {error}
        </p>
      )}
      {ticks.length === 0 && !loading && !error && (
        <p className="mt-1 text-xs text-muted-foreground">
          No live ticks — start a recording or check TimescaleDB.
        </p>
      )}
      <div ref={containerRef} className="mt-3 w-full" style={{ height }} />
    </div>
  );
}

export default SimulatorLiveIndexPanel;
