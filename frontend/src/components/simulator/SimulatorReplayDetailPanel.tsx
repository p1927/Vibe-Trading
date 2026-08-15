import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  api,
  type HubIndexHistoryBar,
  type HubReplayDayOverviewResponse,
  type ReplayCalendarDay,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Underlying = "NIFTY" | "BANKNIFTY" | "SENSEX";

const UNDERLYINGS: Underlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];

const EXCHANGE_FOR: Record<Underlying, string> = {
  NIFTY: "NSE_INDEX",
  BANKNIFTY: "NSE_INDEX",
  SENSEX: "BSE_INDEX",
};

const ROWS_KEY: Record<Underlying, keyof ReplayCalendarDay> = {
  NIFTY: "nifty_rows",
  BANKNIFTY: "banknifty_rows",
  SENSEX: "sensex_rows",
};

function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/**
 * Slide-over panel showing what stock-history data is on disk for a replay
 * calendar date: per-underlying row counts from the calendar payload (no
 * extra request) plus OHLC bars fetched on demand per underlying.
 */
export function SimulatorReplayDetailPanel({
  day,
  onClose,
}: {
  day: ReplayCalendarDay | null;
  onClose: () => void;
}) {
  const [underlying, setUnderlying] = useState<Underlying>("NIFTY");
  const [bars, setBars] = useState<HubIndexHistoryBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<HubReplayDayOverviewResponse | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);

  useEffect(() => {
    if (day) setUnderlying("NIFTY");
  }, [day]);

  // Overview loads immediately on open, independent of the per-underlying bar
  // fetch below — it's a quick "what's on disk for this day" preview across
  // every data vertical (equities, options, macro factors, constituents),
  // not the detailed per-minute drill-down.
  useEffect(() => {
    if (!day) {
      setOverview(null);
      return;
    }
    let cancelled = false;
    setOverviewLoading(true);
    setOverview(null);
    api
      .getHubReplayDayOverview(day.date)
      .then((res) => {
        if (!cancelled) setOverview(res);
      })
      .catch(() => {
        if (!cancelled) setOverview(null);
      })
      .finally(() => {
        if (!cancelled) setOverviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [day]);

  useEffect(() => {
    if (!day) return;
    const hasData = Number(day[ROWS_KEY[underlying]] ?? 0) > 0;
    if (!hasData) {
      setBars([]);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getHubIndexHistoryBars({
        symbol: underlying,
        exchange: EXCHANGE_FOR[underlying],
        since_ist: `${day.date}T09:15:00`,
        until_ist: `${day.date}T15:30:00`,
      })
      .then((res) => {
        if (cancelled) return;
        if (res.status === "ok") {
          setBars(res.bars || []);
        } else {
          setBars([]);
          setError(res.error || "No bars returned");
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setBars([]);
          setError(err instanceof Error ? err.message : "Failed to load bars");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [day, underlying]);

  const summary = useMemo(() => {
    if (bars.length === 0) return null;
    const open = bars[0].open;
    const close = bars[bars.length - 1].close;
    const high = Math.max(...bars.map((b) => b.high));
    const low = Math.min(...bars.map((b) => b.low));
    const volume = bars.reduce((sum, b) => sum + (b.volume || 0), 0);
    return { open, close, high, low, volume, count: bars.length };
  }, [bars]);

  const chartData = useMemo(
    () =>
      bars.map((b) => ({
        t: b.ts_ist.slice(11, 16),
        close: b.close,
      })),
    [bars],
  );

  const open = day !== null;

  return (
    <>
      {open ? (
        <div
          className="fixed inset-0 z-40 bg-background/40 backdrop-blur-[1px]"
          onClick={onClose}
          aria-hidden="true"
        />
      ) : null}
      <aside
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-xl transition-transform duration-200",
          open ? "translate-x-0" : "translate-x-full",
        )}
      >
        {day ? (
          <>
            <div className="flex items-center justify-between border-b px-4 py-3">
              <div>
                <div className="text-sm font-semibold">{day.date}</div>
                <div className="text-[11px] text-muted-foreground">
                  {new Date(day.date + "T00:00:00").toLocaleDateString(undefined, {
                    weekday: "long",
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </div>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                title="Close panel"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-2 border-b p-3">
              <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                What's recorded this day
              </div>
              <div className="grid grid-cols-3 gap-1.5 text-[11px]">
                {UNDERLYINGS.map((u) => {
                  const rows = Number(day[ROWS_KEY[u]] ?? 0);
                  return (
                    <div
                      key={u}
                      className={cn(
                        "rounded-lg border p-1.5",
                        rows > 0 ? "bg-background/60" : "bg-muted/30 text-muted-foreground",
                      )}
                    >
                      <div className="text-[9px] text-muted-foreground">{u}</div>
                      <div className="font-medium tabular-nums">{rows > 0 ? `${rows} bars` : "—"}</div>
                    </div>
                  );
                })}
              </div>

              {overviewLoading ? (
                <p className="text-[11px] text-muted-foreground">Checking equities, options, macro factors…</p>
              ) : overview && overview.status === "ok" ? (
                <div className="space-y-1.5 text-[11px]">
                  <div className="rounded-lg border bg-background/60 p-1.5">
                    <div className="text-[9px] text-muted-foreground">Equities recorded</div>
                    {overview.equities.length === 0 ? (
                      <div className="text-muted-foreground">None</div>
                    ) : (
                      <div className="font-medium">
                        {overview.equities.length} symbol{overview.equities.length === 1 ? "" : "s"}
                        <span className="ml-1 font-normal text-muted-foreground">
                          ({overview.equities.slice(0, 6).map((e) => e.symbol).join(", ")}
                          {overview.equities.length > 6 ? `, +${overview.equities.length - 6} more` : ""})
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="rounded-lg border bg-background/60 p-1.5">
                    <div className="text-[9px] text-muted-foreground">Option-chain coverage</div>
                    {Object.keys(overview.options).length === 0 ? (
                      <div className="text-muted-foreground">None</div>
                    ) : (
                      <div className="font-medium">
                        {Object.entries(overview.options)
                          .map(([u, expiries]) => `${u} (${expiries.length} expiry file${expiries.length === 1 ? "" : "s"})`)
                          .join(" · ")}
                      </div>
                    )}
                  </div>

                  <div className="rounded-lg border bg-background/60 p-1.5">
                    <div className="text-[9px] text-muted-foreground">Macro / prediction factors</div>
                    {overview.macro_factor_keys.length === 0 ? (
                      <div className="text-muted-foreground">None</div>
                    ) : (
                      <div className="font-medium">
                        {overview.macro_factor_keys.length} factor{overview.macro_factor_keys.length === 1 ? "" : "s"}
                        <span className="ml-1 font-normal text-muted-foreground">
                          ({overview.macro_factor_keys.slice(0, 6).join(", ")}
                          {overview.macro_factor_keys.length > 6 ? `, +${overview.macro_factor_keys.length - 6} more` : ""})
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="rounded-lg border bg-background/60 p-1.5">
                    <div className="text-[9px] text-muted-foreground">Index constituents</div>
                    <div className="font-medium">
                      {overview.constituents_available ? "Available" : "Not available"}
                    </div>
                  </div>
                </div>
              ) : overview && overview.status !== "ok" ? (
                <p className="text-[11px] text-destructive">{overview.error || "Failed to load overview"}</p>
              ) : null}
            </div>

            <div className="px-3 pt-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              Per-minute detail
            </div>
            <div className="flex border-b text-[11px]">
              {UNDERLYINGS.map((u) => {
                const rows = Number(day[ROWS_KEY[u]] ?? 0);
                return (
                  <button
                    key={u}
                    type="button"
                    onClick={() => setUnderlying(u)}
                    disabled={rows === 0}
                    className={cn(
                      "flex-1 px-2 py-2 font-medium transition disabled:cursor-not-allowed disabled:opacity-40",
                      underlying === u
                        ? "border-b-2 border-primary text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {u}
                    <span className="ml-1 text-[10px] text-muted-foreground">({rows})</span>
                  </button>
                );
              })}
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              {Number(day[ROWS_KEY[underlying]] ?? 0) === 0 ? (
                <p className="text-[11px] text-muted-foreground">
                  No {underlying} data recorded on {day.date}.
                </p>
              ) : loading ? (
                <p className="text-[11px] text-muted-foreground">Loading {underlying} bars…</p>
              ) : error ? (
                <p className="text-[11px] text-destructive">{error}</p>
              ) : bars.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">
                  No {underlying} bars returned for {day.date}.
                </p>
              ) : (
                <div className="space-y-4">
                  {summary ? (
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div className="rounded-lg border bg-background/60 p-2">
                        <div className="text-[10px] text-muted-foreground">Open</div>
                        <div className="font-medium tabular-nums">{fmtNum(summary.open)}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-2">
                        <div className="text-[10px] text-muted-foreground">Close</div>
                        <div className="font-medium tabular-nums">{fmtNum(summary.close)}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-2">
                        <div className="text-[10px] text-muted-foreground">High</div>
                        <div className="font-medium tabular-nums">{fmtNum(summary.high)}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-2">
                        <div className="text-[10px] text-muted-foreground">Low</div>
                        <div className="font-medium tabular-nums">{fmtNum(summary.low)}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-2">
                        <div className="text-[10px] text-muted-foreground">Bars</div>
                        <div className="font-medium tabular-nums">{summary.count}</div>
                      </div>
                      <div className="rounded-lg border bg-background/60 p-2">
                        <div className="text-[10px] text-muted-foreground">Volume</div>
                        <div className="font-medium tabular-nums">{fmtNum(summary.volume)}</div>
                      </div>
                    </div>
                  ) : null}

                  {chartData.length > 1 ? (
                    <div className="h-40 rounded-lg border bg-background/60 p-2">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                          <defs>
                            <linearGradient id="replayClose" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor="currentColor" stopOpacity={0.35} />
                              <stop offset="100%" stopColor="currentColor" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <XAxis dataKey="t" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                          <YAxis domain={["auto", "auto"]} tick={{ fontSize: 9 }} width={44} />
                          <Tooltip
                            contentStyle={{ fontSize: 11 }}
                            formatter={(value: number) => fmtNum(value)}
                          />
                          <Area
                            type="monotone"
                            dataKey="close"
                            stroke="currentColor"
                            className="text-primary"
                            fill="url(#replayClose)"
                            strokeWidth={1.5}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          </>
        ) : null}
      </aside>
    </>
  );
}
