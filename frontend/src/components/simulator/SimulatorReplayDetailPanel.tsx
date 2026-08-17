import { useEffect, useMemo, useState } from "react";
import { Loader2, Wand2, X } from "lucide-react";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import {
  api,
  type HubIndexHistoryBar,
  type HubReplayDayOverviewResponse,
  type HubStockHistoryBackfillResult,
  type HubStockHistoryCoverageDay,
  type PriceBar,
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

// Which Data-Coverage bucket carries the backtestable OHLC series for each
// underlying. SENSEX's bucket is optional (only required for SENSEX weeks),
// so it only shows up when coverage is fetched with include_optional=true —
// which this panel always does.
const INDEX_TAPE_BUCKET: Partial<Record<Underlying, string>> = {
  NIFTY: "index_tape_nifty",
  BANKNIFTY: "index_tape_banknifty",
  SENSEX: "index_tape_sensex",
};

const BUCKET_DISPLAY_NAMES: Record<string, string> = {
  index_tape_nifty: "NIFTY index tape (OHLC candles)",
  index_tape_banknifty: "BANKNIFTY index tape (OHLC candles)",
  index_tape_sensex: "SENSEX index tape (OHLC candles)",
  option_chain_nifty: "NIFTY option chain",
  option_chain_banknifty: "BANKNIFTY option chain",
  option_chain_sensex: "SENSEX option chain",
  macro_factors: "Macro / prediction factors",
  constituents: "Index constituents",
  constituent_ohlcv: "Constituent OHLCV (equities)",
};

function fmtNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function barsToPriceBars(bars: HubIndexHistoryBar[]): PriceBar[] {
  return bars.map((b) => ({
    time: b.ts_ist.slice(11, 16),
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume,
  }));
}

/**
 * Slide-over panel showing what stock-history data is on disk for a replay
 * calendar date — split into two layers that are easy to conflate:
 *
 * 1. "What's recorded" — raw rows the day-recorder logged (ticks, depth,
 *    equities, options, macro factors). A non-zero count here does NOT
 *    imply the day is backtestable.
 * 2. "Canonical dataset coverage" — the same per-bucket present/missing
 *    verdict the Data Coverage panel shows below, cross-referenced for
 *    this exact date, with an inline backfill action. This is what
 *    actually gates replay/backtest availability.
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
  const [coverageDay, setCoverageDay] = useState<HubStockHistoryCoverageDay | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(false);
  const [coverageError, setCoverageError] = useState<string | null>(null);
  const [backfilling, setBackfilling] = useState<string | null>(null);
  const [backfillResult, setBackfillResult] = useState<
    Record<string, HubStockHistoryBackfillResult | { error: string }>
  >({});
  // Bumped after a successful backfill so the bars-fetch effect below
  // re-runs even when `underlying` didn't change (e.g. backfilling the
  // bucket behind the currently-selected tab).
  const [barsRefreshKey, setBarsRefreshKey] = useState(0);

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

  // Canonical bucket coverage for this exact date — the same source of
  // truth the Data Coverage panel below reads, so "recorded" and
  // "backtestable" never get conflated again.
  useEffect(() => {
    if (!day) {
      setCoverageDay(null);
      setCoverageError(null);
      setBackfillResult({});
      return;
    }
    let cancelled = false;
    setCoverageLoading(true);
    setCoverageError(null);
    api
      .getHubStockHistoryCoverage({ week: day.date, symbol: "NIFTY", include_optional: true })
      .then((res) => {
        if (cancelled) return;
        if (res.status !== "ok") {
          setCoverageDay(null);
          setCoverageError(res.error ?? "coverage request failed");
          return;
        }
        setCoverageDay(res.days.find((d) => d.day === day.date) ?? null);
      })
      .catch((err) => {
        if (!cancelled) {
          setCoverageDay(null);
          setCoverageError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setCoverageLoading(false);
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
  }, [day, underlying, barsRefreshKey]);

  const priceBars = useMemo(() => barsToPriceBars(bars), [bars]);

  const summary = useMemo(() => {
    if (bars.length === 0) return null;
    const open = bars[0].open;
    const close = bars[bars.length - 1].close;
    const high = Math.max(...bars.map((b) => b.high));
    const low = Math.min(...bars.map((b) => b.low));
    const volume = bars.reduce((sum, b) => sum + (b.volume || 0), 0);
    return { open, close, high, low, volume, count: bars.length };
  }, [bars]);

  const open = day !== null;

  const runBackfill = async (bucket: string) => {
    if (!day) return;
    setBackfilling(bucket);
    try {
      const resp = await api.postHubStockHistoryBackfill({
        week: day.date,
        symbol: "NIFTY",
        include_optional: true,
        buckets: [bucket],
        verify_after: true,
      });
      if (resp.status !== "ok") {
        setBackfillResult((prev) => ({ ...prev, [bucket]: { error: resp.error ?? "backfill failed" } }));
        return;
      }
      const result = resp.summary.results.find((r) => r.bucket === bucket);
      if (result) {
        setBackfillResult((prev) => ({ ...prev, [bucket]: result }));
      }
      const nextDay = resp.coverage_after?.days.find((d) => d.day === day.date);
      if (nextDay) setCoverageDay(nextDay);
      // Bars may now exist where they didn't before — re-trigger the fetch.
      if (bucket === INDEX_TAPE_BUCKET[underlying]) setBarsRefreshKey((k) => k + 1);
    } catch (err) {
      setBackfillResult((prev) => ({
        ...prev,
        [bucket]: { error: err instanceof Error ? err.message : String(err) },
      }));
    } finally {
      setBackfilling(null);
    }
  };

  const indexTapeBucket = INDEX_TAPE_BUCKET[underlying];
  const indexTapeStatus = indexTapeBucket ? coverageDay?.buckets[indexTapeBucket] : undefined;
  const indexTapeMissing = Boolean(indexTapeBucket && coverageDay && indexTapeStatus && !indexTapeStatus.present);

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
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l bg-background shadow-xl transition-transform duration-200",
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

            <div className="flex-1 overflow-y-auto">
              <div className="space-y-2 border-b p-4">
                <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  What's recorded this day (raw rows logged by the day-recorder)
                </div>
                <div className="grid grid-cols-3 gap-2 text-[11px]">
                  {UNDERLYINGS.map((u) => {
                    const rows = Number(day[ROWS_KEY[u]] ?? 0);
                    const bucket = INDEX_TAPE_BUCKET[u];
                    const status = bucket ? coverageDay?.buckets[bucket] : undefined;
                    return (
                      <div
                        key={u}
                        className={cn(
                          "rounded-lg border p-2",
                          rows > 0 ? "bg-background/60" : "bg-muted/30 text-muted-foreground",
                        )}
                      >
                        <div className="text-[9px] text-muted-foreground">{u}</div>
                        <div className="font-medium tabular-nums">{rows > 0 ? `${rows} rows` : "—"}</div>
                        {bucket ? (
                          <div
                            className={cn(
                              "mt-1 inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium",
                              coverageLoading
                                ? "bg-muted text-muted-foreground"
                                : status?.present
                                ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                                : "bg-amber-500/15 text-amber-800 dark:text-amber-300",
                            )}
                          >
                            {coverageLoading ? "checking…" : status?.present ? "OHLC backfilled" : "OHLC missing"}
                          </div>
                        ) : (
                          <div className="mt-1 text-[9px] text-muted-foreground/70">not tracked in coverage</div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {overviewLoading ? (
                  <p className="text-[11px] text-muted-foreground">Checking equities, options, macro factors…</p>
                ) : overview && overview.status === "ok" ? (
                  <div className="grid grid-cols-2 gap-2 text-[11px]">
                    <div className="rounded-lg border bg-background/60 p-2">
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

                    <div className="rounded-lg border bg-background/60 p-2">
                      <div className="text-[9px] text-muted-foreground">Option-chain coverage (recorder)</div>
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

                    <div className="rounded-lg border bg-background/60 p-2">
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

                    <div className="rounded-lg border bg-background/60 p-2">
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

              <div className="space-y-2 border-b p-4">
                <div className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Canonical dataset coverage (what's actually backtestable)
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Same buckets as Data Coverage below, resolved for this exact date. A row can be recorded
                  above and still show missing here — it just hasn't been backfilled into the canonical
                  dataset yet.
                </p>
                {coverageLoading ? (
                  <p className="text-[11px] text-muted-foreground">Loading bucket coverage…</p>
                ) : coverageError ? (
                  <p className="text-[11px] text-destructive">{coverageError}</p>
                ) : !coverageDay ? (
                  <p className="text-[11px] text-muted-foreground">No coverage data for this date.</p>
                ) : (
                  <div className="space-y-1.5">
                    {Object.values(coverageDay.buckets).map((status) => {
                      const progress = backfillResult[status.bucket];
                      const isBackfilling = backfilling === status.bucket;
                      return (
                        <div
                          key={status.bucket}
                          className={cn(
                            "rounded-lg border p-2 text-[11px]",
                            status.present ? "bg-emerald-500/5" : "bg-amber-500/5",
                          )}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                              <span className="font-medium">
                                {BUCKET_DISPLAY_NAMES[status.bucket] ?? status.bucket}
                              </span>
                              <span className="ml-2 font-mono text-[10px] text-muted-foreground">
                                {status.bucket}
                              </span>
                            </div>
                            <span
                              className={cn(
                                "inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-medium",
                                status.present
                                  ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                                  : "bg-amber-500/15 text-amber-800 dark:text-amber-300",
                              )}
                            >
                              {status.present ? "present" : "missing"}
                            </span>
                          </div>
                          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[10px] text-muted-foreground">
                            {status.rows !== undefined && status.rows !== null ? (
                              <span>rows: {fmtNum(status.rows)}</span>
                            ) : null}
                            <span>source: {status.primary_source || "—"}</span>
                          </div>
                          {!status.present ? (
                            <div className="mt-1.5 flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => void runBackfill(status.bucket)}
                                disabled={isBackfilling}
                                className="inline-flex items-center gap-1 rounded border border-amber-500/40 bg-background px-2 py-0.5 text-[10px] font-medium text-amber-800 hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-300"
                              >
                                {isBackfilling ? (
                                  <Loader2 className="h-3 w-3 animate-spin" />
                                ) : (
                                  <Wand2 className="h-3 w-3" />
                                )}
                                {isBackfilling ? "Backfilling…" : "Backfill this bucket"}
                              </button>
                              {progress && "error" in progress ? (
                                <span className="text-[10px] text-destructive">error: {progress.error}</span>
                              ) : progress ? (
                                <span className="text-[10px] text-emerald-700 dark:text-emerald-300">
                                  {progress.status} — {progress.rows_written} row(s)
                                </span>
                              ) : null}
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="px-4 pt-3 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
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

              <div className="p-4">
                {Number(day[ROWS_KEY[underlying]] ?? 0) === 0 ? (
                  <p className="text-[11px] text-muted-foreground">
                    No {underlying} data recorded on {day.date}.
                  </p>
                ) : loading ? (
                  <p className="text-[11px] text-muted-foreground">Loading {underlying} bars…</p>
                ) : bars.length === 0 ? (
                  indexTapeMissing ? (
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-[11px] text-amber-800 dark:text-amber-300">
                      <p>
                        {underlying} ticks were recorded this day, but the canonical{" "}
                        {BUCKET_DISPLAY_NAMES[indexTapeBucket!]} bucket hasn't been backfilled — that's why no
                        candles render below. Use "Backfill this bucket" above to derive it.
                      </p>
                    </div>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">
                      {error || `No ${underlying} bars returned for ${day.date}.`}
                    </p>
                  )
                ) : (
                  <div className="space-y-4">
                    {summary ? (
                      <div className="grid grid-cols-3 gap-2 text-xs">
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

                    <div className="rounded-lg border bg-background/60 p-2">
                      <CandlestickChart data={priceBars} height={320} />
                    </div>
                  </div>
                )}
              </div>
            </div>
          </>
        ) : null}
      </aside>
    </>
  );
}
