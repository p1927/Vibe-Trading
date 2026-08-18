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
 *
 * When `isReplayArmed` is true, the panel sends `?replay=1` with both
 * ticks + spot requests so the bridge routes through the simulator's
 * ReplayService (advancing the sim clock and reading the catalog bar)
 * instead of the live broker or Timescale. The header badge reflects which
 * of the three sources actually answered (see `modeBadge` below): a true
 * broker quote ("BROKER · LIVE"), recently-recorded Timescale ticks
 * ("RECENT · RECORDED"), or a replay session ("REPLAY") — rather than
 * calling everything "LIVE".
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
  isReplayArmed?: boolean;
  height?: number;
  /** Reports the backend's `session_open` read on every non-replay poll, so
   *  a parent can show a page-level "market closed" state. `null` means the
   *  backend's own check failed — treat as unknown, not "closed". */
  onSessionOpenChange?: (open: boolean | null) => void;
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

// The badge used to say "LIVE" regardless of where the number actually came
// from, which conflated three different sources behind one word. Map the
// backend's `spot.source` (see get_live_spot_quote in _market_data.py) to a
// label that tells the truth: "timescale" is recently-recorded playback,
// not the broker tape; "openalgo" is the only genuinely-live path;
// "parquet_fallback" is from the replay bundle (the chart is showing
// recent intraday bars because the recorder's hot tier is empty — the
// recorder or Timescale writer has stalled).
function modeBadge(
  isReplayArmed: boolean,
  source: string | undefined,
  sessionOpen: boolean | null,
): { label: string; live: boolean } {
  if (isReplayArmed) return { label: "REPLAY", live: false };
  if (source === "openalgo") return { label: "BROKER · LIVE", live: true };
  if (source === "timescale") return { label: "RECENT · RECORDED", live: false };
  if (source === "parquet_fallback") return { label: "RECENT · FALLBACK", live: false };
  if (sessionOpen === false) return { label: "MARKET CLOSED", live: false };
  return { label: "LIVE", live: true };
}

export function SimulatorLiveIndexPanel({
  symbol,
  exchange = "NSE_INDEX",
  isRecordingActive = false,
  isReplayArmed = false,
  height = 200,
  onSessionOpenChange,
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
  const [sessionOpen, setSessionOpen] = useState<boolean | null>(null);
  // The backend's real reason for empty replay ticks/spot (not armed, no
  // bars in the window, an exception) — preferred over the guessed
  // messages below so the empty state tells the truth instead of a plausible
  // but possibly wrong guess.
  const [emptyReason, setEmptyReason] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const { dark } = useDarkMode();

  // Poll cadence: 2 s while recording, 5 s otherwise.
  const pollMs = isRecordingActive ? 2000 : 5000;
  const prevCloseRef = useRef<number | null>(null);

  // Reset chart when symbol changes, or when switching into/out of replay
  // mode (avoids stale live/replay data bleeding into the other view while
  // the next poll is in flight).
  const symbolKey = `${symbol}:${exchange}`;

  useEffect(() => {
    setTicks([]);
    setSpot(null);
    setError(null);
    setLoading(true);
    setSessionOpen(null);
    setEmptyReason(null);
    prevCloseRef.current = null;
    if (seriesRef.current) {
      seriesRef.current.setData([]);
    }
  }, [symbolKey, isReplayArmed]);

  // Fetch loop.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const [ticksRes, spotRes] = await Promise.all([
          api.getHubMarketDataTicks({
            symbol,
            exchange,
            // In replay mode the user can seek anywhere in the trading
            // session (09:15-15:30 IST, 375 minutes) and expects the chart
            // to keep showing everything from session open up to the new
            // sim clock position. A fixed 240-minute lookback would silently
            // drop the earlier candles once sim_now is more than 4 hours
            // past open, since every poll fully replaces the chart's
            // dataset with just this window. Live mode keeps the tighter
            // rolling window since it isn't seekable.
            since_minutes: isReplayArmed ? 400 : 240,
            limit: 500,
            replay: isReplayArmed,
          }),
          api.getHubMarketDataSpot({ symbol, exchange, replay: isReplayArmed }),
        ]);
        if (cancelled) return;
        if (!isReplayArmed) {
          const nextSessionOpen = spotRes.session_open ?? null;
          setSessionOpen(nextSessionOpen);
          onSessionOpenChange?.(nextSessionOpen);
        }
        let lastTickPrice: number | null = null;
        if (ticksRes.status === "ok") {
          const next: Tick[] = ticksRes.ticks.map((t) => ({
            ts: t.ts,
            price: t.price,
            volume: t.volume ?? null,
            open: t.open ?? null,
          }));
          setTicks(next);
          if (next.length) lastTickPrice = next[next.length - 1].price;
          if (next.length && prevCloseRef.current == null && next[0].open != null) {
            prevCloseRef.current = next[0].open ?? null;
          }
          setEmptyReason(next.length === 0 ? ticksRes.error ?? null : null);
        }
        if (spotRes.status === "ok" && spotRes.spot) {
          // In replay mode the ticks and spot requests are two independent
          // calls that each read the simulator's sim clock — if a replay day
          // rolls over between the two, they can land on different days and
          // report wildly different prices (e.g. LTP from the new day, chart
          // still showing the old day's last bars). Prefer the ticks
          // response's own last price when we have one: it's exactly what
          // the chart just rendered, so the header number can never disagree
          // with what's plotted next to it.
          setSpot({
            ltp: isReplayArmed && lastTickPrice != null ? lastTickPrice : spotRes.spot.ltp,
            prev_close: spotRes.spot.prev_close ?? null,
            source: spotRes.spot.source,
            as_of: spotRes.spot.as_of ?? null,
          });
          if (spotRes.spot.prev_close != null && prevCloseRef.current == null) {
            prevCloseRef.current = spotRes.spot.prev_close;
          }
        } else if (isReplayArmed && lastTickPrice != null) {
          // Spot failed/empty but ticks succeeded — still show a value
          // consistent with the chart instead of leaving the header blank.
          setSpot((prev) => ({
            ltp: lastTickPrice as number,
            prev_close: prev?.prev_close ?? null,
            source: "simulator",
            as_of: null,
          }));
        } else if (isReplayArmed && lastTickPrice == null && spotRes.error) {
          // Neither call returned data — prefer the spot error over the
          // ticks one when ticks didn't give a reason of its own.
          setEmptyReason((prev) => prev ?? spotRes.error ?? null);
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
  }, [symbolKey, pollMs, isReplayArmed]);

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
    // Compute the numeric time first, then sort/dedupe on that — sorting on
    // the raw ISO string is unsafe because bars and the live simulator tick
    // can use different timestamp formats (naive vs tz-aware, whole-second
    // vs sub-second), so string order doesn't always match time order. At
    // higher simulator speeds the live tick's sub-second drift grows large
    // enough to violate ascending order and crash setData().
    const mapped = ticks
      .filter((t) => Number.isFinite(t.price))
      .map((t) => {
        const ms = Date.parse(t.ts);
        // Lightweight Charts always renders a numeric `Time` on its axis as
        // UTC clock time — it has no timezone option. `t.ts` is IST (NSE's
        // trading hours), so the *true* UTC epoch displays 5:30 earlier
        // than the actual IST wall clock (09:15 IST would show as 03:45).
        // Shift the instant forward by IST's UTC offset before truncating
        // to seconds, so the axis's "UTC" label happens to read the real
        // IST time instead.
        const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
        return { time: Math.floor((ms + IST_OFFSET_MS) / 1000) as Time, value: t.price };
      })
      .filter((p) => Number.isFinite(p.time));
    mapped.sort((a, b) => (a.time as number) - (b.time as number));
    // Lightweight Charts requires strictly ascending, unique times — two
    // ticks landing in the same second (sub-second polling) would tie.
    // Keep the latest value for each timestamp.
    const data: { time: Time; value: number }[] = [];
    for (const p of mapped) {
      const last = data[data.length - 1];
      if (last && last.time === p.time) {
        last.value = p.value;
      } else {
        data.push({ time: p.time, value: p.value });
      }
    }
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
  const badge = modeBadge(isReplayArmed, spot?.source, sessionOpen);

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          <span
            className={
              isReplayArmed
                ? "inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-700 dark:text-amber-300"
                : !badge.live
                ? "inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5"
                : "inline-flex items-center gap-1"
            }
            data-testid="live-spot-mode"
            title={
              isReplayArmed
                ? "Replaying a recorded session through the simulator clock."
                : badge.live
                ? "Live quote from the broker (OpenAlgo)."
                : sessionOpen === false
                ? "The market is closed right now — no live tape to show."
                : "Recently recorded ticks from TimescaleDB, not a live broker tape."
            }
          >
            {symbol}
            <span>·</span>
            <span>{badge.label}</span>
          </span>
          {isRecordingActive && !isReplayArmed && (
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-red-500 align-middle" />
          )}
          {isReplayArmed && (
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-500 align-middle" />
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
          {isReplayArmed
            ? emptyReason ??
              "No replay ticks at the current sim clock — try a different speed or check the replay day."
            : sessionOpen === false
            ? "Market is closed right now — arm a replay day below to see it move."
            : "No live ticks — start a recording or check TimescaleDB."}
        </p>
      )}
      <div ref={containerRef} className="mt-3 w-full" style={{ height }} />
    </div>
  );
}

export default SimulatorLiveIndexPanel;
