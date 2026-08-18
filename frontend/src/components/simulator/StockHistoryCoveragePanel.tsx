/**
 * Stock-history coverage panel — bucket-availability gate for the
 * stock_simulator, with two views:
 *
 *   - **Week** (default) — 5 trading days × N buckets as a tabular
 *     heatmap, one row per bucket. Click a cell to inspect /
 *     backfill. This is the original view, untouched in spirit.
 *   - **Year** — GitHub-contribution-style 7 × 52 grid covering the
 *     past 365 days. Each day is emerald if all selected buckets
 *     are present (AND semantics), muted otherwise. Click a day
 *     to drill into the week view at that date.
 *
 * Both views share a single filter dropdown: a button in the toolbar
 * opens a popover with a search input, a checkbox list of buckets,
 * and Select all / Clear / NIFTY-50 live preset actions at the
 * footer. Toggling a checkbox is instant (no Apply required);
 * closing the popover just hides it. By default every bucket is
 * selected so the panel is a no-op for users who never open the
 * filter.
 *
 * Reuses the project conventions from SimulatorReplayCalendar
 * (useEffect, lucide-react icons, cn() helper, @/lib/api client).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Calendar,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronsLeft,
  ChevronsRight,
  LayoutGrid,
  Loader2,
  RefreshCw,
  Search,
  Wand2,
  X,
} from "lucide-react";
import { cn, localIsoDate } from "@/lib/utils";
import {
  type HubStockHistoryBackfillRequest,
  type HubStockHistoryBackfillResponse,
  type HubStockHistoryBackfillResult,
  type HubStockHistoryCoverageBucketStatus,
  type HubStockHistoryCoverageDay,
  type HubStockHistoryCoverageResponse,
  api,
} from "@/lib/api";

const DAY_MS = 24 * 60 * 60 * 1000;
const YEAR_WEEKS = 52;

function mondayOf(d: Date): Date {
  const out = new Date(d);
  const js = out.getDay();
  const backToMon = (js + 6) % 7;
  out.setDate(out.getDate() - backToMon);
  out.setHours(0, 0, 0, 0);
  return out;
}

const isoDate = localIsoDate;

function addDays(d: Date, n: number): Date {
  return new Date(d.getTime() + n * DAY_MS);
}

function parseIsoDate(iso: string): Date {
  return new Date(iso + "T00:00:00");
}

interface SelectedCell {
  day: string;
  bucket: string;
  status: HubStockHistoryCoverageBucketStatus;
}

interface BackfillProgress {
  bucket: string;
  status: "running" | "done" | "error";
  result?: HubStockHistoryBackfillResult;
  error?: string;
}

/** Per-key macro / valuation / rate rows have long internal names
 *  (e.g. `valuation_nifty_pe`); show a short human label in the chip
 *  and cell, while keeping the long id in the data-row attribute so
 *  the backfill click still targets the right bucket name. */
const SHORT_LABEL: Record<string, string> = {
  macro_oil_brent: "Oil (Brent)",
  macro_oil_wti: "Oil (WTI)",
  macro_usd_inr: "USD/INR",
  macro_gold: "Gold",
  macro_sp500: "S&P 500",
  macro_india_vix: "India VIX",
  valuation_nifty_pe: "NIFTY PE",
  valuation_nifty_pb: "NIFTY PB",
  valuation_nifty_dy: "NIFTY DivY",
  rate_repo_rate: "Repo",
  rate_india_10y: "India 10Y",
  rate_india_91d_tbill: "India 91d",
  index_tape_nifty: "NIFTY 1-min",
  index_tape_banknifty: "BANKNIFTY 1-min",
  index_tape_sensex: "SENSEX 1-min",
  option_chain_nifty: "NIFTY chain",
  option_chain_banknifty: "BANKNIFTY chain",
  option_chain_sensex: "SENSEX chain",
  constituents: "Const.",
  constituent_ohlcv: "Const. OHLCV",
  equity_ohlcv: "All-50 equities",
  corporate_actions: "Corp. actions",
  intraday_5min: "5-min intraday",
  news_events: "News",
  ticks_daily: "Timescale ticks",
};

/** NIFTY-50 "live" preset — the buckets a typical NIFTY-50 day-trader
 *  actually watches. Used by the "NIFTY-50 live" pill in the
 *  dropdown footer. */
const NIFTY50_LIVE_PRESET: ReadonlySet<string> = new Set([
  "index_tape_nifty",
  "option_chain_nifty",
  "equity_ohlcv",
  "constituent_ohlcv",
  "macro_usd_inr",
  "macro_india_vix",
  "rate_repo_rate",
  "intraday_5min",
  "news_events",
]);

/** First-paint default — one representative bucket per recording
 *  pillar (index tape, option chain, daily OHLCV history, the macro
 *  factor umbrella, equities, news) instead of every individual
 *  factor-key bucket. The panel now tracks each of the ~90 ML factor
 *  keys as its own bucket so nothing is silently unrecorded, but
 *  showing all of them on first paint would be unreadable — everything
 *  beyond this set is one search/toggle away in the filter bar. */
const DEFAULT_VISIBLE_PRESET: ReadonlySet<string> = new Set([
  "index_tape_nifty",
  "option_chain_nifty",
  "nifty_ohlcv_daily",
  "macro_factors",
  "equity_ohlcv",
  "news_events",
]);

function bucketLabel(bucket: string): string {
  return SHORT_LABEL[bucket] ?? bucket;
}

type ViewMode = "week" | "year";

export interface StockHistoryCoveragePanelProps {
  /** Initial week start (any date in the week); defaults to this week. */
  initialWeekStart?: string;
  /** When true, the panel fetches with include_optional=1. */
  includeOptional?: boolean;
  /** Symbol label (default NIFTY). */
  symbol?: string;
  /** Extra classes for the outer wrapper. */
  className?: string;
}

export function StockHistoryCoveragePanel({
  initialWeekStart,
  includeOptional = false,
  symbol = "NIFTY",
  className,
}: StockHistoryCoveragePanelProps) {
  const [view, setView] = useState<ViewMode>("week");
  const [weekStart, setWeekStart] = useState<Date>(() => {
    if (initialWeekStart) return mondayOf(parseIsoDate(initialWeekStart));
    return mondayOf(new Date());
  });
  const [report, setReport] = useState<HubStockHistoryCoverageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedCell | null>(null);
  const [backfill, setBackfill] = useState<BackfillProgress | null>(null);

  // Year-view state: a window-end date and a stitched coverage response
  // covering that date's preceding 52 weeks. We fetch each week in
  // parallel (small JSON responses, ~26 buckets × 5 days).
  const [yearEnd, setYearEnd] = useState<string>(() => isoDate(new Date()));
  const [yearReport, setYearReport] = useState<HubStockHistoryCoverageResponse | null>(null);
  const [yearLoading, setYearLoading] = useState(false);
  const [yearError, setYearError] = useState<string | null>(null);

  // Committed date range from the picker. `null` = no range picked
  // yet (the chevron buttons still work for incremental paging).
  // When set, week view jumps to the start of the range; year view
  // jumps to the year containing the range's end.
  const [dateRange, setDateRange] = useState<{ start: string; end: string } | null>(
    null,
  );

  // Filter state. `null` means "show every bucket" (the default); a
  // Set means "show only these". AND semantics: a cell is "present"
  // only when every selected bucket is present on that day. The same
  // filter applies to both week and year views.
  const [bucketFilter, setBucketFilter] = useState<Set<string> | null>(null);

  const weekStartIso = isoDate(weekStart);

  const fetchCoverage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getHubStockHistoryCoverage({
        week: weekStartIso,
        symbol,
        include_optional: includeOptional,
      });
      if (resp.status !== "ok") {
        setError(resp.error ?? "coverage request failed");
        setReport(null);
      } else {
        setReport(resp);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, [weekStartIso, symbol, includeOptional]);

  useEffect(() => {
    void fetchCoverage();
  }, [fetchCoverage]);

  // Year fetch: 52 weeks ending on `yearEnd`, fetched in parallel.
  // Failures on individual weeks degrade gracefully — we still show
  // the rest of the grid, the failed week renders as "no data".
  const fetchYear = useCallback(async () => {
    setYearLoading(true);
    setYearError(null);
    const end = parseIsoDate(yearEnd);
    const mondayEnd = mondayOf(end);
    const mondayStart = addDays(mondayEnd, -7 * (YEAR_WEEKS - 1));
    // Build the list of week-start ISO dates (Mon..Mon span).
    const weekStarts: string[] = [];
    for (let i = 0; i < YEAR_WEEKS; i++) {
      weekStarts.push(isoDate(addDays(mondayStart, i * 7)));
    }
    const settled = await Promise.allSettled(
      weekStarts.map((week) =>
        api.getHubStockHistoryCoverage({
          week,
          symbol,
          include_optional: includeOptional,
        }),
      ),
    );
    let firstError: string | null = null;
    const allDays: HubStockHistoryCoverageDay[] = [];
    const allBucketLabels = new Set<string>();
    let isComplete = true;
    const missingDays = new Set<string>();
    for (let i = 0; i < settled.length; i++) {
      const r = settled[i];
      if (r.status === "rejected") {
        if (!firstError) firstError = String(r.reason);
        // Mark this whole week as "no data" — we'll skip these days in
        // the grid by leaving them out of `allDays`.
        continue;
      }
      const resp = r.value;
      if (resp.status !== "ok") {
        if (!firstError) firstError = resp.error ?? "coverage request failed";
        continue;
      }
      for (const d of resp.days) {
        allDays.push(d);
        if (!d.is_complete) {
          isComplete = false;
          for (const _missing of d.missing) missingDays.add(d.day);
        }
        for (const b of Object.keys(d.buckets)) allBucketLabels.add(b);
      }
    }
    // Sort by date ascending so the heatmap renders left-to-right
    // without re-sorting.
    allDays.sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : 0));
    if (firstError && allDays.length === 0) {
      setYearError(firstError);
      setYearReport(null);
    } else {
      setYearReport({
        status: "ok",
        week_start: isoDate(mondayStart),
        week_end: isoDate(addDays(mondayStart, 7 * YEAR_WEEKS - 1)),
        symbol,
        is_complete: isComplete && !firstError,
        missing_days: [...missingDays],
        bucket_labels: [...allBucketLabels].sort(),
        days: allDays,
        fetch_list: [],
      });
      if (firstError) setYearError(firstError);
    }
    setYearLoading(false);
  }, [yearEnd, symbol, includeOptional]);

  useEffect(() => {
    if (view !== "year") return;
    void fetchYear();
  }, [view, fetchYear]);

  // Default the filter to "all buckets selected" once we know the
  // universe, so the panel renders identically to today on first
  // paint. Uses whichever view's report landed first.
  const allBuckets = useMemo(() => {
    const labels = report?.bucket_labels ?? yearReport?.bucket_labels ?? [];
    return labels;
  }, [report, yearReport]);
  const totalBuckets = allBuckets.length;

  useEffect(() => {
    if (bucketFilter !== null) return;
    if (totalBuckets === 0) return;
    const defaults = allBuckets.filter((b) => DEFAULT_VISIBLE_PRESET.has(b));
    // Fall back to "everything" if none of the default pillars exist in
    // this deployment's bucket registry yet, so the panel never opens
    // to an empty grid.
    setBucketFilter(new Set(defaults.length > 0 ? defaults : allBuckets));
  }, [allBuckets, totalBuckets, bucketFilter]);

  const selectedBucketsList = useMemo(() => {
    if (bucketFilter === null) return allBuckets;
    return allBuckets.filter((b) => bucketFilter.has(b));
  }, [allBuckets, bucketFilter]);

  const onBackfill = useCallback(
    async (bucket: string) => {
      const req: HubStockHistoryBackfillRequest = {
        week: weekStartIso,
        symbol,
        include_optional: includeOptional,
        buckets: [bucket],
        verify_after: true,
      };
      setBackfill({ bucket, status: "running" });
      try {
        const resp: HubStockHistoryBackfillResponse =
          await api.postHubStockHistoryBackfill(req);
        if (resp.status !== "ok") {
          setBackfill({ bucket, status: "error", error: resp.error ?? "backfill failed" });
          return;
        }
        const result = resp.summary.results.find((r) => r.bucket === bucket);
        setBackfill({
          bucket,
          status: result?.had_errors ? "error" : "done",
          result,
        });
        if (resp.coverage_after) {
          setReport(resp.coverage_after);
        } else {
          await fetchCoverage();
        }
        // Year view shows stale state until the next manual refresh —
        // a year re-fetch here would block the UI for too long, so we
        // just nudge the user with a hint in the footer instead.
      } catch (e) {
        setBackfill({
          bucket,
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },
    [weekStartIso, symbol, includeOptional, fetchCoverage],
  );

  const shiftWeek = useCallback((deltaDays: number) => {
    setWeekStart((prev) => addDays(prev, deltaDays));
  }, []);

  const shiftYear = useCallback((deltaDays: number) => {
    setYearEnd((prev) => isoDate(addDays(parseIsoDate(prev), deltaDays)));
  }, []);

  // Picker commit: snap whichever view is active to the picked
  // range. Week view shows one ISO week — anchor on Monday of the
  // range's start. Year view keeps a 52-week window ending on the
  // range's end (so the window contains the entire picked span).
  const handleDateRangeChange = useCallback(
    (range: { start: string; end: string }) => {
      setDateRange(range);
      setWeekStart(mondayOf(parseIsoDate(range.start)));
      setYearEnd(range.end);
    },
    [],
  );

  const displayWeekStart = report?.week_start ?? weekStartIso;
  const displayWeekEnd = report?.week_end ?? isoDate(addDays(weekStart, 4));

  // The set of buckets the user has actively checked in the dropdown.
  // `null` means "show every bucket" (the all-default). The "AND"
  // predicate is: a day is present iff every selected bucket has
  // status.present === true on that day.
  const isAllSelected = bucketFilter === null;
  const effectiveSelected =
    bucketFilter === null ? new Set(allBuckets) : bucketFilter;

  const toggleBucket = useCallback(
    (bucket: string) => {
      setBucketFilter((prev) => {
        const base = prev ?? new Set(allBuckets);
        const next = new Set(base);
        if (next.has(bucket)) next.delete(bucket);
        else next.add(bucket);
        if (next.size === allBuckets.length) return null;
        return next;
      });
    },
    [allBuckets],
  );

  const selectAll = useCallback(() => {
    setBucketFilter(null);
  }, []);

  const clearAll = useCallback(() => {
    setBucketFilter(new Set());
  }, []);

  const applyNifty50LivePreset = useCallback(() => {
    const intersection = allBuckets.filter((b) => NIFTY50_LIVE_PRESET.has(b));
    setBucketFilter(new Set(intersection));
  }, [allBuckets]);

  // In year view, clicking a cell drills into the week view at that
  // date — the natural "I want to backfill this missing bucket"
  // follow-up action lives there.
  const onYearCellClick = useCallback(
    (dayKey: string) => {
      setView("week");
      setWeekStart(mondayOf(parseIsoDate(dayKey)));
      setSelected(null);
    },
    [],
  );

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {/* Toolbar — view toggle, week-nav, filter dropdown, refresh */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {view === "week" ? (
            <>
              <button
                type="button"
                aria-label="Previous week"
                onClick={() => shiftWeek(-7)}
                className="rounded-lg border bg-background px-2.5 py-1.5 text-sm transition-colors hover:bg-muted disabled:opacity-50"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <DateRangePicker
                label={`${displayWeekStart} — ${displayWeekEnd}`}
                windowStart={displayWeekStart}
                range={dateRange}
                onRangeChange={handleDateRangeChange}
              />
              <button
                type="button"
                aria-label="Next week"
                onClick={() => shiftWeek(7)}
                className="rounded-lg border bg-background px-2.5 py-1.5 text-sm transition-colors hover:bg-muted disabled:opacity-50"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                aria-label="Previous year"
                onClick={() => shiftYear(-7 * YEAR_WEEKS)}
                className="rounded-lg border bg-background px-2.5 py-1.5 text-sm transition-all hover:bg-muted active:scale-95 disabled:opacity-50"
              >
                <ChevronsLeft className="h-4 w-4" />
              </button>
              <DateRangePicker
                label={`${
                  yearReport?.week_start ??
                  isoDate(addDays(parseIsoDate(yearEnd), -7 * (YEAR_WEEKS - 1)))
                } — ${yearReport?.week_end ?? yearEnd}`}
                windowStart={
                  yearReport?.week_start ??
                  isoDate(addDays(parseIsoDate(yearEnd), -7 * (YEAR_WEEKS - 1)))
                }
                range={dateRange}
                onRangeChange={handleDateRangeChange}
              />
              <span
                className="rounded-md bg-muted/60 px-2 py-1 font-mono text-[10px] text-muted-foreground"
                title="Calendar year shown at the right edge of the window"
              >
                yr {parseIsoDate(yearEnd).getFullYear()}
              </span>
              <button
                type="button"
                aria-label="Next year"
                onClick={() => shiftYear(7 * YEAR_WEEKS)}
                className="rounded-lg border bg-background px-2.5 py-1.5 text-sm transition-all hover:bg-muted active:scale-95 disabled:opacity-50"
              >
                <ChevronsRight className="h-4 w-4" />
              </button>
            </>
          )}

          {/* View toggle */}
          <div className="ml-2 inline-flex overflow-hidden rounded-lg border bg-background">
            <button
              type="button"
              onClick={() => setView("week")}
              aria-pressed={view === "week"}
              title="Week view"
              className={cn(
                "inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium transition-colors",
                view === "week"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              <CalendarDays className="h-3.5 w-3.5" />
              Week
            </button>
            <button
              type="button"
              onClick={() => setView("year")}
              aria-pressed={view === "year"}
              title="Year view"
              className={cn(
                "inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium transition-colors",
                view === "year"
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              <LayoutGrid className="h-3.5 w-3.5" />
              Year
            </button>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs">
          {totalBuckets > 0 && (
            <BucketFilterDropdown
              buckets={allBuckets}
              selected={effectiveSelected}
              isAllSelected={isAllSelected}
              onToggle={toggleBucket}
              onSelectAll={selectAll}
              onClearAll={clearAll}
              onApplyNifty50LivePreset={applyNifty50LivePreset}
            />
          )}
          {report && view === "week" && (
            <span
              className={cn(
                "rounded-full px-2.5 py-1 font-medium",
                report.is_complete
                  ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                  : "bg-rose-500/15 text-rose-700 dark:text-rose-300",
              )}
            >
              {report.is_complete
                ? "Week fully covered"
                : `${report.missing_days.length} day(s) incomplete`}
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              if (view === "week") void fetchCoverage();
              else void fetchYear();
            }}
            disabled={view === "week" ? loading : yearLoading}
            className="rounded-lg border bg-background px-2.5 py-1.5 transition-colors hover:bg-muted disabled:opacity-50"
            aria-label="Refresh coverage"
          >
            <RefreshCw
              className={cn(
                "h-4 w-4",
                (view === "week" ? loading : yearLoading) && "animate-spin",
              )}
            />
          </button>
        </div>
      </div>

      {(view === "week" ? error : yearError) && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
          {(view === "week" ? error : yearError)}
        </div>
      )}

      {view === "week" ? (
        <>
          <CoverageHeatmap
            report={report}
            loading={loading}
            selectedBuckets={effectiveSelected}
            isAllSelected={isAllSelected}
            onCellClick={(day, bucket, status) =>
              setSelected({ day, bucket, status })
            }
            selected={selected}
          />
          <CellDrawer
            selected={selected}
            onClose={() => setSelected(null)}
            onBackfill={(bucket) => void onBackfill(bucket)}
            backfill={backfill}
          />
        </>
      ) : (
        <>
          <YearHeatmap
            report={yearReport}
            loading={yearLoading}
            selectedBuckets={effectiveSelected}
            isAllSelected={isAllSelected}
            onCellClick={onYearCellClick}
          />
          {selectedBucketsList.length === 0 && (
            <p className="rounded-lg border bg-card/50 px-3 py-2 text-[11px] text-muted-foreground shadow-sm">
              No buckets selected — every day will look empty. Open the
              filter dropdown above and pick at least one bucket.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// ============================================================
// Date range picker — click the trigger, a month-grid popover
// opens with year/month nav. Two-click range selection (click
// start, click end) matches the calendar grid below. Click-outside
// or Escape closes the popover without losing the anchor day, so a
// user can pick a start in January then navigate forward in months
// before picking the end.
// ============================================================

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

interface DateRangePickerProps {
  /** Display label shown on the trigger (e.g. "2026-08-18 — 2026-08-22"). */
  label: string;
  /** Currently visible window start (drives which month the popover opens to). */
  windowStart: string;
  /** Currently committed range, if any. */
  range: { start: string; end: string } | null;
  /** Called when the user completes a two-click range selection. */
  onRangeChange: (range: { start: string; end: string }) => void;
  /** Called when the user clicks the trigger to open the popover. */
  onOpen?: () => void;
}

function DateRangePicker({
  label,
  windowStart,
  range,
  onRangeChange,
  onOpen,
}: DateRangePickerProps) {
  const [open, setOpen] = useState(false);
  // The popover's currently-visible month. Initialised to the
  // windowStart so opening the picker always lands on a relevant
  // month, not whatever today happens to be.
  const [viewMonth, setViewMonth] = useState<Date>(() => parseIsoDate(windowStart));
  // Pending range start (set on first click, cleared on second click
  // once the range is committed).
  const [pendingStart, setPendingStart] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Reset pending state when the popover closes (a half-picked range
  // shouldn't stick around between sessions of use).
  useEffect(() => {
    if (!open) setPendingStart(null);
  }, [open]);

  // Click-outside / Escape close.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const handleOpen = () => {
    setOpen(true);
    // Anchor the popover's view-month to the current window so the
    // user lands somewhere meaningful, not on a stale month.
    setViewMonth(parseIsoDate(windowStart));
    onOpen?.();
  };

  const shiftMonth = (deltaMonths: number) => {
    setViewMonth((d) => {
      const next = new Date(d);
      next.setMonth(next.getMonth() + deltaMonths);
      return next;
    });
  };

  const shiftYear = (deltaYears: number) => {
    setViewMonth((d) => {
      const next = new Date(d);
      next.setFullYear(d.getFullYear() + deltaYears);
      return next;
    });
  };

  const today = new Date();
  const todayIso = isoDate(today);
  // Build the 6×7 grid for the visible month. Start at the Sunday on
  // or before the 1st of the month so the grid is always a rectangle.
  const monthStart = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), 1);
  const gridStart = addDays(monthStart, -monthStart.getDay());
  const cells: Date[] = [];
  for (let i = 0; i < 42; i++) cells.push(addDays(gridStart, i));

  const handleCellClick = (cell: Date) => {
    const key = isoDate(cell);
    // First click: anchor the start, no end yet.
    if (pendingStart === null) {
      setPendingStart(key);
      return;
    }
    // Second click: commit the range, normalised so start ≤ end.
    const start = key < pendingStart ? key : pendingStart;
    const end = key < pendingStart ? pendingStart : key;
    setPendingStart(null);
    onRangeChange({ start, end });
    setOpen(false);
  };

  // Cell highlight helpers.
  const inRange = (cellIso: string): boolean => {
    if (pendingStart && range === null) {
      return cellIso === pendingStart;
    }
    if (range) {
      return cellIso >= range.start && cellIso <= range.end;
    }
    return false;
  };
  const isRangeEdge = (cellIso: string): boolean => {
    if (pendingStart) return cellIso === pendingStart;
    if (range) return cellIso === range.start || cellIso === range.end;
    return false;
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={handleOpen}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 font-mono text-sm transition-colors",
          open
            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : "border-border bg-background/60 hover:bg-muted",
        )}
      >
        <Calendar className="h-3.5 w-3.5" />
        {label}
        <ChevronDown
          className={cn("h-3 w-3 transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Pick a date range"
          className="absolute left-0 top-full z-50 mt-2 w-[280px] rounded-lg border bg-popover p-3 shadow-lg"
        >
          {/* Month / year header with prev/next buttons. The up/down
              chevrons jump by year; the left/right chevrons jump by
              month — same pattern most OS date pickers use. */}
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                onClick={() => shiftYear(-1)}
                aria-label="Previous year"
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => shiftMonth(-1)}
                aria-label="Previous month"
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="font-medium text-foreground">
              {MONTH_NAMES[viewMonth.getMonth()]} {viewMonth.getFullYear()}
            </div>
            <div className="flex items-center gap-0.5">
              <button
                type="button"
                onClick={() => shiftMonth(1)}
                aria-label="Next month"
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => shiftYear(1)}
                aria-label="Next year"
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {/* Weekday header row — Mon..Sun to match the rest of the
              project's date UI (the calendar grid below also shows
              Mon/Wed/Fri on the left). */}
          <div className="grid grid-cols-7 gap-1 text-center text-[10px] font-medium text-muted-foreground">
            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
              <div key={d}>{d}</div>
            ))}
          </div>

          {/* Day grid. Days outside the visible month are dimmed so
              the rectangle stays clean; today gets a subtle ring so
              the user can find "now" at a glance. */}
          <div className="mt-1 grid grid-cols-7 gap-1">
            {cells.map((cell) => {
              const inMonth = cell.getMonth() === viewMonth.getMonth();
              const key = isoDate(cell);
              const future = cell > today;
              const isToday = key === todayIso;
              const edge = isRangeEdge(key);
              const ranged = inRange(key);
              return (
                <button
                  key={key}
                  type="button"
                  disabled={future}
                  onClick={() => handleCellClick(cell)}
                  className={cn(
                    "h-7 w-7 rounded-md font-mono text-[11px] transition-colors",
                    !inMonth && "text-muted-foreground/40",
                    inMonth && !ranged && "hover:bg-muted",
                    future && "cursor-not-allowed opacity-30",
                    ranged && !edge && "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
                    edge && "bg-emerald-500 text-white",
                    isToday && !edge && "ring-1 ring-amber-400/60",
                    pendingStart && key === pendingStart && "ring-2 ring-emerald-500",
                  )}
                  title={key}
                >
                  {cell.getDate()}
                </button>
              );
            })}
          </div>

          {/* Footer hint — tells the user what the next click will do.
              Resets after they commit a range (pendingStart null). */}
          <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
            <span>
              {pendingStart
                ? `Start: ${pendingStart} — click an end day.`
                : range
                ? `Range: ${range.start} → ${range.end}`
                : "Click a start day, then an end day."}
            </span>
            {pendingStart ? (
              <button
                type="button"
                onClick={() => setPendingStart(null)}
                className="rounded px-1.5 py-0.5 hover:bg-muted"
              >
                Reset
              </button>
            ) : range ? (
              <button
                type="button"
                onClick={() => {
                  setPendingStart(range.start);
                }}
                className="rounded px-1.5 py-0.5 hover:bg-muted"
              >
                Edit
              </button>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// Filter dropdown — single trigger, click → popover with search +
// checkbox list + Select all / Clear / NIFTY-50 live at the footer.
// ============================================================

interface BucketFilterDropdownProps {
  buckets: string[];
  selected: Set<string>;
  isAllSelected: boolean;
  onToggle: (bucket: string) => void;
  onSelectAll: () => void;
  onClearAll: () => void;
  onApplyNifty50LivePreset: () => void;
}

function BucketFilterDropdown({
  buckets,
  selected,
  isAllSelected,
  onToggle,
  onSelectAll,
  onClearAll,
  onApplyNifty50LivePreset,
}: BucketFilterDropdownProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const containerRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  // Close on click-outside; focus the search box when opened.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onEsc);
    // Focus search after the popover mounts.
    requestAnimationFrame(() => searchInputRef.current?.focus());
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return buckets;
    return buckets.filter(
      (b) =>
        b.toLowerCase().includes(q) ||
        bucketLabel(b).toLowerCase().includes(q),
    );
  }, [buckets, query]);

  const selectedCount = selected.size;
  const total = buckets.length;
  // Trigger label summarises the selection; if the user picked the
  // "NIFTY-50 live" preset, name it explicitly so they know what they
  // filtered to.
  const triggerLabel = (() => {
    if (selectedCount === 0) return "No buckets";
    if (isAllSelected) return "All buckets";
    if (
      [...NIFTY50_LIVE_PRESET].every((b) => selected.has(b)) &&
      selected.size <= NIFTY50_LIVE_PRESET.size
    )
      return "NIFTY-50 live";
    if (selectedCount === 1) return `${selectedCount} bucket`;
    return `${selectedCount} buckets`;
  })();

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-medium transition-colors",
          open
            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
            : "border-border bg-background text-foreground hover:bg-muted",
        )}
      >
        <Search className="h-3 w-3" />
        {triggerLabel}
        <span className="rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {selectedCount}/{total}
        </span>
        <ChevronDown
          className={cn(
            "h-3 w-3 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-multiselectable="true"
          className="absolute right-0 top-full z-50 mt-2 w-[280px] rounded-lg border bg-popover shadow-lg"
        >
          <div className="border-b p-2">
            <div className="relative flex items-center">
              <Search className="pointer-events-none absolute left-2 h-3 w-3 text-muted-foreground" />
              <input
                ref={searchInputRef}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search buckets…"
                aria-label="Search buckets"
                className="w-full rounded-md border bg-background py-1.5 pl-7 pr-7 text-xs placeholder:text-muted-foreground/70 focus:outline-none focus:ring-2 focus:ring-emerald-500/40"
              />
              {query ? (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  className="absolute right-2 rounded-full p-0.5 text-muted-foreground hover:bg-muted"
                >
                  <X className="h-3 w-3" />
                </button>
              ) : null}
            </div>
          </div>

          <div className="max-h-64 overflow-y-auto p-1">
            {filtered.length === 0 ? (
              <p className="px-2 py-2 text-[11px] text-muted-foreground">
                No buckets match “{query}”.
              </p>
            ) : (
              filtered.map((bucket) => {
                const checked = selected.has(bucket);
                return (
                  <button
                    key={bucket}
                    type="button"
                    role="option"
                    aria-selected={checked}
                    onClick={() => onToggle(bucket)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] transition-colors",
                      checked
                        ? "bg-emerald-500/10 text-foreground"
                        : "text-muted-foreground hover:bg-muted",
                    )}
                  >
                    <span
                      className={cn(
                        "inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border",
                        checked
                          ? "border-emerald-500/60 bg-emerald-500/80 text-white"
                          : "border-border bg-background",
                      )}
                    >
                      {checked ? <Check className="h-2.5 w-2.5" /> : null}
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">
                        {bucketLabel(bucket)}
                      </span>
                      <span className="truncate font-mono text-[10px] text-muted-foreground">
                        {bucket}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>

          <div className="flex items-center justify-between gap-1 border-t bg-muted/30 p-1.5">
            <button
              type="button"
              onClick={onSelectAll}
              disabled={isAllSelected}
              className="rounded-md px-2 py-1 text-[11px] font-medium hover:bg-background disabled:opacity-40"
            >
              All
            </button>
            <button
              type="button"
              onClick={onClearAll}
              disabled={selected.size === 0}
              className="rounded-md px-2 py-1 text-[11px] font-medium hover:bg-background disabled:opacity-40"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={onApplyNifty50LivePreset}
              className="rounded-md px-2 py-1 text-[11px] font-medium hover:bg-background"
              title="Pick the buckets a NIFTY-50 day-trader usually watches"
            >
              NIFTY-50 live
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// Week heatmap (5 days × N buckets)
// ============================================================

interface HeatmapProps {
  report: HubStockHistoryCoverageResponse | null;
  loading: boolean;
  selectedBuckets: Set<string>;
  isAllSelected: boolean;
  selected: SelectedCell | null;
  onCellClick: (
    day: string,
    bucket: string,
    status: HubStockHistoryCoverageBucketStatus,
  ) => void;
}

function CoverageHeatmap({
  report,
  loading,
  selectedBuckets,
  isAllSelected,
  selected,
  onCellClick,
}: HeatmapProps) {
  if (loading && !report) {
    return (
      <div className="flex items-center gap-2 rounded-xl border bg-card/50 px-3 py-8 text-xs text-muted-foreground shadow-sm">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading coverage…
      </div>
    );
  }
  if (!report || report.bucket_labels.length === 0 || report.days.length === 0) {
    return (
      <div className="rounded-xl border bg-card/50 px-3 py-8 text-center text-xs text-muted-foreground shadow-sm">
        No coverage data for this week.
      </div>
    );
  }

  // Week view now hides unselected rows: with ~90 factor-key buckets
  // in the registry, rendering every row by default is unreadable.
  // `bucketFilter`'s default is a curated 5-6 bucket preset
  // (`DEFAULT_VISIBLE_PRESET`); the dropdown's search/checkboxes are
  // how a user brings any of the other ~85 buckets into view.
  const labels = isAllSelected
    ? report.bucket_labels
    : report.bucket_labels.filter((b) => selectedBuckets.has(b));
  if (labels.length === 0) {
    return (
      <div className="rounded-xl border bg-card/50 px-3 py-8 text-center text-xs text-muted-foreground shadow-sm">
        No buckets selected — use the filter above to pick one, or "All" to see everything.
      </div>
    );
  }

  // For each day, compute a synthetic boolean "is the AND of selected
  // buckets present?". Used to colour the bucket label cell on the
  // left so the user can see at a glance which days are "fully green"
  // for their selection, vs partially green vs all-missing.
  const dayPassesFilter = (day: HubStockHistoryCoverageDay): boolean | null => {
    if (selectedBuckets.size === 0) return null; // no selection — show real per-row state
    const buckets = Object.values(day.buckets);
    for (const wanted of selectedBuckets) {
      const status = buckets.find((s) => s.bucket === wanted);
      if (!status || !status.present) return false;
    }
    return true;
  };

  const labelWidth =
    Math.max(...labels.map((l) => bucketLabel(l).length)) + 2;

  return (
    <div className="overflow-x-auto rounded-xl border bg-card/50 shadow-sm">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            <th
              className="border-b border-border bg-muted/40 px-3 py-2 text-left font-medium text-muted-foreground"
              style={{ minWidth: `${labelWidth}ch` }}
            >
              bucket
            </th>
            {report.days.map((d) => {
              const passes = dayPassesFilter(d);
              return (
                <th
                  key={d.day}
                  className={cn(
                    "border-b border-border bg-muted/40 px-3 py-2 text-center font-mono font-medium text-muted-foreground",
                    passes === true && "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
                    passes === false && "bg-rose-500/10 text-rose-700 dark:text-rose-300",
                  )}
                  style={{ minWidth: "5rem" }}
                  title={
                    passes === null
                      ? undefined
                      : passes
                      ? "All selected buckets present"
                      : "At least one selected bucket is missing"
                  }
                >
                  {d.day.slice(5)}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {labels.map((bucket) => {
            // Highlight rows for selected buckets so the user can see
            // what they filtered to. Unselected buckets stay muted but
            // remain visible — the cell still shows OK/X so the user
            // can tell whether they "missed" a bucket that has data.
            const rowIsSelected = isAllSelected || selectedBuckets.has(bucket);
            return (
              <tr key={bucket} data-bucket={bucket}>
                <td
                  className={cn(
                    "border-b border-border bg-card px-3 py-2 font-mono",
                    rowIsSelected
                      ? "text-foreground"
                      : "text-muted-foreground/60",
                  )}
                  title={bucket}
                >
                  {bucketLabel(bucket)}
                </td>
                {report.days.map((day) => (
                  <CoverageCell
                    key={`${bucket}-${day.day}`}
                    bucket={bucket}
                    day={day}
                    selected={selected}
                    onClick={onCellClick}
                    dim={!rowIsSelected}
                  />
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CoverageCell({
  bucket,
  day,
  selected,
  onClick,
  dim,
}: {
  bucket: string;
  day: HubStockHistoryCoverageDay;
  selected: SelectedCell | null;
  onClick: (
    day: string,
    bucket: string,
    status: HubStockHistoryCoverageBucketStatus,
  ) => void;
  dim: boolean;
}) {
  const status = day.buckets[bucket];
  if (!status) {
    return (
      <td
        className={cn(
          "border-b border-border bg-card px-2 py-2",
          dim && "opacity-50",
        )}
      />
    );
  }
  const isSelected =
    selected !== null &&
    selected.day === day.day &&
    selected.bucket === bucket;

  return (
    <td
      className={cn(
        "border-b border-border px-2 py-2 text-center font-mono transition-shadow",
        status.present
          ? "bg-emerald-500/10 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
          : "bg-card text-muted-foreground hover:bg-muted",
        isSelected && "shadow-[0_0_0_2px_hsl(var(--primary))]",
        dim && "opacity-50",
      )}
      onClick={() => onClick(day.day, bucket, status)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick(day.day, bucket, status);
      }}
      aria-label={`${bucket} on ${day.day}: ${status.present ? "present" : "missing"}`}
    >
      {status.present ? "OK" : "X"}
    </td>
  );
}

// ============================================================
// Year heatmap (GitHub-contribution style: 7 × 52 grid)
// ============================================================

interface YearHeatmapProps {
  report: HubStockHistoryCoverageResponse | null;
  loading: boolean;
  selectedBuckets: Set<string>;
  isAllSelected: boolean;
  onCellClick: (day: string) => void;
}

function YearHeatmap({
  report,
  loading,
  selectedBuckets,
  isAllSelected,
  onCellClick,
}: YearHeatmapProps) {
  if (loading && !report) {
    return (
      <div className="flex items-center gap-2 rounded-xl border bg-card/50 px-3 py-8 text-xs text-muted-foreground shadow-sm">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading year…
      </div>
    );
  }
  if (!report || report.days.length === 0) {
    return (
      <div className="rounded-xl border bg-card/50 px-3 py-8 text-center text-xs text-muted-foreground shadow-sm">
        No coverage data for this year.
      </div>
    );
  }

  // Build a date → buckets lookup, then walk 7 rows × 52 cols.
  const byKey = new Map(report.days.map((d) => [d.day, d]));
  const end = parseIsoDate(report.week_end);
  const jsWeekdayEnd = end.getDay(); // Sun=0..Sat=6
  const startSunday = addDays(end, -jsWeekdayEnd);
  const startMonday = addDays(startSunday, 1); // align grid to Mon..Sun
  // Re-anchor start to Monday of the week containing startSunday.
  const gridStart = addDays(startSunday, -((startSunday.getDay() + 6) % 7));

  const today = new Date();
  const ROW_LABELS = ["", "Mon", "", "Wed", "", "Fri", ""];
  const CELL_PX = 11;
  const CELL_GAP_PX = 3;
  const LABEL_COL_PX = 28;
  const HEADER_ROW_PX = 14;
  const WEEKS = 52;
  const weeks: { date: Date; key: string; day: HubStockHistoryCoverageDay | null }[][] = [];

  // Month-label placement: emit a label on the first column of every
  // new month, mirroring the replay calendar.
  const months: { col: number; label: string }[] = [];
  const seenMonth = new Set<string>();
  let lastLabelCol = -Infinity;
  const MIN_MONTH_LABEL_GAP_COLS = 3;

  for (let w = 0; w < WEEKS; w++) {
    const week: { date: Date; key: string; day: HubStockHistoryCoverageDay | null }[] = [];
    for (let d = 0; d < 7; d++) {
      const cell = addDays(gridStart, w * 7 + d);
      const key = isoDate(cell);
      // Out-of-window days (before Monday or after Sunday of the last week)
      // fall outside the report's coverage span — render them as muted/empty
      // so the grid is a clean rectangle.
      const inWindow = cell >= startMonday && cell <= end;
      const day = inWindow ? byKey.get(key) ?? null : null;
      week.push({ date: cell, key, day });
      if (d === 0) {
        const mkey = `${cell.getFullYear()}-${cell.getMonth()}`;
        if (!seenMonth.has(mkey)) {
          seenMonth.add(mkey);
          if (w - lastLabelCol >= MIN_MONTH_LABEL_GAP_COLS) {
            months.push({
              col: w,
              label: cell.toLocaleString(undefined, { month: "short" }),
            });
            lastLabelCol = w;
          }
        }
      }
    }
    weeks.push(week);
  }

  // AND predicate for a single day. Returns one of three states:
  //   - "empty"  — no selection, or no buckets at all on this day
  //   - "pass"   — every selected bucket is present
  //   - "fail"   — at least one selected bucket is missing
  const dayState = (
    day: HubStockHistoryCoverageDay | null,
  ): "empty" | "pass" | "fail" => {
    if (!day) return "empty";
    if (selectedBuckets.size === 0) return "empty";
    const buckets = Object.values(day.buckets);
    for (const wanted of selectedBuckets) {
      const status = buckets.find((s) => s.bucket === wanted);
      if (!status || !status.present) return "fail";
    }
    return "pass";
  };

  const passCount = report.days.filter(
    (d) => dayState(d) === "pass",
  ).length;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>
          {isAllSelected
            ? "All buckets selected — every day with any data is shown."
            : selectedBuckets.size === 0
            ? "No buckets selected — pick at least one from the filter above."
            : `${passCount} of ${report.days.length} days match all ${
                selectedBuckets.size
              } selected bucket${selectedBuckets.size === 1 ? "" : "s"} (AND).`}
        </span>
        <span className="inline-flex items-center gap-2">
          <span>None</span>
          <span className="h-[10px] w-[10px] rounded-[2px] border border-border/60 bg-rose-500/15" />
          <span className="h-[10px] w-[10px] rounded-[2px] border border-emerald-500/40 bg-emerald-500/40" />
          <span>All match</span>
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border bg-card/50 p-3 shadow-sm">
        <div
          className="grid"
          style={{
            gridTemplateColumns: `${LABEL_COL_PX}px repeat(${weeks.length}, ${CELL_PX}px)`,
            gridTemplateRows: `${HEADER_ROW_PX}px repeat(7, ${CELL_PX}px)`,
            columnGap: `${CELL_GAP_PX}px`,
            rowGap: `${CELL_GAP_PX}px`,
          }}
        >
          {ROW_LABELS.map((label, di) =>
            label ? (
              <div
                key={`wd-${di}`}
                className="text-[9px] leading-none text-muted-foreground/70"
                style={{ gridColumn: 1, gridRow: di + 2, alignSelf: "center" }}
              >
                {label}
              </div>
            ) : null,
          )}
          {months.map((m) => (
            <div
              key={`m-${m.col}`}
              className="shrink-0 text-[9px] leading-none text-muted-foreground/70"
              style={{ gridColumn: m.col + 2, gridRow: 1, alignSelf: "end" }}
            >
              {m.label}
            </div>
          ))}
          {weeks.map((week, wi) =>
            week.map((cell, di) => {
              const state = dayState(cell.day);
              const isFuture = cell.date > today;
              const inWindow =
                cell.date >= startMonday && cell.date <= end;
              return (
                <button
                  key={`${wi}-${di}`}
                  type="button"
                  onClick={() => cell.day && onCellClick(cell.day.day)}
                  disabled={!cell.day || isFuture}
                  style={{ gridColumn: wi + 2, gridRow: di + 2 }}
                  title={
                    cell.day
                      ? `${cell.key} · ${
                          state === "pass"
                            ? "all selected buckets present"
                            : state === "fail"
                            ? "missing at least one selected bucket"
                            : "no data"
                        } · click to open the week`
                      : cell.key
                  }
                  aria-label={
                    cell.day ? `${cell.key}` : `no data ${cell.key}`
                  }
                  className={cn(
                    "h-[11px] w-[11px] rounded-[2px] border transition-colors",
                    !inWindow && "cursor-default border-transparent bg-transparent",
                    inWindow && state === "empty" && "border-border/40 bg-muted/40",
                    inWindow && state === "fail" && "border-rose-500/30 bg-rose-500/15 hover:bg-rose-500/25",
                    inWindow && state === "pass" && "border-emerald-500/40 bg-emerald-500/40 hover:bg-emerald-500/60",
                    isFuture && inWindow && "opacity-40",
                  )}
                />
              );
            }),
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================
// Cell drawer (unchanged)
// ============================================================

interface DrawerProps {
  selected: SelectedCell | null;
  backfill: BackfillProgress | null;
  onClose: () => void;
  onBackfill: (bucket: string) => void;
}

function CellDrawer({ selected, backfill, onClose, onBackfill }: DrawerProps) {
  if (!selected) return null;
  const { day, bucket, status } = selected;
  const isMissing = !status.present;
  const progress = backfill && backfill.bucket === bucket ? backfill : null;
  const today = localIsoDate(new Date());
  const knownUnfillable =
    day !== today &&
    (status.fallback ?? "").toLowerCase().includes("not backfillable");
  return (
    <div
      className="rounded-xl border bg-card p-4 text-xs shadow-sm"
      role="dialog"
      aria-label={`${bucket} on ${day}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-mono text-sm font-semibold">{bucket}</div>
          <div className="text-muted-foreground">on {day}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close drawer"
          className="rounded-lg border bg-background px-2.5 py-1 text-muted-foreground transition-colors hover:bg-muted"
        >
          ×
        </button>
      </div>
      <div className="mt-3 grid grid-cols-[8rem_1fr] gap-x-2 gap-y-1.5">
        <div className="text-muted-foreground">status</div>
        <div
          className={cn(
            "font-mono",
            status.present
              ? "text-emerald-700 dark:text-emerald-300"
              : "text-rose-700 dark:text-rose-300",
          )}
        >
          {status.present ? "present" : "missing"}
        </div>
        {status.rows !== undefined && status.rows !== null && (
          <>
            <div className="text-muted-foreground">rows</div>
            <div className="font-mono">{status.rows}</div>
          </>
        )}
        {status.location && (
          <>
            <div className="text-muted-foreground">location</div>
            <div className="truncate font-mono" title={status.location}>
              {status.location}
            </div>
          </>
        )}
        <div className="text-muted-foreground">source</div>
        <div className="font-mono">{status.primary_source || "—"}</div>
      </div>
      {isMissing && (
        <div className="mt-3 rounded-lg bg-amber-500/10 px-3 py-2 text-amber-800 dark:text-amber-300">
          <div className="font-mono">
            <span className="text-muted-foreground">cmd:</span>{" "}
            {status.fetch_command || "—"}
          </div>
          {status.fallback && (
            <div className="mt-1 font-mono">
              <span className="text-muted-foreground">fallback:</span>{" "}
              {status.fallback}
            </div>
          )}
          {knownUnfillable ? (
            <div className="mt-2 text-muted-foreground">
              Not backfillable for {day} — this source only has a live
              snapshot for today.
            </div>
          ) : (
            <>
              <button
                type="button"
                onClick={() => onBackfill(bucket)}
                disabled={progress?.status === "running"}
                className="mt-2 inline-flex items-center gap-1 rounded-lg border border-amber-500/40 bg-background px-3 py-1.5 text-amber-800 transition-colors hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-300"
              >
                {progress?.status === "running" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Wand2 className="h-4 w-4" />
                )}
                {progress?.status === "running"
                  ? "Backfilling…"
                  : "Backfill this bucket"}
              </button>
              {progress &&
                progress.status === "done" &&
                progress.result && (
                  <div className="mt-2 font-mono text-emerald-700 dark:text-emerald-300">
                    {progress.result.status} —{" "}
                    {progress.result.rows_written} row(s) in{" "}
                    {progress.result.duration_ms} ms
                    {progress.result.rows_written === 0 &&
                      progress.result.message && (
                        <div className="mt-1 text-muted-foreground">
                          {progress.result.message}
                        </div>
                      )}
                  </div>
                )}
              {progress && progress.status === "error" && (
                <div className="mt-2 font-mono text-rose-700 dark:text-rose-300">
                  error:{" "}
                  {progress.error ?? progress.result?.error ?? "unknown"}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default StockHistoryCoveragePanel;
