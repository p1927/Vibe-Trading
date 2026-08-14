/**
 * SimulatorOptionChainPanel — Phase 9
 *
 * Portal-mounted drawer showing the live option chain for the selected
 * underlying. Polls /trade/hub/market-data/option-chain every 5 s.
 * Shows strikes + CE/PE LTP, OI, IV, delta. Pause when closed.
 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { X, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  symbol: string;
  exchange?: string;
  open: boolean;
  onClose: () => void;
  recordingActive?: boolean;
  strikeCount?: number;
}

interface StrikeRow {
  strike: number;
  ce?: {
    last_price?: number;
    oi?: number;
    iv?: number;
    delta?: number;
    volume?: number;
    top_bid_price?: number;
    top_ask_price?: number;
  } | null;
  pe?: {
    last_price?: number;
    oi?: number;
    iv?: number;
    delta?: number;
    volume?: number;
    top_bid_price?: number;
    top_ask_price?: number;
  } | null;
}

function fmt(v: number | undefined | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

function closestStrikeIndex(strikes: StrikeRow[], spot: number | null): number {
  if (spot == null || strikes.length === 0) return -1;
  let best = 0;
  let bestDiff = Infinity;
  strikes.forEach((row, i) => {
    const diff = Math.abs(row.strike - spot);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  });
  return best;
}

export function SimulatorOptionChainPanel({
  symbol,
  exchange = "NSE_INDEX",
  open,
  onClose,
  strikeCount = 10,
}: Props) {
  const [strikes, setStrikes] = useState<StrikeRow[]>([]);
  const [spot, setSpot] = useState<number | null>(null);
  const [prevClose, setPrevClose] = useState<number | null>(null);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchChain = async () => {
    setLoading(true);
    try {
      const [chainRes, spotRes] = await Promise.all([
        api.getHubMarketDataOptionChain({
          symbol,
          exchange,
          strike_count: strikeCount,
        }),
        api.getHubMarketDataSpot({ symbol, exchange }),
      ]);
      if (chainRes.status === "ok") {
        setStrikes((chainRes.strikes ?? []) as StrikeRow[]);
        setExpiry(chainRes.expiry_date ?? null);
        setSpot(chainRes.underlying_ltp ?? null);
      } else {
        setError(chainRes.error || "chain unavailable");
        setStrikes([]);
      }
      if (spotRes.status === "ok" && spotRes.spot) {
        setSpot(spotRes.spot.ltp);
        setPrevClose(spotRes.spot.prev_close ?? null);
      }
      if (chainRes.status === "ok") {
        setError(null);
        setLastUpdated(new Date());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "fetch failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    fetchChain();
    const handle = window.setInterval(fetchChain, 5000);
    return () => window.clearInterval(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, symbol, exchange, strikeCount]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/30"
      onClick={onClose}
      data-testid="option-chain-overlay"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="option-chain-title"
        className="flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl sm:max-w-xl lg:max-w-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <h2 id="option-chain-title" className="text-sm font-semibold">
              {symbol} Option Chain
            </h2>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
              <span>{expiry ?? "expiry —"}</span>
              <span className="text-border">·</span>
              <span className="font-medium text-foreground">LTP {fmt(spot)}</span>
              {prevClose != null && <span>prev close {fmt(prevClose)}</span>}
              {lastUpdated && (
                <span className="text-muted-foreground/70">
                  updated {lastUpdated.toLocaleTimeString([], { hour12: false })}
                </span>
              )}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={fetchChain}
              className="rounded-md p-1.5 hover:bg-muted"
              aria-label="Refresh chain"
              title="Refresh"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md p-1.5 hover:bg-muted"
              aria-label="Close option chain"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-auto">
          {error && (
            <div
              className="flex items-center justify-between gap-3 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs text-red-700 dark:text-red-400"
              data-testid="option-chain-error"
            >
              <span>{error}</span>
              <button
                type="button"
                onClick={fetchChain}
                className="shrink-0 rounded border border-red-500/40 px-2 py-0.5 text-[10px] font-medium hover:bg-red-500/10"
              >
                Retry
              </button>
            </div>
          )}
          {strikes.length === 0 && !loading && !error && (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">
              No strikes returned for this underlying.
            </p>
          )}
          {strikes.length === 0 && !error && (
            <p className="px-4 py-6 text-center text-xs text-muted-foreground">Loading…</p>
          )}
          {strikes.length > 0 && (
            <table className="w-full text-[11px] tabular-nums">
              <thead className="sticky top-0 z-10 bg-background/95 backdrop-blur">
                <tr>
                  <th className="bg-emerald-500/10 px-2 py-1 text-right text-emerald-700 dark:text-emerald-400" colSpan={4}>
                    CALLS
                  </th>
                  <th className="border-x bg-muted px-2 py-1 text-center">Strike</th>
                  <th className="bg-red-500/10 px-2 py-1 text-left text-red-700 dark:text-red-400" colSpan={4}>
                    PUTS
                  </th>
                </tr>
                <tr className="border-b text-[10px] text-muted-foreground">
                  <th className="px-2 py-1 text-right font-medium">LTP</th>
                  <th className="px-2 py-1 text-right font-medium">OI</th>
                  <th className="px-2 py-1 text-right font-medium">IV</th>
                  <th className="px-2 py-1 text-right font-medium">Δ</th>
                  <th className="border-x px-2 py-1"></th>
                  <th className="px-2 py-1 text-left font-medium">LTP</th>
                  <th className="px-2 py-1 text-left font-medium">OI</th>
                  <th className="px-2 py-1 text-left font-medium">IV</th>
                  <th className="px-2 py-1 text-left font-medium">Δ</th>
                </tr>
              </thead>
              <tbody>
                {strikes.map((row, i) => {
                  const isAtm = i === closestStrikeIndex(strikes, spot);
                  return (
                    <tr
                      key={row.strike}
                      className={cn(
                        "border-t border-border/40",
                        isAtm && "bg-primary/5",
                        !isAtm && i % 2 === 1 && "bg-muted/20",
                      )}
                    >
                      <td className="px-2 py-1.5 text-right">{fmt(row.ce?.last_price)}</td>
                      <td className="px-2 py-1.5 text-right text-muted-foreground">{fmt(row.ce?.oi, 0)}</td>
                      <td className="px-2 py-1.5 text-right text-muted-foreground">{fmt(row.ce?.iv)}</td>
                      <td className="px-2 py-1.5 text-right text-muted-foreground">{fmt(row.ce?.delta, 3)}</td>
                      <td
                        className={cn(
                          "border-x px-2 py-1.5 text-center font-semibold",
                          isAtm ? "bg-primary/10 text-primary" : "bg-muted/40",
                        )}
                      >
                        {fmt(row.strike, 0)}
                      </td>
                      <td className="px-2 py-1.5 text-left">{fmt(row.pe?.last_price)}</td>
                      <td className="px-2 py-1.5 text-left text-muted-foreground">{fmt(row.pe?.oi, 0)}</td>
                      <td className="px-2 py-1.5 text-left text-muted-foreground">{fmt(row.pe?.iv)}</td>
                      <td className="px-2 py-1.5 text-left text-muted-foreground">{fmt(row.pe?.delta, 3)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default SimulatorOptionChainPanel;
