/**
 * SimulatorOptionChainPanel — Phase 9
 *
 * Portal-mounted drawer showing the live option chain for the selected
 * underlying. Polls /trade/hub/market-data/option-chain every 5 s.
 * Shows strikes + CE/PE LTP, OI (with a Groww-style OI bar), IV, delta.
 * ITM strikes are shaded, the ATM strike is highlighted, and the header
 * strip carries LTP/change plus the put-call ratio — styled after Groww's
 * option-chain layout (OI outermost, LTP innermost next to the strike).
 * Pause when closed.
 */
import { useEffect, useMemo, useState } from "react";
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
  isReplayArmed?: boolean;
  strikeCount?: number;
}

interface Leg {
  last_price?: number;
  oi?: number;
  iv?: number;
  delta?: number;
  volume?: number;
  top_bid_price?: number;
  top_ask_price?: number;
}

interface StrikeRow {
  strike: number;
  ce?: Leg | null;
  pe?: Leg | null;
}

function fmt(v: number | undefined | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

// Groww-style compact OI numerals: thousand/lakh/crore suffixes instead of
// raw grouped digits — matches how OI reads on Groww's chain at a glance.
function fmtOi(v: number | undefined | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
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

// Horizontal bar behind an OI cell, width relative to the max OI on that
// side of the chain in the current view — the visual buildup cue Groww's
// chain uses instead of making you compare raw numbers row by row.
function OiBar({ value, max, side }: { value: number | undefined; max: number; side: "ce" | "pe" }) {
  if (!Number.isFinite(value) || !value || max <= 0) return null;
  const pct = Math.max(2, Math.min(100, (value / max) * 100));
  return (
    <span
      aria-hidden="true"
      className={cn(
        "absolute inset-y-0 top-[15%] h-[70%] rounded-sm",
        side === "ce" ? "right-0 bg-emerald-500/15" : "left-0 bg-red-500/15",
      )}
      style={{ width: `${pct}%` }}
    />
  );
}

export function SimulatorOptionChainPanel({
  symbol,
  exchange = "NSE_INDEX",
  open,
  onClose,
  isReplayArmed = false,
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
          replay: isReplayArmed,
        }),
        api.getHubMarketDataSpot({ symbol, exchange, replay: isReplayArmed }),
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
        // Chain and spot are two independent calls that each read the
        // simulator's sim clock in replay mode — if a replay day rolls over
        // between them they can land on different days. The chain's own
        // underlying_ltp is what ATM-strike highlighting is computed against,
        // so keep it authoritative there and only take prev_close from spot;
        // only fall back to spot's ltp when the chain call itself failed.
        if (chainRes.status !== "ok") {
          setSpot(spotRes.spot.ltp);
        }
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
  }, [open, symbol, exchange, strikeCount, isReplayArmed]);

  const change = useMemo(() => {
    if (spot == null || prevClose == null) return null;
    return spot - prevClose;
  }, [spot, prevClose]);

  const changePct = useMemo(() => {
    if (spot == null || prevClose == null || prevClose === 0) return null;
    return ((spot - prevClose) / prevClose) * 100;
  }, [spot, prevClose]);

  const { maxCeOi, maxPeOi, pcr } = useMemo(() => {
    let ceOiTotal = 0;
    let peOiTotal = 0;
    let ceOiMax = 0;
    let peOiMax = 0;
    for (const row of strikes) {
      const ceOi = row.ce?.oi;
      const peOi = row.pe?.oi;
      if (Number.isFinite(ceOi)) {
        ceOiTotal += ceOi as number;
        ceOiMax = Math.max(ceOiMax, ceOi as number);
      }
      if (Number.isFinite(peOi)) {
        peOiTotal += peOi as number;
        peOiMax = Math.max(peOiMax, peOi as number);
      }
    }
    return {
      maxCeOi: ceOiMax,
      maxPeOi: peOiMax,
      pcr: ceOiTotal > 0 ? peOiTotal / ceOiTotal : null,
    };
  }, [strikes]);

  if (!open) return null;

  const positive = (change ?? 0) >= 0;

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
        <div className="sticky top-0 z-20 border-b bg-background/95 px-4 py-3 backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="option-chain-title" className="text-sm font-semibold">
                {symbol} Option Chain
              </h2>
              <p className="mt-0.5 text-[11px] text-muted-foreground">{expiry ?? "expiry —"}</p>
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
          <div className="mt-2 flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums">{fmt(spot)}</span>
              {change != null && (
                <span
                  className={cn(
                    "text-xs font-medium tabular-nums",
                    positive ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400",
                  )}
                >
                  {positive ? "▲" : "▼"} {fmt(Math.abs(change))}
                  {changePct != null && <> ({positive ? "+" : ""}{changePct.toFixed(2)}%)</>}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
              <span>
                PCR{" "}
                <span
                  className={cn(
                    "font-semibold tabular-nums",
                    pcr == null
                      ? "text-foreground"
                      : pcr >= 1
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-red-600 dark:text-red-400",
                  )}
                >
                  {pcr == null ? "—" : pcr.toFixed(2)}
                </span>
              </span>
              {lastUpdated && (
                <span className="text-muted-foreground/70">
                  updated {lastUpdated.toLocaleTimeString([], { hour12: false })}
                </span>
              )}
            </div>
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
            <table className="w-full border-collapse text-[11px] tabular-nums">
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
                  <th className="px-2 py-0.5 text-right font-medium">OI</th>
                  <th className="px-2 py-0.5 text-right font-medium">IV</th>
                  <th className="px-2 py-0.5 text-right font-medium">Δ</th>
                  <th className="px-2 py-0.5 text-right font-medium">LTP</th>
                  <th className="border-x px-2 py-0.5"></th>
                  <th className="px-2 py-0.5 text-left font-medium">LTP</th>
                  <th className="px-2 py-0.5 text-left font-medium">Δ</th>
                  <th className="px-2 py-0.5 text-left font-medium">IV</th>
                  <th className="px-2 py-0.5 text-left font-medium">OI</th>
                </tr>
              </thead>
              <tbody>
                {strikes.map((row, i) => {
                  const isAtm = i === closestStrikeIndex(strikes, spot);
                  const ceItm = spot != null && row.strike < spot;
                  const peItm = spot != null && row.strike > spot;
                  return (
                    <tr
                      key={row.strike}
                      className={cn(
                        "border-t border-border/40",
                        isAtm && "bg-primary/5",
                        !isAtm && i % 2 === 1 && "bg-muted/10",
                      )}
                    >
                      <td className={cn("relative overflow-hidden px-2 py-1 text-right text-muted-foreground", !isAtm && ceItm && "bg-emerald-500/5")}>
                        <OiBar value={row.ce?.oi} max={maxCeOi} side="ce" />
                        <span className="relative">{fmtOi(row.ce?.oi)}</span>
                      </td>
                      <td className={cn("px-2 py-1 text-right text-muted-foreground", !isAtm && ceItm && "bg-emerald-500/5")}>{fmt(row.ce?.iv)}</td>
                      <td className={cn("px-2 py-1 text-right text-muted-foreground", !isAtm && ceItm && "bg-emerald-500/5")}>{fmt(row.ce?.delta, 3)}</td>
                      <td className={cn("px-2 py-1 text-right font-medium", !isAtm && ceItm && "bg-emerald-500/5")}>{fmt(row.ce?.last_price)}</td>
                      <td
                        className={cn(
                          "border-x px-2 py-1 text-center font-semibold",
                          isAtm ? "bg-primary/10 text-primary" : "bg-muted/40",
                        )}
                      >
                        {fmt(row.strike, 0)}
                      </td>
                      <td className={cn("px-2 py-1 text-left font-medium", !isAtm && peItm && "bg-red-500/5")}>{fmt(row.pe?.last_price)}</td>
                      <td className={cn("px-2 py-1 text-left text-muted-foreground", !isAtm && peItm && "bg-red-500/5")}>{fmt(row.pe?.delta, 3)}</td>
                      <td className={cn("px-2 py-1 text-left text-muted-foreground", !isAtm && peItm && "bg-red-500/5")}>{fmt(row.pe?.iv)}</td>
                      <td className={cn("relative overflow-hidden px-2 py-1 text-left text-muted-foreground", !isAtm && peItm && "bg-red-500/5")}>
                        <OiBar value={row.pe?.oi} max={maxPeOi} side="pe" />
                        <span className="relative">{fmtOi(row.pe?.oi)}</span>
                      </td>
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
