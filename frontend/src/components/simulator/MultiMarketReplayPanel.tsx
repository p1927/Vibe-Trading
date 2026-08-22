import { useCallback, useEffect, useState } from "react";
import { Pause, Play, Square, Timer } from "lucide-react";
import {
  api,
  type MarketRegistryEntry,
  type MultiMarketQuote,
  type MultiMarketStatusResponse,
} from "@/lib/api";
import { COUNTRY_LABELS } from "./GlobalMarketsPanel";

const ALL_MARKETS = ["IN", "US", "CN", "JP", "RU", "ME", "LATAM"];

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

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-muted-foreground">
        Arm several markets on one UTC clock to compare them at (approximately) the same
        instant. Backed by live-forward tick recordings plus backfilled daily closes only —
        there's no deep historical intraday archive for non-India markets yet, so a seek or
        quote lookup outside that window returns a clear error rather than a fake price.
      </p>

      {error ? <p className="text-[11px] text-destructive">{error}</p> : null}

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
                  {quote.stale ? " (stale — held over from the last known tick)" : ""} ·{" "}
                  {quote.source}
                </span>
              </p>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
