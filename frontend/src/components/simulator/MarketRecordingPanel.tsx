import { useCallback, useEffect, useState } from "react";
import { Disc, Square } from "lucide-react";
import { api, type GlobalMacroUiCard, type TickRecordingJob } from "@/lib/api";

/** `kind="fx"` tick-recording pool — every `factors/catalog.py`-registered `market="GLOBAL"`/
 * FX-pair factor with a real live-spot source, fetched from `api.getGlobalMacroUiCards()`
 * (the same registry-driven endpoint `GlobalMarketsPanel.tsx` already uses) instead of a
 * hand-maintained list — closes
 * .claude/backlog/items/2026-09-03-market-recording-panel-fx-list-not-registry-driven.md.
 * `card.live_spot_series !== null` is the tick-recordable filter (checked live 2026-09-03:
 * only `us_10y`/`vix_daily` have no live spot among the `global` cards — every other GLOBAL/
 * currency card does, including several the old hardcoded list never had:
 * gold/oil_brent_daily/oil_wti_daily/copper/natural_gas/baltic_dry_freight). The `currency` vs
 * `global` array split from the API response is reused directly for this checklist's own
 * two-group layout (USD-anchored pairs vs. cross-market money-flow factors) — same grouping the
 * old hardcoded `FX_DEFAULT_SYMBOLS`/`FX_MONEY_FLOW_SYMBOLS` split had, just sourced live now.
 * Both groups record identically via `tick_recorder._poll_fx` ->
 * `StockHistory.live_macro_spot(series=symbol)` — the split is a UI grouping choice only, not a
 * backend distinction, same as before this refactor. */
function liveSpotOnly(cards: GlobalMacroUiCard[]): { key: string; name: string }[] {
  return cards
    .filter((c) => c.live_spot_series !== null)
    .map((c) => ({ key: c.key, name: c.name }));
}

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
  const [fxDefaultSymbols, setFxDefaultSymbols] = useState<{ key: string; name: string }[]>([]);
  const [fxMoneyFlowSymbols, setFxMoneyFlowSymbols] = useState<{ key: string; name: string }[]>([]);
  // Only meaningful for kind="fx" — mirrors `tick_recorder._fx_symbols()`'s default (the
  // USD-anchored currency pairs) so unchecking nothing preserves today's default-start
  // behavior; the money-flow factors start unchecked since they're opt-in. Seeded once the
  // registry-driven card lists arrive below (empty at mount, unlike the old hardcoded-list
  // version — a checked/default set can't exist before the API response lands).
  const [selectedSymbols, setSelectedSymbols] = useState<Set<string>>(new Set());

  const toggleSymbol = (key: string) => {
    setSelectedSymbols((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  useEffect(() => {
    if (kind !== "fx") return;
    api
      .getGlobalMacroUiCards()
      .then((res) => {
        const currency = liveSpotOnly(res.data.currency);
        const moneyFlow = liveSpotOnly(res.data.global);
        setFxDefaultSymbols(currency);
        setFxMoneyFlowSymbols(moneyFlow);
        setSelectedSymbols(new Set(currency.map((s) => s.key)));
      })
      .catch(() => {});
  }, [kind]);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        // Explicit for fx so the checklist (including any selected money-flow factors) is
        // what actually gets recorded — omitting this would fall back to the backend's
        // implicit currency-pair-only default and silently drop the checklist's selection.
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
            {[...fxDefaultSymbols, ...fxMoneyFlowSymbols].map((s) => (
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
