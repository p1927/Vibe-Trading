/**
 * Stock-history coverage panel — per-week bucket availability gate
 * for the stock_simulator.
 *
 * Shows 5 trading days x N buckets as a heatmap:
 *   - Green cell = data is present for that bucket on that day
 *   - White cell = data is missing (click to see the fetch_command)
 *
 * Clicking a missing cell opens a drawer with the bucket's
 * `primary_source`, `fetch_command`, and a "Backfill this bucket"
 * button that POSTs to /trade/hub/stock-history/backfill. After the
 * backfill completes the panel re-fetches coverage so the user sees
 * the green tick without reloading the page.
 *
 * Reuses the project conventions from SimulatorReplayCalendar
 * (useEffect, lucide-react icons, cn() helper, @/lib/api client).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, RefreshCw, Wand2 } from "lucide-react";
import { cn } from "@/lib/utils";
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

/** Monday of the ISO week containing `d`. */
function mondayOf(d: Date): Date {
  const out = new Date(d);
  const js = out.getDay(); // Sun=0..Sat=6
  // JS Sun(0) -> -6, Mon(1) -> 0, Tue(2) -> -1, ... Sat(6) -> -5.
  // We want distance back to Monday (JS weekday=1).
  const backToMon = (js + 6) % 7;
  out.setDate(out.getDate() - backToMon);
  out.setHours(0, 0, 0, 0);
  return out;
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

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
  const [weekStart, setWeekStart] = useState<Date>(() => {
    if (initialWeekStart) return mondayOf(parseIsoDate(initialWeekStart));
    return mondayOf(new Date());
  });
  const [report, setReport] = useState<HubStockHistoryCoverageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedCell | null>(null);
  const [backfill, setBackfill] = useState<BackfillProgress | null>(null);

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
        // Re-fetch coverage so the user sees the green tick.
        if (resp.coverage_after) {
          setReport(resp.coverage_after);
        } else {
          await fetchCoverage();
        }
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

  const weekEndIso = useMemo(() => {
    if (!report) return isoDate(addDays(weekStart, 4));
    return report.week_end;
  }, [weekStart, report]);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Previous week"
            onClick={() => shiftWeek(-7)}
            className="rounded-md border border-zinc-300 px-2 py-1 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <div className="font-mono text-sm">
            {weekStartIso} &mdash; {weekEndIso}
          </div>
          <button
            type="button"
            aria-label="Next week"
            onClick={() => shiftWeek(7)}
            className="rounded-md border border-zinc-300 px-2 py-1 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {report && (
            <span
              className={cn(
                "rounded-full px-2 py-0.5",
                report.is_complete
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100"
                  : "bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-100",
              )}
            >
              {report.is_complete
                ? "Week fully covered"
                : `${report.missing_days.length} day(s) incomplete`}
            </span>
          )}
          <button
            type="button"
            onClick={() => void fetchCoverage()}
            disabled={loading}
            className="rounded-md border border-zinc-300 px-2 py-1 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
            aria-label="Refresh coverage"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-700 dark:bg-rose-900/40 dark:text-rose-200">
          {error}
        </div>
      )}

      <CoverageHeatmap
        report={report}
        loading={loading}
        onCellClick={(day, bucket, status) => setSelected({ day, bucket, status })}
        selected={selected}
      />

      <CellDrawer
        selected={selected}
        onClose={() => setSelected(null)}
        onBackfill={(bucket) => void onBackfill(bucket)}
        backfill={backfill}
      />
    </div>
  );
}

interface HeatmapProps {
  report: HubStockHistoryCoverageResponse | null;
  loading: boolean;
  selected: SelectedCell | null;
  onCellClick: (
    day: string,
    bucket: string,
    status: HubStockHistoryCoverageBucketStatus,
  ) => void;
}

function CoverageHeatmap({ report, loading, selected, onCellClick }: HeatmapProps) {
  if (loading && !report) {
    return (
      <div className="flex items-center gap-2 rounded-md border border-zinc-200 bg-zinc-50 px-3 py-6 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading coverage…
      </div>
    );
  }
  if (!report || report.bucket_labels.length === 0 || report.days.length === 0) {
    return (
      <div className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-6 text-center text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900">
        No coverage data for this week.
      </div>
    );
  }

  const labels = report.bucket_labels;
  const labelWidth = Math.max(...labels.map((l) => l.length)) + 2;

  return (
    <div className="overflow-x-auto rounded-md border border-zinc-200 dark:border-zinc-700">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            <th
              className="border-b border-zinc-200 bg-zinc-50 px-2 py-1 text-left font-medium text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
              style={{ minWidth: `${labelWidth}ch` }}
            >
              bucket
            </th>
            {report.days.map((d) => (
              <th
                key={d.day}
                className="border-b border-zinc-200 bg-zinc-50 px-2 py-1 text-center font-mono font-medium text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                style={{ minWidth: "5rem" }}
              >
                {d.day.slice(5)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((bucket) => (
            <tr key={bucket}>
              <td className="border-b border-zinc-100 bg-white px-2 py-1 font-mono text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-300">
                {bucket}
              </td>
              {report.days.map((day) => (
                <CoverageCell
                  key={`${bucket}-${day.day}`}
                  bucket={bucket}
                  day={day}
                  selected={selected}
                  onClick={onCellClick}
                />
              ))}
            </tr>
          ))}
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
}: {
  bucket: string;
  day: HubStockHistoryCoverageDay;
  selected: SelectedCell | null;
  onClick: (
    day: string,
    bucket: string,
    status: HubStockHistoryCoverageBucketStatus,
  ) => void;
}) {
  const status = day.buckets[bucket];
  if (!status) {
    return <td className="border-b border-zinc-100 px-2 py-1 dark:border-zinc-800" />;
  }
  const isSelected =
    selected !== null &&
    selected.day === day.day &&
    selected.bucket === bucket;

  return (
    <td
      className={cn(
        "border-b border-zinc-100 px-2 py-1 text-center font-mono dark:border-zinc-800",
        status.present
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
          : "bg-white text-zinc-500 hover:bg-zinc-50 dark:bg-zinc-950 dark:hover:bg-zinc-900",
        isSelected && "ring-2 ring-sky-500",
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
  return (
    <div
      className="rounded-md border border-zinc-200 bg-white p-3 text-xs dark:border-zinc-700 dark:bg-zinc-950"
      role="dialog"
      aria-label={`${bucket} on ${day}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-mono text-sm font-semibold">{bucket}</div>
          <div className="text-zinc-500">on {day}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close drawer"
          className="rounded-md border border-zinc-300 px-2 py-0.5 text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          ×
        </button>
      </div>
      <div className="mt-2 grid grid-cols-[8rem_1fr] gap-x-2 gap-y-1">
        <div className="text-zinc-500">status</div>
        <div
          className={cn(
            "font-mono",
            status.present ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300",
          )}
        >
          {status.present ? "present" : "missing"}
        </div>
        {status.rows !== undefined && status.rows !== null && (
          <>
            <div className="text-zinc-500">rows</div>
            <div className="font-mono">{status.rows}</div>
          </>
        )}
        {status.location && (
          <>
            <div className="text-zinc-500">location</div>
            <div className="truncate font-mono" title={status.location}>
              {status.location}
            </div>
          </>
        )}
        <div className="text-zinc-500">source</div>
        <div className="font-mono">{status.primary_source || "—"}</div>
      </div>
      {isMissing && (
        <div className="mt-3 rounded-md bg-amber-50 px-2 py-2 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <div className="font-mono">
            <span className="text-zinc-500">cmd:</span> {status.fetch_command || "—"}
          </div>
          {status.fallback && (
            <div className="mt-1 font-mono">
              <span className="text-zinc-500">fallback:</span> {status.fallback}
            </div>
          )}
          <button
            type="button"
            onClick={() => onBackfill(bucket)}
            disabled={progress?.status === "running"}
            className="mt-2 inline-flex items-center gap-1 rounded-md border border-amber-400 bg-white px-2 py-1 text-amber-900 hover:bg-amber-100 disabled:opacity-50 dark:bg-amber-950 dark:text-amber-100 dark:hover:bg-amber-900"
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
          {progress && progress.status === "done" && progress.result && (
            <div className="mt-2 font-mono text-emerald-700 dark:text-emerald-300">
              {progress.result.status} — {progress.result.rows_written} row(s) in
              {" "}
              {progress.result.duration_ms} ms
            </div>
          )}
          {progress && progress.status === "error" && (
            <div className="mt-2 font-mono text-rose-700 dark:text-rose-300">
              error: {progress.error ?? progress.result?.error ?? "unknown"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default StockHistoryCoveragePanel;
