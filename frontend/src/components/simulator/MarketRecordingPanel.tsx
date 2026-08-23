import { useCallback, useEffect, useState } from "react";
import { Disc, Square } from "lucide-react";
import { api, type TickRecordingJob } from "@/lib/api";

/** `kind="fx"` tick-recording pool — `tick_recorder._fx_symbols()`'s 6 USD-anchored pairs
 * (the backend default when `symbols` is omitted) plus the 7 cross-market money-flow/
 * risk-appetite factors added to `global_macro_store` (2026-08-23, "Nifty 50 prediction gaps"
 * pass — dollar strength, broad EM risk appetite, 3 Asian EM peer indices, credit-spread proxy).
 * Both groups are plain `global_macro_store` series names and record identically via
 * `tick_recorder._poll_fx` -> `StockHistory.live_macro_spot(series=symbol)`; the split below is
 * only for the checklist's grouping, not a backend distinction. Kept in sync by hand with
 * `GlobalMarketsPanel.tsx`'s `CURRENCY_FACTORS`/`GLOBAL_FACTORS` (money-flow subset) — no shared
 * import because that module also lists EOD-only factors (gold/oil/VIX/US10Y) that have no live
 * spot and so can't be tick-recorded here. */
const FX_DEFAULT_SYMBOLS: { key: string; name: string }[] = [
  { key: "usd_inr", name: "USD/INR" },
  { key: "usd_cny", name: "USD/CNY" },
  { key: "usd_jpy", name: "USD/JPY" },
  { key: "usd_rub", name: "USD/RUB" },
  { key: "usd_sar", name: "USD/SAR" },
  { key: "usd_brl", name: "USD/BRL" },
];

const FX_MONEY_FLOW_SYMBOLS: { key: string; name: string }[] = [
  { key: "dxy", name: "US Dollar Index (DXY)" },
  { key: "msci_em", name: "MSCI Emerging Markets (EEM)" },
  { key: "kospi", name: "Korea (KOSPI)" },
  { key: "taiex", name: "Taiwan (TAIEX)" },
  { key: "jci_indonesia", name: "Indonesia (JCI)" },
  { key: "hyg", name: "US High Yield Bond ETF (HYG)" },
  { key: "lqd", name: "US Investment Grade Bond ETF (LQD)" },
];

const FX_SYMBOL_OPTIONS = [...FX_DEFAULT_SYMBOLS, ...FX_MONEY_FLOW_SYMBOLS];

/** Start/stop UI for `stock_simulator`'s tick-recording jobs (append-only ticks into the
 * generic `market_ticks` table), scoped to one market tab. `kind="index"` records a country's
 * headline indices (market-hours gated); `kind="fx"` records USD-anchored currency pairs plus
 * the cross-market money-flow factors above (no gate, trades ~24/5). In-memory job state on the
 * simulator side — a simulator restart silently drops any running job, so this polls `/active`
 * rather than trusting local state alone. */
export function MarketRecordingPanel({
  kind,
  country,
  label,
}: {
  kind: "index" | "fx";
  country?: string;
  label: string;
}) {
  const [jobs, setJobs] = useState<TickRecordingJob[]>([]);
  const [intervalSeconds, setIntervalSeconds] = useState(30);
  // Only meaningful for kind="fx" — mirrors `tick_recorder._fx_symbols()`'s default (the 6
  // USD-anchored pairs) so unchecking nothing preserves today's default-start behavior; the 7
  // money-flow factors start unchecked since they're new and opt-in.
  const [selectedSymbols, setSelectedSymbols] = useState<Set<string>>(
    () => new Set(FX_DEFAULT_SYMBOLS.map((s) => s.key)),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleSymbol = (key: string) => {
    setSelectedSymbols((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const refresh = useCallback(() => {
    api
      .getActiveMarketTickRecordings()
      .then((res) => {
        const all = res.jobs ?? [];
        setJobs(all.filter((j) => j.kind === kind && (kind === "fx" || j.country === country)));
      })
      .catch(() => {});
  }, [kind, country]);

  useEffect(() => {
    refresh();
    const interval = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.startMarketTickRecording({
        kind,
        country: kind === "index" ? country : undefined,
        // Explicit for fx so the checklist (including any of the 7 money-flow factors) is
        // what actually gets recorded — omitting this would fall back to the backend's
        // implicit 6-currency-pair default and silently drop the checklist's selection.
        symbols: kind === "fx" ? Array.from(selectedSymbols) : undefined,
        interval_seconds: intervalSeconds,
      });
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start recording");
    } finally {
      setBusy(false);
    }
  };

  const stop = async (jobId: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.stopMarketTickRecording(jobId);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop recording");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {kind === "fx" ? (
        <div className="space-y-1.5">
          <p className="text-[11px] text-muted-foreground">Symbols to record</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {FX_SYMBOL_OPTIONS.map((s) => (
              <label key={s.key} className="flex items-center gap-1.5 text-xs">
                <input
                  type="checkbox"
                  checked={selectedSymbols.has(s.key)}
                  onChange={() => toggleSymbol(s.key)}
                  className="h-3.5 w-3.5"
                />
                {s.name}
              </label>
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          Poll every
          <input
            type="number"
            min={5}
            value={intervalSeconds}
            onChange={(e) => setIntervalSeconds(Math.max(5, Number(e.target.value) || 5))}
            className="w-16 rounded border bg-background px-1.5 py-1 text-xs"
          />
          seconds
        </label>
        <button
          type="button"
          onClick={start}
          disabled={busy || (kind === "fx" && selectedSymbols.size === 0)}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Disc className="h-3.5 w-3.5" />
          Start recording {label}
        </button>
      </div>

      {error ? <p className="text-[11px] text-destructive">{error}</p> : null}

      {jobs.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No {label} recording in progress. {kind === "index" ? "Only polls while the market is open." : "FX polls around the clock."}
        </p>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => (
            <div
              key={job.job_id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-background/60 p-2.5 text-[11px]"
            >
              <div className="min-w-0">
                <p className="font-medium text-foreground">
                  {job.symbols?.join(", ") || "—"} · every {job.interval_seconds}s
                </p>
                <p className="text-muted-foreground">
                  {job.polls} polls · {job.errors} errors
                  {job.last_error ? ` · ${job.last_error}` : ""}
                </p>
              </div>
              <button
                type="button"
                onClick={() => stop(job.job_id)}
                disabled={busy}
                className="inline-flex h-7 shrink-0 items-center gap-1 rounded border border-red-500/40 bg-background px-2 text-[11px] text-red-600 hover:bg-red-500/10 disabled:opacity-50"
              >
                <Square className="h-3 w-3" />
                Stop
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
