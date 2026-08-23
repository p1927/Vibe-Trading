import { useEffect, useState } from "react";
import { ApiError, api, type MarketIndexOhlcRow, type SectorIndexEntry, type TopConstituentRow } from "@/lib/api";
import { IndexCard } from "./IndexCard";

interface SectorCardState {
  key: string;
  name: string;
  price: number | null;
  change: number | null;
  changePct: number | null;
  sparkline: number[];
  loading: boolean;
  error: string | null;
}

function deriveChange(price: number | null, prevClose: number | null): { change: number | null; changePct: number | null } {
  if (price == null || prevClose == null) return { change: null, changePct: null };
  const change = price - prevClose;
  return { change, changePct: prevClose ? (change / prevClose) * 100 : null };
}

function fmtMarketCap(v: number): string {
  if (!Number.isFinite(v)) return "—";
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/** Sector-index cards + top-constituents table for one market — the frontend consumer
 * `sector_indices()`/`top_constituents()` never had (see
 * .claude/backlog/items/2026-08-23-tradingview-sector-constituent-integration.md). Only the
 * `kind: "sector"` entries render as cards here — `kind: "headline"` entries are the same
 * indices `GlobalMarketsPanel`'s own card grid already shows above this panel, so repeating
 * them here would just duplicate that view. Top constituents 404s (`NotSourcedError`, CN/RU/US)
 * render as an honest "not sourced" note, never a crash or a silently-empty table — same
 * convention `EconomyPanel` uses for its own per-market gaps. */
export function SectorIndicesPanel({ country, label }: { country: string; label: string }) {
  const [sectorCards, setSectorCards] = useState<SectorCardState[]>([]);
  const [sectorsLoading, setSectorsLoading] = useState(true);
  const [sectorsError, setSectorsError] = useState<string | null>(null);

  const [constituents, setConstituents] = useState<TopConstituentRow[] | null>(null);
  const [constituentsLoading, setConstituentsLoading] = useState(true);
  const [constituentsNotSourced, setConstituentsNotSourced] = useState(false);
  const [constituentsError, setConstituentsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSectorsLoading(true);
    setSectorsError(null);
    setSectorCards([]);

    api
      .getMarketSectorIndices(country)
      .then((res) => {
        if (cancelled) return;
        const sectors: SectorIndexEntry[] = (res.data ?? []).filter((s) => s.kind === "sector");
        setSectorCards(
          sectors.map((s) => ({
            key: s.name, name: s.label, price: null, change: null, changePct: null, sparkline: [],
            loading: true, error: null,
          })),
        );
        setSectorsLoading(false);
        sectors.forEach((s) => {
          api
            .getMarketIndexHistory(country, s.name, "3mo")
            .then((histRes) => {
              if (cancelled) return;
              const rows: MarketIndexOhlcRow[] = Array.isArray(histRes.data) ? histRes.data : [];
              const closes = rows.map((r) => r.close).filter((v): v is number => typeof v === "number");
              const price = closes.length ? closes[closes.length - 1] : null;
              const prevClose = closes.length > 1 ? closes[closes.length - 2] : null;
              const { change, changePct } = deriveChange(price, prevClose);
              setSectorCards((prev) =>
                prev.map((c) => (c.key === s.name ? { ...c, price, change, changePct, sparkline: closes.slice(-30), loading: false } : c)),
              );
            })
            .catch((err) => {
              if (cancelled) return;
              const message = err instanceof Error ? err.message : String(err);
              setSectorCards((prev) => prev.map((c) => (c.key === s.name ? { ...c, loading: false, error: message } : c)));
            });
        });
      })
      .catch((err) => {
        if (cancelled) return;
        setSectorsError(err instanceof Error ? err.message : String(err));
        setSectorsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [country]);

  useEffect(() => {
    let cancelled = false;
    setConstituentsLoading(true);
    setConstituentsNotSourced(false);
    setConstituentsError(null);
    setConstituents(null);

    api
      .getMarketTopConstituents(country, 10)
      .then((res) => {
        if (cancelled) return;
        setConstituents(Array.isArray(res.data) ? res.data : []);
        setConstituentsLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        const notSourced = err instanceof ApiError && err.status === 404;
        setConstituentsNotSourced(notSourced);
        if (!notSourced) setConstituentsError(err instanceof Error ? err.message : String(err));
        setConstituentsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [country]);

  return (
    <div className="space-y-5">
      <div>
        <p className="mb-2 text-[11px] text-muted-foreground">
          {label}'s sector indices, sourced live via TradingView.
        </p>
        {sectorsError ? (
          <p className="text-[11px] text-destructive">{sectorsError}</p>
        ) : sectorsLoading ? (
          <p className="text-[11px] text-muted-foreground">Loading…</p>
        ) : sectorCards.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No sector indices wired for this market yet.</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {sectorCards.map((c) => (
              <IndexCard
                key={c.key}
                name={c.name}
                price={c.price}
                change={c.change}
                changePct={c.changePct}
                sparkline={c.sparkline}
                badge="EOD"
                loading={c.loading}
                error={c.error}
              />
            ))}
          </div>
        )}
      </div>

      <div>
        <p className="mb-2 text-xs font-semibold">Top Constituents</p>
        {constituentsLoading ? (
          <p className="text-[11px] text-muted-foreground">Loading…</p>
        ) : constituentsNotSourced ? (
          <p className="text-[11px] text-muted-foreground" data-testid="constituents-not-sourced">
            Not sourced for this market — no live constituent-ranking query is registered here.
          </p>
        ) : constituentsError ? (
          <p className="text-[11px] text-destructive">{constituentsError}</p>
        ) : !constituents || constituents.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No constituents returned.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b bg-muted/40 text-left text-[11px] text-muted-foreground">
                  <th className="px-2 py-1.5">#</th>
                  <th className="px-2 py-1.5">Symbol</th>
                  <th className="px-2 py-1.5">Sector</th>
                  <th className="px-2 py-1.5 text-right">Close</th>
                  <th className="px-2 py-1.5 text-right">Market Cap</th>
                </tr>
              </thead>
              <tbody>
                {constituents.map((row, i) => (
                  <tr key={row.symbol} className="border-b last:border-0">
                    <td className="px-2 py-1.5 text-muted-foreground">{i + 1}</td>
                    <td className="px-2 py-1.5 font-medium">{row.symbol}</td>
                    <td className="px-2 py-1.5 text-muted-foreground">{row.sector ?? "—"}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {typeof row.close === "number" ? row.close.toLocaleString("en-US", { maximumFractionDigits: 2 }) : "—"}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums">
                      {typeof row.market_cap_basic === "number" ? fmtMarketCap(row.market_cap_basic) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
