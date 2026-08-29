import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Disc, ListOrdered, PlayCircle, Square, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  api,
  type ConstituentInfo,
  type PipelineLogEntry,
  type RecordingJobSnapshot,
  type RecordingResult,
  type ReplayCalendarDay,
} from "@/lib/api";
import { EconomyPanel } from "@/components/simulator/EconomyPanel";
import { COUNTRY_LABELS, GlobalMarketsPanel } from "@/components/simulator/GlobalMarketsPanel";
import { MarketCoveragePanel } from "@/components/simulator/MarketCoveragePanel";
import { MarketFactorCoveragePanel } from "@/components/simulator/MarketFactorCoveragePanel";
import { MarketRecordingPanel } from "@/components/simulator/MarketRecordingPanel";
import { MarketReplayPanel } from "@/components/simulator/MarketReplayPanel";
import { MultiMarketReplayPanel } from "@/components/simulator/MultiMarketReplayPanel";
import { SectorIndicesPanel } from "@/components/simulator/SectorIndicesPanel";
import { SimulatorInstrumentPicker } from "@/components/simulator/SimulatorInstrumentPicker";
import { SimulatorLiveIndexPanel } from "@/components/simulator/SimulatorLiveIndexPanel";
import { SimulatorOptionChainPanel } from "@/components/simulator/SimulatorOptionChainPanel";
import {
  SimulatorReplayCalendar,
  type ReplayRange,
} from "@/components/simulator/SimulatorReplayCalendar";
import { SimulatorReplayClock } from "@/components/simulator/SimulatorReplayClock";
import { SimulatorReplayDetailPanel } from "@/components/simulator/SimulatorReplayDetailPanel";
import { StockHistoryCoveragePanel } from "@/components/simulator/StockHistoryCoveragePanel";
import {
  RecordingConfigProvider,
  SimulatorRecordingFields,
} from "@/components/simulator/SimulatorRecordingFields";
import {
  DEFAULT_CATEGORY_INTERVALS,
  DEFAULT_EQUITY_INTERVALS,
  DEFAULT_HISTORICAL_CONFIG,
  DEFAULT_WS_THROTTLE_HZ,
  historicalIntervalKeyToApi,
  historicalLookbackKeyToDays,
  type HistoricalIntervalKey,
  type HistoricalLookbackKey,
} from "@/lib/intervalOptions";

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "SENSEX"];

// GIFT Nifty (NSE IX futures on Nifty 50) is chart-only, not part of `UNDERLYINGS`: it's always
// recorded independently (`gift_nifty_poller.py`, a plain page-scrape poll, not INDmoney's
// option-chain/depth path), so offering it in the "Record" instrument picker below would imply a
// toggle that doesn't do anything — the recorder's `underlyings` resolution step would just
// silently drop it (it isn't in INDmoney's tradable scrip master). Also its exchange is "NSEIX",
// not "NSE_INDEX" like the other three, so it can't just be appended to `UNDERLYINGS`.
const GIFT_NIFTY_SYMBOL = { symbol: "GIFTNIFTY", exchange: "NSEIX" };

function StatCard({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-xl border bg-card p-4 shadow-sm", className)}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function statusBadge(status: string | undefined, stoppedReason?: string | null) {
  const s = (status || "idle").toLowerCase();
  const styles: Record<string, string> = {
    queued: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
    waiting: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
    waiting_for_open: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
    running: "bg-blue-500/15 text-blue-800 dark:text-blue-200",
    done: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    error: "bg-red-500/15 text-red-800 dark:text-red-200",
    idle: "bg-muted text-muted-foreground",
  };
  const labels: Record<string, string> = {
    queued: "Starting…",
    waiting: "Waiting for Market Open",
    waiting_for_open: "Waiting for Market Open",
    running: "Recording",
    // "done" covers both a natural market-close stop and a manual Stop —
    // only label it "Market Closed" when that's actually why it ended
    // (``stopped_reason`` from the recorder's ``RecordingResult``);
    // otherwise say "Stopped" so a manual stop mid-session (market still
    // open) doesn't claim the market closed.
    done: stoppedReason === "market_closed" ? "Market Closed — Done" : "Stopped",
    error: "Error",
    idle: "Idle",
  };
  return (
    <span className={cn("rounded-full px-2.5 py-0.5 text-[11px] font-medium", styles[s] || styles.idle)}>
      {labels[s] || s}
    </span>
  );
}

function logLevelColor(level: string | undefined): string {
  const l = (level || "info").toLowerCase();
  if (l === "error") return "text-red-500";
  if (l === "warn" || l === "warning") return "text-amber-500";
  return "text-muted-foreground";
}

function ProgressBar({ pct }: { pct: number | null | undefined }) {
  const value = Math.max(0, Math.min(1, pct ?? 0));
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full bg-primary transition-[width] duration-500"
        style={{ width: `${(value * 100).toFixed(1)}%` }}
      />
    </div>
  );
}

export function Simulator() {
  // Which Global Markets tab is selected — drives which Record/Replay/Coverage section renders
  // below (India's own INDmoney-based recorder+replay for "IN", tick-recording+factor-coverage
  // for a country tab or "CURRENCY", nothing extra for "GLOBAL" since gold/oil/VIX/US10Y aren't
  // owned by one market).
  const [marketTab, setMarketTab] = useState<string>("IN");
  const [selected, setSelected] = useState<string[]>(UNDERLYINGS);
  const [selectedEquities, setSelectedEquities] = useState<string[]>([]);
  const [waitForOpen, setWaitForOpen] = useState(false);
  const [autoRecord, setAutoRecordState] = useState(false);
  const [autoRecordBusy, setAutoRecordBusy] = useState(false);
  // Per-category recording intervals (seconds) + WS LTP throttle (Hz).
  // Lifted from the disclosure so the page passes them into
  // api.startRecording rather than the now-removed poll_interval_s.
  const [categoryIntervals, setCategoryIntervals] = useState<Record<string, number>>({
    ...DEFAULT_CATEGORY_INTERVALS,
  });
  // Per-equity (NIFTY50) intervals — defaults to *off* per the plan so
  // the user opts in. Shared cadence across all selected equities.
  const [equityIntervals, setEquityIntervals] = useState<Record<string, number>>({
    ...DEFAULT_EQUITY_INTERVALS,
  });
  // Historical candle config. Pulled once per equity at session start.
  const [historicalConfig, setHistoricalConfigState] = useState<{
    interval: HistoricalIntervalKey;
    lookback_days: HistoricalLookbackKey;
  }>(DEFAULT_HISTORICAL_CONFIG);
  const setHistoricalConfig = useCallback(
    (
      next:
        | {
            interval: HistoricalIntervalKey;
            lookback_days: HistoricalLookbackKey;
          }
        | null,
    ) => {
      if (next === null) {
        setHistoricalConfigState(DEFAULT_HISTORICAL_CONFIG);
      } else {
        setHistoricalConfigState(next);
      }
    },
    [],
  );
  const [wsThrottleHz, setWsThrottleHz] = useState<number | null>(DEFAULT_WS_THROTTLE_HZ);
  const setCategoryInterval = useCallback((category: string, seconds: number) => {
    setCategoryIntervals((prev) => ({ ...prev, [category]: seconds }));
  }, []);
  const setEquityInterval = useCallback((category: string, seconds: number) => {
    setEquityIntervals((prev) => ({ ...prev, [category]: seconds }));
  }, []);
  const setWsThrottle = useCallback((hz: number | null) => {
    setWsThrottleHz(hz);
  }, []);
  const [job, setJob] = useState<RecordingJobSnapshot | null>(null);
  const [logs, setLogs] = useState<PipelineLogEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [calendarDays, setCalendarDays] = useState<ReplayCalendarDay[]>([]);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  // Distinct from calendarError: the token simply isn't set up yet, not a
  // fetch failure — no "Retry" button, since retrying can't fix a missing
  // env var.
  const [replayNotConfigured, setReplayNotConfigured] = useState<string | null>(null);
  const [replayRange, setReplayRange] = useState<ReplayRange | null>(null);
  const [armedRange, setArmedRange] = useState<ReplayRange | null>(null);
  const [replaySpeed, setReplaySpeed] = useState<number>(1);
  const [speedUpdating, setSpeedUpdating] = useState(false);
  const [replayLoop, setReplayLoop] = useState<boolean>(true);
  const [armingDay, setArmingDay] = useState<string | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  const [selectedReplayDay, setSelectedReplayDay] = useState<ReplayCalendarDay | null>(null);
  // Phase 9: option-chain drawer toggle.
  const [showChain, setShowChain] = useState(false);

  // Which symbol drives the live chart + option chain, independent of the
  // Record checkboxes — any of the three indices are always chartable, plus
  // the full NIFTY-50 constituent list (not just whatever's picked for
  // recording — the picker's selection only controls what gets *recorded*,
  // this controls what the chart *shows*). Bare NSE tickers like
  // "RELIANCE"; the market-data endpoints accept exchange="NSE" for those,
  // same as "NSE_INDEX" for the indices.
  const [primarySymbol, setPrimarySymbol] = useState<{ symbol: string; exchange: string }>({
    symbol: "NIFTY",
    exchange: "NSE_INDEX",
  });
  const [nifty50, setNifty50] = useState<ConstituentInfo[]>([]);
  useEffect(() => {
    api
      .getRecordingConstituents()
      .then((res) => setNifty50(res.constituents || []))
      .catch(() => {});
  }, []);
  const chartableSymbols = useMemo(
    () => [
      ...UNDERLYINGS.map((u) => ({ symbol: u, exchange: "NSE_INDEX" })),
      GIFT_NIFTY_SYMBOL,
      ...nifty50.map((c) => ({ symbol: c.symbol, exchange: "NSE" })),
    ],
    [nifty50],
  );
  useEffect(() => {
    const stillValid = chartableSymbols.some(
      (s) => s.symbol === primarySymbol.symbol && s.exchange === primarySymbol.exchange,
    );
    if (!stillValid) setPrimarySymbol(chartableSymbols[0] ?? { symbol: "NIFTY", exchange: "NSE_INDEX" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartableSymbols]);

  // Whether the underlying's real trading session is open right now, as
  // reported by the live index panel's poll (null = unknown/check failed).
  // Drives the "market closed — replay the latest day" banner below.
  const [marketOpen, setMarketOpen] = useState<boolean | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  const isActive =
    job?.status === "queued" ||
    job?.status === "waiting_for_open" ||
    job?.status === "running";
  const isWaitingForOpen = job?.status === "waiting_for_open";
  const displayStatus = isWaitingForOpen ? "waiting_for_open" : job?.status;
  // Phase C: prefer the structured ``next_open_at`` field from the job
  // snapshot (set by ``set_waiting_for_open_at`` in recording_jobs.py)
  // over the legacy log-entry fallback. The fallback still works for
  // jobs persisted before Phase C, but new waits have an empty log
  // stream until the scheduled wake fires.
  const nextOpenAtLabel =
    job?.next_open_at ||
    (([...logs].reverse().find((entry) => entry?.stage === "waiting")
      ?.detail as { next_open_at?: string } | undefined)?.next_open_at) ||
    "—";

  const loadCalendar = useCallback(() => {
    setCalendarError(null);
    setReplayNotConfigured(null);
    api
      .getReplayCalendar()
      .then((res) => {
        setCalendarDays(res.days || []);
        if (res.configured === false) {
          setReplayNotConfigured(
            res.message || "Replay isn't configured on this OpenAlgo instance yet.",
          );
        }
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : "Failed to load calendar";
        setCalendarError(
          /failed to fetch|networkerror|load failed/i.test(message)
            ? "Can't reach the recording API to load the calendar."
            : message,
        );
      });
  }, []);

  const attachStream = useCallback((jobId: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    api
      .streamRecordingJob(
        jobId,
        {
          onLog: (entry) => setLogs((prev) => [...prev, entry]),
          onDone: (result: RecordingResult) => {
            setJob((prev) => (prev ? { ...prev, status: "done", result } : prev));
            loadCalendar();
          },
          onError: (message) => {
            setJob((prev) => (prev ? { ...prev, status: "error", error: message } : prev));
          },
        },
        controller.signal,
      )
      .catch(() => {});
  }, [loadCalendar]);

  // Reconciles this page's client-held `armedRange` against the server's
  // actual replay state. Needed both at mount (a replay armed in a prior
  // session, or by another tab, leaves STOCK_SIMULATOR_MODE=replay set on
  // the server even after this page reloads) AND periodically thereafter —
  // the standalone stock_simulator service has no persistence for an armed
  // replay across its own restarts, so a service restart while this tab is
  // open silently reverts the server to unarmed/auto-fallback while this
  // page's cached `armedRange` — and everything derived from it (the REPLAY
  // badge, the chart's poll mode, the Replay section's Running display) —
  // would otherwise keep showing the stale pre-restart state indefinitely.
  // Bidirectional: syncs to armed when the server says replay, and clears
  // local state when the server says it isn't (rather than only ever
  // setting, never clearing, as the old mount-only version did). Best-effort
  // — swallow errors (e.g. SIMULATOR_CONTROL_TOKEN not configured) since
  // this is a background reconciliation, not a user-initiated action.
  const reconcileReplayStatus = useCallback(() => {
    api
      .getReplayStatus()
      .then((res) => {
        const replay = (res.replay ?? null) as Record<string, unknown> | null;
        if (!replay || replay.mode !== "replay") {
          setArmedRange((prev) => (prev ? null : prev));
          return;
        }
        const clock = (replay.clock || {}) as Record<string, unknown>;
        const replayDate = typeof clock.replay_date === "string" ? clock.replay_date : null;
        if (!replayDate) return;
        // The status endpoint doesn't cleanly distinguish an explicit
        // start+end range arm from the "last N trading days" week-mode
        // default, so reconcile conservatively as a single-day arm at the
        // current replay date rather than guessing a wider range.
        setArmedRange((prev) =>
          prev && prev.start === replayDate && prev.end === replayDate
            ? prev
            : { start: replayDate, end: replayDate },
        );
        setReplayRange((prev) =>
          prev && prev.start === replayDate && prev.end === replayDate
            ? prev
            : { start: replayDate, end: replayDate },
        );
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadCalendar();
    api
      .getActiveRecording()
      .then((res) => {
        if (res.job) {
          setJob(res.job);
          setLogs(res.job.logs || []);
          attachStream(res.job.job_id);
        }
      })
      .catch(() => {});
    api
      .getAutoRecord()
      .then((res) => setAutoRecordState(res.enabled))
      .catch(() => {});
    reconcileReplayStatus();
    return () => abortRef.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const interval = window.setInterval(reconcileReplayStatus, 20_000);
    return () => window.clearInterval(interval);
  }, [reconcileReplayStatus]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [logs]);

  useEffect(() => {
    if (!isActive) return;
    const interval = window.setInterval(() => {
      if (!job) return;
      api
        .getRecordingJob(job.job_id)
        .then((res) => {
          if (res.job) setJob(res.job);
        })
        .catch(() => {});
    }, 5000);
    return () => window.clearInterval(interval);
  }, [isActive, job?.job_id]);

  // Auto Record re-arms a fresh job server-side (see
  // recording_wait_scheduler.py's poller) once the previous day's session
  // ends — no button press involved. The 5s job-refresh loop above only
  // re-fetches the *known* job id, so while auto-record is on and nothing
  // is locally tracked as active, poll for a newly-armed job to pick up.
  useEffect(() => {
    if (!autoRecord || isActive) return;
    const discover = () => {
      api
        .getActiveRecording()
        .then((res) => {
          if (res.job && res.job.job_id !== job?.job_id) {
            setJob(res.job);
            setLogs(res.job.logs || []);
            attachStream(res.job.job_id);
          }
        })
        .catch(() => {});
    };
    discover();
    const interval = window.setInterval(discover, 20000);
    return () => window.clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRecord, isActive]);

  const setAutoRecord = async (enabled: boolean) => {
    setAutoRecordBusy(true);
    setError(null);
    try {
      const res = await api.setAutoRecord({
        enabled,
        underlyings: [...selected, ...selectedEquities],
        equities: selectedEquities,
        category_intervals: categoryIntervals,
        equity_intervals: equityIntervals,
        ws_throttle_hz: wsThrottleHz,
        historical_config: historicalConfig
          ? {
              interval: historicalIntervalKeyToApi(historicalConfig.interval),
              lookback_days: historicalLookbackKeyToDays(historicalConfig.lookback_days),
            }
          : null,
      });
      setAutoRecordState(res.enabled);
      if (res.active_job_id && res.active_job_id !== job?.job_id) {
        const snap = await api.getRecordingJob(res.active_job_id);
        if (snap.job) {
          setJob(snap.job);
          setLogs(snap.job.logs || []);
          attachStream(snap.job.job_id);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update auto-record");
    } finally {
      setAutoRecordBusy(false);
    }
  };

  const startRecording = async () => {
    setBusy(true);
    setError(null);
    setLogs([]);
    try {
      const res = await api.startRecording({
        underlyings: [...selected, ...selectedEquities],
        equities: selectedEquities,
        category_intervals: categoryIntervals,
        equity_intervals: equityIntervals,
        ws_throttle_hz: wsThrottleHz,
        historical_config: historicalConfig
          ? {
              interval: historicalIntervalKeyToApi(historicalConfig.interval),
              lookback_days: historicalLookbackKeyToDays(historicalConfig.lookback_days),
            }
          : null,
        wait_for_open: waitForOpen,
      });
      const snap = await api.getRecordingJob(res.job_id);
      setJob(snap.job ?? { job_id: res.job_id, status: res.job_status });
      attachStream(res.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start recording");
    } finally {
      setBusy(false);
    }
  };

  const stopRecording = async () => {
    if (!job) return;
    // Optimistic flip so the UI visibly reacts within one frame —
    // ``complete_job`` on the server fires within ~1s of Stop and the
    // SSE ``done`` event will catch up shortly; this just removes the
    // window where the user wonders whether the click was received.
    // The recorder worker is now detached from the end-of-session
    // supplement, so the SSE ``done`` event lands essentially
    // immediately in practice — this is belt-and-braces UX.
    const previousStatus = job.status;
    setJob((prev) => (prev ? { ...prev, status: "done" } : prev));
    setBusy(true);
    try {
      await api.stopRecording(job.job_id);
    } catch (err) {
      // POST failed (network or 409 because the job already finished).
      // Roll back to the pre-click status — the SSE stream will
      // overwrite it with the real state on the next tick either way.
      setJob((prev) => (prev ? { ...prev, status: previousStatus } : prev));
      setError(err instanceof Error ? err.message : "Failed to stop recording");
    } finally {
      setBusy(false);
    }
  };

  const armReplay = async (range: ReplayRange) => {
    setArmingDay(range.start);
    setReplayError(null);
    try {
      const res = await api.startReplay(range.start, {
        speed: replaySpeed,
        loop: replayLoop,
        end_date: range.end !== range.start ? range.end : undefined,
      });
      const replay = (res.replay ?? null) as Record<string, unknown> | null;
      const clock = (replay?.clock || {}) as Record<string, unknown>;
      const armedStart =
        typeof clock.replay_date === "string" ? clock.replay_date : range.start;
      const armed: ReplayRange = { start: armedStart, end: range.end };
      setReplayRange(armed);
      setArmedRange(armed);
    } catch (err) {
      setReplayError(err instanceof Error ? err.message : "Failed to arm replay");
    } finally {
      setArmingDay(null);
    }
  };

  const handleSimulatorStopped = useCallback(() => {
    setArmedRange(null);
  }, []);

  const handleSpeedChange = async (value: number) => {
    setReplaySpeed(value);
    if (!armedRange) return;
    // Live rate change on the already-armed clock (SimClock.set_speed) — no
    // re-arm, so it doesn't disturb the running replay or its sim_now.
    setSpeedUpdating(true);
    try {
      await api.setReplaySpeed(value);
    } catch (err) {
      setReplayError(err instanceof Error ? err.message : "Failed to change replay speed");
    } finally {
      setSpeedUpdating(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Stock Simulator</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Record a live trading day from INDmoney (real option-chain greeks/OI/IV, market depth,
          index ticks), then replay that day through the simulator.
        </p>
      </div>

      <StatCard title="Global markets">
        <GlobalMarketsPanel activeTab={marketTab} onTabChange={setMarketTab} />
      </StatCard>

      {marketTab === "IN" ? (
        <>
        {/* Primary chart symbol: any index, or any NIFTY-50 constituent —
            independent of what's checked for recording below. This only
            controls what the chart/chain show, not what gets recorded. */}
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Chart symbol
            <select
              value={`${primarySymbol.exchange}:${primarySymbol.symbol}`}
              onChange={(e) => {
                const [exchange, symbol] = e.target.value.split(":");
                setPrimarySymbol({ symbol, exchange });
              }}
              className="max-w-[220px] rounded border bg-background px-1.5 py-1 text-xs"
              data-testid="simulator-primary-symbol"
            >
              <optgroup label="Indices">
                {UNDERLYINGS.map((u) => (
                  <option key={u} value={`NSE_INDEX:${u}`}>
                    {u}
                  </option>
                ))}
                <option
                  key={GIFT_NIFTY_SYMBOL.symbol}
                  value={`${GIFT_NIFTY_SYMBOL.exchange}:${GIFT_NIFTY_SYMBOL.symbol}`}
                >
                  GIFT NIFTY
                </option>
              </optgroup>
              {nifty50.length > 0 && (
                <optgroup label="NIFTY 50 equities">
                  {[...nifty50]
                    .sort((a, b) => a.symbol.localeCompare(b.symbol))
                    .map((c) => (
                      <option key={c.symbol} value={`NSE:${c.symbol}`}>
                        {c.symbol} — {c.name}
                      </option>
                    ))}
                </optgroup>
              )}
            </select>
          </label>
        </div>

        {marketOpen === false && !armedRange && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
            <span>
              Market is closed right now.
              {calendarDays[0]
                ? ` Replay the most recent recorded session (${calendarDays[0].date}) to see it moving.`
                : " Record a full session (below) to enable replay."}
            </span>
            {calendarDays[0] ? (
              <button
                type="button"
                onClick={() => armReplay({ start: calendarDays[0].date, end: calendarDays[0].date })}
                disabled={Boolean(armingDay)}
                className="shrink-0 rounded border border-amber-500/40 px-2 py-0.5 font-medium hover:bg-amber-500/10 disabled:opacity-50"
                data-testid="replay-latest-day"
              >
                {armingDay ? "Arming…" : `Replay ${calendarDays[0].date}`}
              </button>
            ) : null}
          </div>
        )}

        {/* Phase 9: live index panel + option chain toggle. Follows the
            "Chart symbol" selector above. */}
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1 basis-full sm:basis-0">
            <SimulatorLiveIndexPanel
              symbol={primarySymbol.symbol}
              exchange={primarySymbol.exchange}
              isRecordingActive={isActive}
              isReplayArmed={Boolean(armedRange)}
              replaySpeed={replaySpeed}
              height={240}
              onSessionOpenChange={setMarketOpen}
            />
          </div>
          <button
            type="button"
            onClick={() => setShowChain(true)}
            className="inline-flex h-9 items-center gap-1.5 self-start rounded-lg border bg-background px-3 text-sm hover:bg-muted/50"
            title="Show live option chain"
            data-testid="open-option-chain"
          >
            <ListOrdered className="h-3.5 w-3.5" />
            Option Chain
          </button>
        </div>
        <SimulatorOptionChainPanel
          symbol={primarySymbol.symbol}
          exchange={primarySymbol.exchange}
          open={showChain}
          onClose={() => setShowChain(false)}
          recordingActive={isActive}
          isReplayArmed={Boolean(armedRange)}
          replaySpeed={replaySpeed}
        />

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        <StatCard title="Record">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <span title={isActive ? "Stop the recording to change what's being recorded" : undefined}>
                <SimulatorInstrumentPicker
                  indices={UNDERLYINGS}
                  selectedIndices={selected}
                  onChangeIndices={setSelected}
                  selectedEquities={selectedEquities}
                  onChangeEquities={setSelectedEquities}
                  disabled={isActive}
                />
              </span>
              <label
                className="flex items-center gap-1.5 text-sm text-muted-foreground"
                title={isActive ? "Stop the recording to change this" : undefined}
              >
                <input
                  type="checkbox"
                  checked={waitForOpen}
                  disabled={isActive}
                  onChange={(e) => setWaitForOpen(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-border"
                />
                Wait for market open
              </label>
              <label
                className="flex items-center gap-1.5 text-sm text-muted-foreground"
                title="Automatically start recording at market open and stop at close, every trading day — no need to press Start each morning. Uses whatever's picked above at the moment you turn this on."
              >
                <input
                  type="checkbox"
                  checked={autoRecord}
                  disabled={autoRecordBusy}
                  onChange={(e) => setAutoRecord(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-border"
                />
                Auto Record
              </label>
            </div>
            <div className="flex items-center gap-2">
              {autoRecord ? (
                <span
                  className="rounded-full bg-violet-500/15 px-2.5 py-0.5 text-[11px] font-medium text-violet-800 dark:text-violet-200"
                  title="Every trading day: starts at open, stops at close, re-arms for the next day automatically."
                >
                  Auto Record ON
                </span>
              ) : null}
              {statusBadge(displayStatus, job?.result?.stopped_reason)}
              {isWaitingForOpen ? (
                <span
                  className="text-[11px] text-amber-800 dark:text-amber-200"
                  title={`Next NSE open at ${nextOpenAtLabel}`}
                >
                  {nextOpenAtLabel === "—"
                    ? "Awaiting next NSE open"
                    : `Next open at ${nextOpenAtLabel.slice(11, 16)} IST`}
                </span>
              ) : null}
              {isActive ? (
                <button
                  type="button"
                  onClick={stopRecording}
                  disabled={busy}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-red-500/40 bg-background px-3 text-sm text-red-600 hover:bg-red-500/10 disabled:opacity-50"
                >
                  <Square className="h-3.5 w-3.5" />
                  Stop
                </button>
              ) : (
                <button
                  type="button"
                  onClick={startRecording}
                  disabled={busy || selected.length + selectedEquities.length === 0}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  <Disc className="h-3.5 w-3.5" />
                  Start Recording
                </button>
              )}
            </div>
          </div>

          <RecordingConfigProvider
            config={{
              categoryIntervals,
              equityIntervals,
              wsThrottleHz,
              historicalConfig,
              setCategoryInterval,
              setEquityInterval,
              setWsThrottle,
              setHistoricalConfig,
            }}
          >
            <SimulatorRecordingFields disabled={isActive} />
          </RecordingConfigProvider>
          {job ? (
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span>
                  {job.underlyings?.join(", ") || "—"} · session {job.session_date || "today"}
                </span>
                <span>{Math.round((job.session_pct_complete ?? 0) * 100)}% of trading day recorded</span>
              </div>
              <ProgressBar pct={job.session_pct_complete} />

              <details
                className="group rounded-lg border bg-background/60"
                data-testid="simulator-recording-logs"
                onToggle={(e) => {
                  // When the user expands the logs, jump them to the
                  // latest entry — the auto-scroll effect above only
                  // fires on log *changes*, not on panel reveal.
                  if ((e.currentTarget as HTMLDetailsElement).open) {
                    requestAnimationFrame(() => {
                      logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
                    });
                  }
                }}
              >
                <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-[11px] font-medium text-muted-foreground select-none hover:text-foreground [&::-webkit-details-marker]:hidden">
                  <span className="flex items-center gap-1.5">
                    <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                    Recording logs
                    <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                      {logs.length}
                    </span>
                  </span>
                  <span className="font-normal text-muted-foreground/70 group-open:hidden">
                    Click to expand
                  </span>
                </summary>
                <div
                  ref={logRef}
                  className="h-56 overflow-auto rounded-b-lg border-t bg-background/60 p-3 font-mono text-[11px] leading-relaxed"
                >
                  {logs.length === 0 ? (
                    <p className="text-muted-foreground">No log entries yet.</p>
                  ) : (
                    logs.map((entry, i) => (
                      <div key={i} className={cn("flex gap-2", logLevelColor(entry.level))}>
                        <span className="shrink-0 text-muted-foreground/60">
                          {entry.at ? new Date(entry.at).toLocaleTimeString() : ""}
                        </span>
                        <span>{entry.message}</span>
                      </div>
                    ))
                  )}
                </div>
              </details>

              {job.status === "error" && job.error ? (
                <p className="text-sm text-destructive">{job.error}</p>
              ) : null}
              {job.status === "done" && job.result ? (
                <p className="text-sm text-muted-foreground">
                  Stopped: {job.result.stopped_reason} · {job.result.cycles} cycles
                  {job.result.errors?.length ? ` · ${job.result.errors.length} errors` : ""}
                </p>
              ) : null}
            </div>
          ) : (
            <p className="mt-3 text-sm text-muted-foreground">
              No recording in progress. Recording stops automatically at market close (15:30 IST).
              {autoRecord
                ? " Auto Record is on — it will start on its own at the next market open."
                : ""}
            </p>
          )}
        </StatCard>

        <StatCard title="Replay">
          <div className="space-y-4">
            {replayNotConfigured ? (
              <div className="rounded-lg border border-muted-foreground/20 bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
                Replay not available — {replayNotConfigured}
              </div>
            ) : calendarError ? (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
                <span>{calendarError}</span>
                <button
                  type="button"
                  onClick={loadCalendar}
                  className="shrink-0 rounded border border-destructive/40 px-2 py-0.5 text-[10px] font-medium hover:bg-destructive/10"
                >
                  Retry
                </button>
              </div>
            ) : null}

            <SimulatorReplayCalendar
              days={calendarDays}
              range={replayRange}
              armedRange={armedRange}
              onRangeSelect={(range) => setReplayRange(range)}
              onDaySelect={setSelectedReplayDay}
              selectedDate={selectedReplayDay?.date ?? null}
            />

            <SimulatorReplayClock
              armedRange={armedRange}
              onStop={handleSimulatorStopped}
            />

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background/60 p-4">
              <div className="flex flex-wrap items-center gap-4 text-xs">
                <label className="flex items-center gap-1.5">
                  Speed
                  <select
                    value={replaySpeed}
                    onChange={(e) => handleSpeedChange(Number(e.target.value))}
                    disabled={speedUpdating}
                    className="rounded border bg-background px-1.5 py-0.5 text-xs"
                    data-testid="simulator-speed"
                  >
                    {[0.5, 1, 2, 5, 10, 25, 50].map((s) => (
                      <option key={s} value={s}>{s}×</option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={replayLoop}
                    onChange={(e) => setReplayLoop(e.target.checked)}
                    disabled={Boolean(armedRange)}
                    className="h-3.5 w-3.5 rounded border-border"
                    data-testid="simulator-loop"
                  />
                  {/* While armed, describe what's actually looping (armedRange),
                      not the pending calendar selection (replayRange) — the
                      calendar stays clickable while armed, so replayRange can
                      drift away from what this checkbox (disabled) controls. */}
                  Loop {(() => {
                    const active = armedRange ?? replayRange;
                    return active && active.start !== active.end ? "range" : "at 15:30";
                  })()}
                </label>
              </div>
              <button
                type="button"
                onClick={() => replayRange && armReplay(replayRange)}
                disabled={!replayRange || Boolean(armingDay) || Boolean(armedRange)}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                data-testid="simulator-arm"
              >
                <PlayCircle className="h-3.5 w-3.5" />
                {armingDay
                  ? "Arming…"
                  : armedRange
                  ? `Armed · ${armedRange.start}${armedRange.end !== armedRange.start ? ` → ${armedRange.end}` : ""}`
                  : replayRange
                  ? `Arm replay · ${replayRange.start}${replayRange.end !== replayRange.start ? ` → ${replayRange.end}` : ""}`
                  : "Select a date"}
              </button>
            </div>

            {replayError ? <p className="text-[11px] text-destructive">{replayError}</p> : null}
            <p className="text-[11px] text-muted-foreground">
              Configures the simulator's replay clock on the running OpenAlgo instance
              directly — no restart needed. OpenAlgo then serves quotes from this clock
              as if it were the live market; the chart in the option chain panel updates
              accordingly.
            </p>
          </div>
        </StatCard>

        <SimulatorReplayDetailPanel day={selectedReplayDay} onClose={() => setSelectedReplayDay(null)} />

        <StatCard title="Data coverage">
          <p className="mb-3 text-[11px] text-muted-foreground">
            Per-week bucket availability for the simulator. White cells = data
            missing; click a cell to backfill it via the registered writer.
          </p>
          <StockHistoryCoveragePanel includeOptional />
        </StatCard>

        <StatCard title="Sector Indices">
          <SectorIndicesPanel country="IN" label="India" />
        </StatCard>
        </>
      ) : marketTab === "CURRENCY" ? (
        <StatCard title="Recording">
          <MarketRecordingPanel kind="fx" label="Currencies" />
        </StatCard>
      ) : marketTab === "GLOBAL" ? (
        <p className="text-sm text-muted-foreground">
          Gold, oil, VIX, and US 10Y are EOD/refresh-only — no live spot, so they aren't
          tick-recordable. The 7 money-flow factors (DXY, MSCI EM, KOSPI, TAIEX, JCI, HYG, LQD)
          do have live spots and can be tick-recorded — check their boxes under the{" "}
          <button
            type="button"
            onClick={() => setMarketTab("CURRENCY")}
            className="underline underline-offset-2"
          >
            Currencies
          </button>{" "}
          tab's Recording panel. The cards above already read all of these live/EOD on demand.
        </p>
      ) : marketTab === "ECONOMY" ? (
        <StatCard title="Economy">
          <EconomyPanel />
        </StatCard>
      ) : marketTab === "MULTI" ? (
        <StatCard title="Multi-market replay">
          <MultiMarketReplayPanel />
        </StatCard>
      ) : (
        <>
          <StatCard title="Recording">
            <MarketRecordingPanel
              kind="index"
              country={marketTab}
              label={COUNTRY_LABELS[marketTab] ?? marketTab}
            />
          </StatCard>
          <StatCard title="Replay">
            <MarketReplayPanel country={marketTab} label={COUNTRY_LABELS[marketTab] ?? marketTab} />
          </StatCard>
          <StatCard title="Data coverage">
            <MarketCoveragePanel country={marketTab} />
          </StatCard>
          <StatCard title="Factor coverage">
            <MarketFactorCoveragePanel country={marketTab} />
          </StatCard>
          <StatCard title="Bucket coverage">
            <p className="mb-3 text-[11px] text-muted-foreground">
              Per-week `global_index_ohlcv`/`flow_of_funds`/`valuation`/`volatility`/
              `policy`/`economy` bucket availability for {COUNTRY_LABELS[marketTab] ?? marketTab},
              scoped from the same stock-history coverage grid India's tab uses. White cells =
              data missing; click a cell to backfill it via the registered writer.
            </p>
            <StockHistoryCoveragePanel
              key={marketTab}
              includeOptional
              countryFilter={marketTab}
            />
          </StatCard>
          <StatCard title="Sector Indices">
            <SectorIndicesPanel country={marketTab} label={COUNTRY_LABELS[marketTab] ?? marketTab} />
          </StatCard>
        </>
      )}
    </div>
  );
}
