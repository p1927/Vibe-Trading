import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError, api, type EconomyFactorRow } from "@/lib/api";
import { COUNTRY_LABELS } from "./GlobalMarketsPanel";

const MARKETS = ["IN", "US", "CN", "JP", "RU", "ME", "LATAM"];

// Fixed per-market colors so a line keeps its identity across factor switches — reuses the same
// 7-market set `MultiMarketReplayPanel`/`GlobalMarketsPanel` already work with.
const MARKET_COLOR: Record<string, string> = {
  IN: "#f97316",
  US: "#2563eb",
  CN: "#dc2626",
  JP: "#7c3aed",
  RU: "#0891b2",
  ME: "#65a30d",
  LATAM: "#db2777",
};

// The 6 economy factors the backlog item asked for (`market_factor_catalog.FactorCategory.
// ECONOMY`, minus `pmi_manufacturing` — confirmed `NotSourcedError` for all 7 markets, so
// there's nothing to plot for it; it still shows up honestly as "not sourced" via the per-market
// coverage panel elsewhere).
const FACTORS: { key: string; label: string; unit: string }[] = [
  { key: "gdp_growth", label: "GDP Growth", unit: "% YoY, real" },
  { key: "fiscal_balance", label: "Fiscal Balance", unit: "% of GDP" },
  { key: "current_account_balance", label: "Current Account Balance", unit: "% of GDP" },
  { key: "unemployment_rate", label: "Unemployment Rate", unit: "%" },
  { key: "consumption_share_gdp", label: "Consumption (Private, share of GDP)", unit: "% of GDP" },
  { key: "industrial_production", label: "Industrial Production", unit: "index" },
];

interface MarketSeriesState {
  rows: EconomyFactorRow[];
  loading: boolean;
  notSourced: boolean;
  error: string | null;
}

const EMPTY_STATE: MarketSeriesState = { rows: [], loading: true, notSourced: false, error: null };

function parseValue(raw: EconomyFactorRow["value"]): number | null {
  if (raw == null) return null;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  const trimmed = raw.trim();
  if (trimmed === "" || trimmed === ".") return null; // FRED's own "missing observation" marker
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

// Annual rows (IMF/World Bank) carry `year`; monthly rows (FRED) carry `date` — normalized to a
// "YYYY" or "YYYY-MM" period label so the two cadences can share one x-axis without pretending a
// year value is a specific month.
function periodOf(row: EconomyFactorRow): string {
  if (row.year != null) return String(row.year);
  if (row.date) return String(row.date).slice(0, 7);
  return "unknown";
}

function periodSortKey(period: string): number {
  const year = Number(period.slice(0, 4));
  const month = period.length > 4 ? Number(period.slice(5, 7)) || 0 : 0;
  return Number.isFinite(year) ? year * 100 + month : 0;
}

type ViewMode = "cross-market" | "per-market";

/** Cross-market comparison view for `FactorCategory.ECONOMY` — GDP growth, fiscal balance,
 * current-account balance, unemployment, consumption share of GDP, industrial production — one
 * factor at a time, all 7 `market_registry` markets overlaid on a shared chart. Reads through
 * `stock_simulator`'s `get_economy_factor()` facade (`/trade/markets/{country}/economy/{series}`
 * proxying `/history/{country}/economy/{series}`), never `stock_history`/store internals
 * directly, per the repo's facade-boundary rule. `pmi_manufacturing` raises `NotSourcedError`
 * for every market today (`market_factor_catalog.py`) — this renders that as an honest
 * "not sourced" badge per market, never a crash or a silently-empty chart.
 *
 * Also offers a per-market drill-down (`ViewMode.per-market`): all 6 factors for one selected
 * country, since the factors don't share a unit (%, % of GDP, index) and can't be overlaid on a
 * single cross-market chart the way one factor across markets can. */
export function EconomyPanel() {
  const [viewMode, setViewMode] = useState<ViewMode>("cross-market");
  const [factor, setFactor] = useState<string>(FACTORS[0].key);
  const [byMarket, setByMarket] = useState<Record<string, MarketSeriesState>>({});

  useEffect(() => {
    if (viewMode !== "cross-market") return;
    setByMarket(Object.fromEntries(MARKETS.map((m) => [m, { ...EMPTY_STATE }])));
    let cancelled = false;
    MARKETS.forEach((market) => {
      api
        .getMarketEconomyFactor(market, factor)
        .then((res) => {
          if (cancelled) return;
          const rows = Array.isArray(res.data) ? res.data : [];
          setByMarket((prev) => ({ ...prev, [market]: { rows, loading: false, notSourced: false, error: null } }));
        })
        .catch((err) => {
          if (cancelled) return;
          const notSourced = err instanceof ApiError && err.status === 404;
          const message = err instanceof Error ? err.message : String(err);
          setByMarket((prev) => ({
            ...prev,
            [market]: { rows: [], loading: false, notSourced, error: notSourced ? null : message },
          }));
        });
    });
    return () => {
      cancelled = true;
    };
  }, [viewMode, factor]);

  const [detailMarket, setDetailMarket] = useState<string>(MARKETS[0]);
  const [byFactor, setByFactor] = useState<Record<string, MarketSeriesState>>({});

  useEffect(() => {
    if (viewMode !== "per-market") return;
    setByFactor(Object.fromEntries(FACTORS.map((f) => [f.key, { ...EMPTY_STATE }])));
    let cancelled = false;
    FACTORS.forEach((f) => {
      api
        .getMarketEconomyFactor(detailMarket, f.key)
        .then((res) => {
          if (cancelled) return;
          const rows = Array.isArray(res.data) ? res.data : [];
          setByFactor((prev) => ({ ...prev, [f.key]: { rows, loading: false, notSourced: false, error: null } }));
        })
        .catch((err) => {
          if (cancelled) return;
          const notSourced = err instanceof ApiError && err.status === 404;
          const message = err instanceof Error ? err.message : String(err);
          setByFactor((prev) => ({
            ...prev,
            [f.key]: { rows: [], loading: false, notSourced, error: notSourced ? null : message },
          }));
        });
    });
    return () => {
      cancelled = true;
    };
  }, [viewMode, detailMarket]);

  const chartData = useMemo(() => {
    const periods = new Set<string>();
    for (const market of MARKETS) {
      for (const row of byMarket[market]?.rows ?? []) {
        if (parseValue(row.value) != null) periods.add(periodOf(row));
      }
    }
    const sortedPeriods = [...periods].sort((a, b) => periodSortKey(a) - periodSortKey(b));
    return sortedPeriods.map((period) => {
      const point: Record<string, string | number | null> = { period };
      for (const market of MARKETS) {
        const row = (byMarket[market]?.rows ?? []).find((r) => periodOf(r) === period);
        point[market] = row ? parseValue(row.value) : null;
      }
      return point;
    });
  }, [byMarket]);

  const activeFactor = FACTORS.find((f) => f.key === factor) ?? FACTORS[0];
  const hasAnyData = chartData.length > 0;
  const allLoading = MARKETS.every((m) => byMarket[m]?.loading);

  return (
    <div className="space-y-4">
      <p className="text-[11px] text-muted-foreground">
        {viewMode === "cross-market" ? (
          <>
            Cross-market comparison of {" "}
            <code className="rounded bg-muted px-1 py-0.5">FactorCategory.ECONOMY</code> series —
            IMF/World Bank annual data or FRED monthly data, mixed cadence per market. A market
            with no confirmed source for the selected factor shows "Not sourced" instead of a
            blank line.
          </>
        ) : (
          <>
            All {" "}
            <code className="rounded bg-muted px-1 py-0.5">FactorCategory.ECONOMY</code> series for
            one market. Each factor has its own unit, so each gets its own chart rather than a
            shared axis.
          </>
        )}
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded border bg-background p-0.5 text-xs" data-testid="economy-view-toggle">
          <button
            type="button"
            onClick={() => setViewMode("cross-market")}
            className={`rounded px-2 py-1 ${viewMode === "cross-market" ? "bg-muted font-semibold" : "text-muted-foreground"}`}
          >
            Cross-market
          </button>
          <button
            type="button"
            onClick={() => setViewMode("per-market")}
            className={`rounded px-2 py-1 ${viewMode === "per-market" ? "bg-muted font-semibold" : "text-muted-foreground"}`}
          >
            Per-market
          </button>
        </div>

        {viewMode === "cross-market" ? (
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Factor
            <select
              value={factor}
              onChange={(e) => setFactor(e.target.value)}
              className="rounded border bg-background px-1.5 py-1 text-xs"
              data-testid="economy-factor-picker"
            >
              {FACTORS.map((f) => (
                <option key={f.key} value={f.key}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Market
            <select
              value={detailMarket}
              onChange={(e) => setDetailMarket(e.target.value)}
              className="rounded border bg-background px-1.5 py-1 text-xs"
              data-testid="economy-market-picker"
            >
              {MARKETS.map((m) => (
                <option key={m} value={m}>
                  {COUNTRY_LABELS[m] ?? m}
                </option>
              ))}
            </select>
          </label>
        )}
        {viewMode === "cross-market" && <span className="text-[11px] text-muted-foreground">{activeFactor.unit}</span>}
      </div>

      {viewMode === "per-market" ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="economy-per-market-grid">
          {FACTORS.map((f) => {
            const state = byFactor[f.key] ?? EMPTY_STATE;
            const rows = [...state.rows].sort((a, b) => periodSortKey(periodOf(a)) - periodSortKey(periodOf(b)));
            const data = rows
              .filter((r) => parseValue(r.value) != null)
              .map((r) => ({ period: periodOf(r), value: parseValue(r.value) }));
            return (
              <div key={f.key} className="rounded-lg border bg-background/60 p-2">
                <p className="text-xs font-semibold">{f.label}</p>
                <p className="mb-1 text-[10px] text-muted-foreground">{f.unit}</p>
                <div className="h-40 w-full">
                  {state.loading ? (
                    <p className="flex h-full items-center justify-center text-[11px] text-muted-foreground">
                      Loading…
                    </p>
                  ) : state.notSourced ? (
                    <p
                      className="flex h-full items-center justify-center text-[11px] text-muted-foreground"
                      data-testid={`economy-detail-not-sourced-${f.key}`}
                    >
                      Not sourced for this market
                    </p>
                  ) : state.error ? (
                    <p className="flex h-full items-center justify-center text-[11px] text-destructive">
                      {state.error}
                    </p>
                  ) : data.length > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                        <XAxis dataKey="period" tick={{ fontSize: 9 }} />
                        <YAxis tick={{ fontSize: 9 }} width={36} />
                        <Tooltip contentStyle={{ fontSize: 11 }} />
                        <Line
                          type="monotone"
                          dataKey="value"
                          stroke={MARKET_COLOR[detailMarket] ?? "#2563eb"}
                          strokeWidth={1.75}
                          dot={false}
                          isAnimationActive={false}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : (
                    <p className="flex h-full items-center justify-center text-[11px] text-muted-foreground">
                      No data points.
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <>
      <div className="h-72 w-full rounded-lg border bg-background/60 p-2">
        {allLoading ? (
          <p className="flex h-full items-center justify-center text-[11px] text-muted-foreground">Loading…</p>
        ) : hasAnyData ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="period" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} width={48} />
              <Tooltip
                contentStyle={{ fontSize: 11 }}
                formatter={(value, name) => [value ?? "—", COUNTRY_LABELS[String(name)] ?? String(name)]}
              />
              <Legend
                formatter={(value: string) => COUNTRY_LABELS[value] ?? value}
                wrapperStyle={{ fontSize: 11 }}
              />
              {MARKETS.map((market) => (
                <Line
                  key={market}
                  type="monotone"
                  dataKey={market}
                  stroke={MARKET_COLOR[market]}
                  strokeWidth={1.75}
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="flex h-full items-center justify-center text-[11px] text-muted-foreground">
            No market has sourced data for {activeFactor.label} yet.
          </p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" data-testid="economy-market-status">
        {MARKETS.map((market) => {
          const state = byMarket[market] ?? EMPTY_STATE;
          const latest = [...state.rows]
            .filter((r) => parseValue(r.value) != null)
            .sort((a, b) => periodSortKey(periodOf(b)) - periodSortKey(periodOf(a)))[0];
          return (
            <div key={market} className="rounded-lg border bg-card p-2.5">
              <p className="text-xs font-semibold">{COUNTRY_LABELS[market] ?? market}</p>
              {state.loading ? (
                <p className="mt-1 text-[11px] text-muted-foreground">Loading…</p>
              ) : state.notSourced ? (
                <p className="mt-1 text-[11px] text-muted-foreground" data-testid={`economy-not-sourced-${market}`}>
                  Not sourced for this market
                </p>
              ) : state.error ? (
                <p className="mt-1 text-[11px] text-destructive">{state.error}</p>
              ) : latest ? (
                <p className="mt-1 text-sm tabular-nums">
                  {parseValue(latest.value)?.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                  <span className="ml-1 text-[11px] font-normal text-muted-foreground">@ {periodOf(latest)}</span>
                </p>
              ) : (
                <p className="mt-1 text-[11px] text-muted-foreground">No data points.</p>
              )}
            </div>
          );
        })}
      </div>
        </>
      )}
    </div>
  );
}
