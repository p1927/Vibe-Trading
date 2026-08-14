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
      if (chainRes.status === "ok") setError(null);
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
        className="flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl sm:max-w-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b px-4 py-3">
          <div>
            <h2 id="option-chain-title" className="text-sm font-semibold">
              {symbol} Option Chain
            </h2>
            <p className="text-[11px] text-muted-foreground">
              {expiry ?? "expiry —"} · LTP {fmt(spot)}
              {prevClose != null && (
                <>
                  {" · prev close "}
                  {fmt(prevClose)}
                </>
              )}
            </p>
          </div>
          <div className="flex items-center gap-1">
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
            <p
              className="border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs text-red-700 dark:text-red-400"
              data-testid="option-chain-error"
            >
              {error}
            </p>
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
              <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                <tr>
                  <th className="px-2 py-1 text-right" colSpan={4}>CE</th>
                  <th className="px-2 py-1 text-center">Strike</th>
                  <th className="px-2 py-1 text-left" colSpan={4}>PE</th>
                </tr>
                <tr className="text-[10px] text-muted-foreground">
                  <th className="px-2 py-0.5 text-right">LTP</th>
                  <th className="px-2 py-0.5 text-right">OI</th>
                  <th className="px-2 py-0.5 text-right">IV</th>
                  <th className="px-2 py-0.5 text-right">Δ</th>
                  <th></th>
                  <th className="px-2 py-0.5 text-left">LTP</th>
                  <th className="px-2 py-0.5 text-left">OI</th>
                  <th className="px-2 py-0.5 text-left">IV</th>
                  <th className="px-2 py-0.5 text-left">Δ</th>
                </tr>
              </thead>
              <tbody>
                {strikes.map((row) => (
                  <tr key={row.strike} className="border-t border-border/40">
                    <td className="px-2 py-1 text-right">{fmt(row.ce?.last_price)}</td>
                    <td className="px-2 py-1 text-right">{fmt(row.ce?.oi, 0)}</td>
                    <td className="px-2 py-1 text-right">{fmt(row.ce?.iv)}</td>
                    <td className="px-2 py-1 text-right">{fmt(row.ce?.delta, 3)}</td>
                    <td className="px-2 py-1 text-center font-semibold">{fmt(row.strike, 0)}</td>
                    <td className="px-2 py-1 text-left">{fmt(row.pe?.last_price)}</td>
                    <td className="px-2 py-1 text-left">{fmt(row.pe?.oi, 0)}</td>
                    <td className="px-2 py-1 text-left">{fmt(row.pe?.iv)}</td>
                    <td className="px-2 py-1 text-left">{fmt(row.pe?.delta, 3)}</td>
                  </tr>
                ))}
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
