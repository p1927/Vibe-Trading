import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Pause, Play, Square, Timer } from "lucide-react";
import {
  api,
  ApiError,
  type MarketRegistryEntry,
  type MultiMarketQuote,
  type MultiMarketStatusResponse,
  type SchedulerRegistryEntry,
} from "@/lib/api";
import { COUNTRY_LABELS } from "./GlobalMarketsPanel";

const ALL_MARKETS = ["IN", "US", "CN", "JP", "RU", "ME", "LATAM", "EU"];

// Recorder-category polling cadence for the "Recording" sub-section below —
// matches Scheduled.tsx's own POLL_MS for the same registry endpoint.
const RECORDING_POLL_MS = 15_000;

// stock_simulator's `register_recorder_scheduler(name, ...)` names ("in",
// "us", "cn", "jp", "ru", "me", "latam", "eu", "in_economy" — see the
// recorder/*_recorder.py modules) mostly match `COUNTRY_LABELS`' keys
// uppercased, except "in_economy", which has no market tab of its own.
function marketLabelForRecorder(recorderName: string): string {
  if (recorderName === "in_economy") return "India — Economy";
  return COUNTRY_LABELS[recorderName.toUpperCase()] ?? recorderName;
}

function recorderSortKey(recorderName: string): number {
  const idx = ALL_MARKETS.indexOf(recorderName.toUpperCase());
  return idx === -1 ? ALL_MARKETS.length : idx;
}

/** Cross-market simultaneous replay — one UTC master clock watching several markets at once,
 * fronting `stock_simulator`'s `/multi_market/*` routes. Real data behind this today is
 * live-forward tick history plus backfilled daily closes only (see
 * `multi_market_replay.py`'s module docstring) — arbitrary historical intraday scrubbing for
 * non-India markets isn't available yet, so `seek`/quote lookups outside that window 400 with a
 * clear "no tick data" message rather than fabricating a quote. */
export function MultiMarketReplayPanel() {
  const [registry, setRegistry] = useState<MarketRegistryEntry[]>([]);
  const [selected, setSelected] = useState<string[]>(["IN", "US"]);
  const [speed, setSpeed] = useState(1);
  const [status, setStatus] = useState<MultiMarketStatusResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [quoteMarket, setQuoteMarket] = useState("");
  const [quoteSymbol, setQuoteSymbol] = useState("");
  const [quote, setQuote] = useState<MultiMarketQuote | null>(null);
  const [quoteError, setQuoteError] = useState<string | null>(null);

  // "Recording" sub-section — independent of the arm/replay-clock state
  // above: these are stock_simulator's always-on recorder-category
  // schedulers (live capture loops), pausable/resumable per (recorder,
  // category) via the scheduler-registry endpoint, with the last live
  // pause/resume choice persisted as that category's default across a
  // recorder restart. See
  // .claude/backlog/items/2026-09-03-recorder-pause-resume-multi-market-ui.md.
  const [recordingOpen, setRecordingOpen] = useState(true);
  const [recordingEntries, setRecordingEntries] = useState<SchedulerRegistryEntry[]>([]);
  const [recordingError, setRecordingError] = useState<string | null>(null);
  const [recordingBusyId, setRecordingBusyId] = useState<string | null>(null);
  const recordingSeq = useRef(0);

  const refreshRecording = useCallback(async () => {
    const seq = ++recordingSeq.current;
    try {
      const res = await api.listSchedulerRegistry();
      if (seq !== recordingSeq.current) return;
      setRecordingEntries(
        (res.entries ?? []).filter(
          (entry) => entry.source === "stock_simulator" && entry.section.startsWith("recorder:"),
        ),
      );
      setRecordingError(null);
    } catch (err) {
      if (seq !== recordingSeq.current) return;
      setRecordingError(err instanceof ApiError ? err.message : "Failed to load recording status");
    }
  }, []);

  useEffect(() => {
    void refreshRecording();
    const interval = window.setInterval(() => void refreshRecording(), RECORDING_POLL_MS);
    return () => window.clearInterval(interval);
  }, [refreshRecording]);

  const toggleRecording = async (entry: SchedulerRegistryEntry) => {
    setRecordingBusyId(entry.id);
    setRecordingError(null);
    try {
      if (entry.enabled) {
        await api.pauseStockSimSchedulerEntry(entry.id);
      } else {
        await api.resumeStockSimSchedulerEntry(entry.id);
      }
      await refreshRecording();
    } catch (err) {
      setRecordingError(err instanceof ApiError ? err.message : "Failed to change recording state");
    } finally {
      setRecordingBusyId(null);
    }
  };

  useEffect(() => {
    api.getMarketRegistry().then((res) => setRegistry(res.markets ?? [])).catch(() => {});
  }, []);

  const refreshStatus = useCallback(() => {
    api
      .getMultiMarketStatus()
      .then(setStatus)
      .catch(() => {
        // No session armed (or it was torn down elsewhere) — stop polling stale state.
        setStatus(null);
      });
  }, []);

  useEffect(() => {
    if (!status) return;
    const interval = window.setInterval(refreshStatus, 5_000);
    return () => window.clearInterval(interval);
  }, [status, refreshStatus]);

  const toggleMarket = (code: string) => {
    setSelected((prev) => (prev.includes(code) ? prev.filter((m) => m !== code) : [...prev, code]));
  };

  const arm = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.armMultiMarketReplay({ markets: selected, speed });
      setStatus(res);
      setQuoteMarket(selected[0] ?? "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to arm multi-market replay");
    } finally {
      setBusy(false);
    }
  };

  const pause = async () => {
    setBusy(true);
    try {
      setStatus(await api.pauseMultiMarketReplay());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to pause");
    } finally {
      setBusy(false);
    }
  };

  const resume = async () => {
    setBusy(true);
    try {
      setStatus(await api.resumeMultiMarketReplay());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resume");
    } finally {
      setBusy(false);
    }
  };

  const applySpeed = async (next: number) => {
    setSpeed(next);
    if (!status) return;
    setBusy(true);
    try {
      setStatus(await api.setMultiMarketReplaySpeed(next));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to change speed");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.stopMultiMarketReplay();
      setStatus(null);
      setQuote(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop");
    } finally {
      setBusy(false);
    }
  };

  const fetchQuote = async () => {
    if (!quoteMarket || !quoteSymbol) return;
    setQuoteError(null);
    setQuote(null);
    try {
      const res = await api.getMultiMarketQuote({
        market: quoteMarket,
        symbol: quoteSymbol,
        exchange: `${quoteMarket}_INDEX`,
      });
      setQuote(res.data);
    } catch (err) {
      setQuoteError(err instanceof Error ? err.message : "No quote available for that window");
    }
  };

  const quoteMarketIndices = registry.find((m) => m.code === quoteMarket)?.indices ?? [];

  const recordingGroups = Object.entries(
    recordingEntries.reduce<Record<string, SchedulerRegistryEntry[]>>((groups, entry) => {
      const recorderName = entry.section.slice("recorder:".length);
      (groups[recorderName] ??= []).push(entry);
      return groups;
    }, {}),
  ).sort(([a], [b]) => recorderSortKey(a) - recorderSortKey(b) || a.localeCompare(b));

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-muted-foreground">
        Arm several markets on one UTC clock to compare them at (approximately) the same
        instant. Backed by live-forward tick recordings plus backfilled daily closes only —
        there's no deep historical intraday archive for non-India markets yet, so a seek or
        quote lookup outside that window returns a clear error rather than a fake price.
      </p>

      {error ? <p className="text-[11px] text-destructive">{error}</p> : null}

      <div className="rounded-lg border bg-background/60">
        <button
          type="button"
          onClick={() => setRecordingOpen((prev) => !prev)}
          aria-expanded={recordingOpen}
          className="flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left text-xs font-medium"
        >
          <span className="flex items-center gap-1.5">
            {recordingOpen ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            )}
            Recording
          </span>
          <span className="text-[11px] font-normal text-muted-foreground">
            {recordingEntries.filter((e) => e.enabled).length}/{recordingEntries.length} active
          </span>
        </button>
        {recordingOpen ? (
          <div className="space-y-3 border-t px-2.5 py-2.5">
            <p className="text-[11px] text-muted-foreground">
              Per-market, per-index on/off for the always-on background recorders that feed this
              replay data — independent of the arm/pause controls below, which only affect an
              already-armed replay session's clock. Toggling here pauses or resumes that
              recorder category's live capture loop immediately and is remembered as its default,
              so it stays off (or on) across a recorder restart.
            </p>
            {recordingError ? <p className="text-[11px] text-destructive">{recordingError}</p> : null}
            {recordingGroups.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">
                No recorder categories reported — stock_simulator may be unreachable.
              </p>
            ) : (
              <div className="space-y-2.5">
                {recordingGroups.map(([recorderName, entries]) => (
                  <div key={recorderName} className="rounded border bg-background/40 p-2">
                    <p className="mb-1.5 text-[11px] font-medium">
                      {marketLabelForRecorder(recorderName)}
                    </p>
                    <ul className="space-y-1">
                      {entries
                        .slice()
                        .sort((a, b) => a.section.localeCompare(b.section) || a.id.localeCompare(b.id))
                        .map((entry) => {
                          const category = entry.id.split(":").pop() ?? entry.label;
                          const defaultLabel =
                            entry.persisted_default_enabled === true
                              ? "default: on"
                              : entry.persisted_default_enabled === false
                                ? "default: off"
                                : "default: unset";
                          const canToggle = entry.controls.pause || entry.controls.resume;
                          return (
                            <li
                              key={entry.id}
                              className="flex flex-wrap items-center justify-between gap-2 text-[11px]"
                            >
                              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                                <span className="font-mono">{category}</span>
                                <span
                                  className={
                                    entry.enabled
                                      ? "rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-emerald-600"
                                      : "rounded-full bg-muted px-1.5 py-0.5 text-muted-foreground"
                                  }
                                >
                                  {entry.enabled ? "recording" : "off"}
                                </span>
                                <span className="text-muted-foreground">{defaultLabel}</span>
                              </div>
                              {canToggle ? (
                                <button
                                  type="button"
                                  disabled={recordingBusyId === entry.id}
                                  onClick={() => void toggleRecording(entry)}
                                  aria-label={
                                    entry.enabled
                                      ? `Pause recording for ${marketLabelForRecorder(recorderName)} ${category}`
                                      : `Resume recording for ${marketLabelForRecorder(recorderName)} ${category}`
                                  }
                                  className="inline-flex h-6 items-center gap-1 rounded border px-1.5 text-[11px] hover:bg-accent disabled:opacity-50"
                                >
                                  {entry.enabled ? (
                                    <Pause className="h-3 w-3" aria-hidden />
                                  ) : (
                                    <Play className="h-3 w-3" aria-hidden />
                                  )}
                                  {entry.enabled ? "Pause" : "Resume"}
                                </button>
                              ) : (
                                <span className="text-muted-foreground">read-only</span>
                              )}
                            </li>
                          );
                        })}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : null}
      </div>

      {!status ? (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {ALL_MARKETS.map((code) => (
              <label
                key={code}
                className="flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(code)}
                  onChange={() => toggleMarket(code)}
                  className="h-3.5 w-3.5"
                />
                {COUNTRY_LABELS[code] ?? code}
              </label>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Speed
              <input
                type="number"
                min={0}
                step={0.5}
                value={speed}
                onChange={(e) => setSpeed(Math.max(0, Number(e.target.value) || 0))}
                className="w-16 rounded border bg-background px-1.5 py-1 text-xs"
              />
              ×
            </label>
            <button
              type="button"
              onClick={arm}
              disabled={busy || selected.length === 0}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <Play className="h-3.5 w-3.5" />
              Arm {selected.length || ""} market{selected.length === 1 ? "" : "s"}
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-background/60 p-2.5 text-xs">
            <div className="flex items-center gap-2">
              <Timer className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="font-mono">{new Date(status.clock.sim_now_utc).toUTCString()}</span>
              <span className="text-muted-foreground">
                · {status.clock.paused ? "paused" : `${status.clock.speed}×`}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {status.clock.paused ? (
                <button
                  type="button"
                  onClick={resume}
                  disabled={busy}
                  className="inline-flex h-7 items-center gap-1 rounded border px-2 text-[11px] hover:bg-accent disabled:opacity-50"
                >
                  <Play className="h-3 w-3" />
                  Resume
                </button>
              ) : (
                <button
                  type="button"
                  onClick={pause}
                  disabled={busy}
                  className="inline-flex h-7 items-center gap-1 rounded border px-2 text-[11px] hover:bg-accent disabled:opacity-50"
                >
                  <Pause className="h-3 w-3" />
                  Pause
                </button>
              )}
              <label className="flex items-center gap-1 text-[11px] text-muted-foreground">
                Speed
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={speed}
                  onChange={(e) => applySpeed(Math.max(0, Number(e.target.value) || 0))}
                  className="w-14 rounded border bg-background px-1 py-0.5 text-[11px]"
                />
                ×
              </label>
              <button
                type="button"
                onClick={stop}
                disabled={busy}
                className="inline-flex h-7 items-center gap-1 rounded border border-red-500/40 px-2 text-[11px] text-red-600 hover:bg-red-500/10 disabled:opacity-50"
              >
                <Square className="h-3 w-3" />
                Stop
              </button>
            </div>
          </div>

          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full text-left text-[11px]">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-2.5 py-1.5 font-medium">Market</th>
                  <th className="px-2.5 py-1.5 font-medium">Local time</th>
                  <th className="px-2.5 py-1.5 font-medium">Timezone</th>
                  <th className="px-2.5 py-1.5 font-medium">Session</th>
                </tr>
              </thead>
              <tbody>
                {status.markets.map((code) => {
                  const m = status.market_status[code];
                  return (
                    <tr key={code} className="border-t">
                      <td className="px-2.5 py-1.5 font-medium">{COUNTRY_LABELS[code] ?? code}</td>
                      <td className="px-2.5 py-1.5 font-mono">
                        {m ? new Date(m.local_time).toLocaleString() : "—"}
                      </td>
                      <td className="px-2.5 py-1.5 text-muted-foreground">{m?.timezone ?? "—"}</td>
                      <td className="px-2.5 py-1.5">
                        {m?.session_open ? (
                          <span className="text-emerald-600">Open</span>
                        ) : (
                          <span className="text-muted-foreground">Closed</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="rounded-lg border bg-background/60 p-2.5">
            <p className="mb-2 text-[11px] font-medium">Quote lookup</p>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={quoteMarket}
                onChange={(e) => {
                  setQuoteMarket(e.target.value);
                  setQuoteSymbol("");
                  setQuote(null);
                }}
                className="rounded border bg-background px-1.5 py-1 text-xs"
              >
                <option value="">Market…</option>
                {status.markets.map((code) => (
                  <option key={code} value={code}>
                    {COUNTRY_LABELS[code] ?? code}
                  </option>
                ))}
              </select>
              <select
                value={quoteSymbol}
                onChange={(e) => setQuoteSymbol(e.target.value)}
                disabled={!quoteMarket}
                className="rounded border bg-background px-1.5 py-1 text-xs disabled:opacity-50"
              >
                <option value="">Symbol…</option>
                {quoteMarketIndices.map((idx) => (
                  <option key={idx} value={idx}>
                    {idx}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={fetchQuote}
                disabled={!quoteMarket || !quoteSymbol}
                className="inline-flex h-8 items-center rounded border px-2 text-[11px] hover:bg-accent disabled:opacity-50"
              >
                Get quote
              </button>
            </div>
            {quoteError ? <p className="mt-2 text-[11px] text-destructive">{quoteError}</p> : null}
            {quote ? (
              <p className="mt-2 text-xs">
                <span className="font-mono font-medium">{quote.price}</span>{" "}
                <span className="text-muted-foreground">
                  @ {new Date(quote.ts).toLocaleString()}
                  {quote.synthetic
                    ? " (simulated — no live tick data, interpolated open→close)"
                    : quote.stale
                      ? " (stale — held over from the last known tick)"
                      : ""}{" "}
                  · {quote.source}
                </span>
              </p>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
