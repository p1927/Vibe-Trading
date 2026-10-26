import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { TrackRecordResponse } from "@/lib/knowledgeEngine";
import type { ExecutionAdvisorAdvisory } from "@/lib/options";
import { formatIstTime } from "@/lib/istTime";
import { formatNumber, formatFractionPercent } from "@/lib/formatters";

const POLL_INTERVAL_MS = 15_000;
const UNGROUPED_KEY = "ungrouped";


/** The panel reads the newest row the scheduled sweep wrote, so it can be old. Past this the
 * advice is flagged stale: the sweep is not running, or has stopped. */
export const STALE_ADVICE_MS = 30 * 60_000;

export function adviceAge(advisedAt: string | null | undefined, now: number): { label: string; stale: boolean } {
  const ts = advisedAt ? Date.parse(advisedAt) : NaN;
  if (!Number.isFinite(ts)) return { label: "advised at unknown time", stale: true };
  const ms = Math.max(0, now - ts);
  const min = Math.floor(ms / 60_000);
  const label = min < 1 ? "advised just now" : min < 120 ? `advised ${min}m ago` : `advised ${Math.floor(min / 60)}h ago`;
  return { label, stale: ms > STALE_ADVICE_MS };
}

function actionBadgeClass(action: string): string {
  if (action === "exit") return "bg-danger/10 text-danger";
  if (action === "tighten_stop") return "bg-amber-500/10 text-amber-600 dark:text-amber-400";
  return "bg-muted text-muted-foreground";
}

function fsmStateBadgeClass(state: string): string {
  if (state === "exit_pending") return "bg-danger/10 text-danger";
  if (state === "trailing") return "bg-primary/10 text-primary";
  return "bg-muted text-muted-foreground";
}

function AdvisoryCard({ advisory, now }: { advisory: ExecutionAdvisorAdvisory; now: number }) {
  // A position opened since the last sweep has no row yet: say so, never render a blank badge.
  const notYetAdvised = advisory.action == null;
  const age = notYetAdvised ? null : adviceAge(advisory.advised_at, now);
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3" data-testid="advisory-card">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">{advisory.symbol}</div>
        <div className="flex items-center gap-1.5">
          {notYetAdvised ? (
            <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
              not yet advised
            </span>
          ) : (
            <>
              {advisory.fsm_state ? (
                <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", fsmStateBadgeClass(advisory.fsm_state))}>
                  {advisory.fsm_state}
                </span>
              ) : null}
              <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", actionBadgeClass(advisory.action!))}>
                {advisory.action}
              </span>
            </>
          )}
        </div>
      </div>
      <p className="mb-2 text-[11px] text-muted-foreground">
        {notYetAdvised
          ? "Opened since the last advisory sweep - no advice yet."
          : advisory.reason.replace(/_/g, " ")}
        {age ? (
          <span
            className={cn("ml-1.5", age.stale && "font-medium text-amber-600 dark:text-amber-400")}
            data-testid="advice-age"
          >
            · {age.label}
            {age.stale ? " (stale - is the advisory sweep running?)" : ""}
          </span>
        ) : null}
      </p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
        <div>
          LTP <span className="font-medium text-foreground">{formatNumber(advisory.ltp, { maximumFractionDigits: 2 })}</span>
        </div>
        <div>
          Entry <span className="font-medium text-foreground">{formatNumber(advisory.entry_price, { maximumFractionDigits: 2 })}</span>
        </div>
        <div>
          Direction <span className="font-medium text-foreground">{advisory.entry_direction ?? "–"}</span>
        </div>
        <div>
          Trail stop <span className="font-medium text-foreground">{formatNumber(advisory.recommended_stop_price, { maximumFractionDigits: 2 })}</span>
        </div>
        <div>
          Confidence (EMA) <span className="font-medium text-foreground">{formatFractionPercent(advisory.confidence_ema)}</span>
        </div>
        <div>
          Flip count <span className="font-medium text-foreground">{formatNumber(advisory.consecutive_flip_count, { maximumFractionDigits: 0 })}</span>
        </div>
      </div>
    </div>
  );
}

function StrategyGroup({
  strategyId,
  advisories,
  now,
}: {
  strategyId: string;
  advisories: ExecutionAdvisorAdvisory[];
  now: number;
}) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {strategyId === UNGROUPED_KEY ? "Ungrouped positions" : strategyId}
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {advisories.map((advisory) => (
          <AdvisoryCard key={advisory.symbol} advisory={advisory} now={now} />
        ))}
      </div>
    </div>
  );
}

function TrackRecordPanel({ record }: { record: TrackRecordResponse | null }) {
  if (!record) return null;
  return (
    <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3">
        <div className="text-sm font-semibold">Track Record</div>
        <p className="text-xs text-muted-foreground">Forecast accuracy from the realized prediction ledger — {record.scope}</p>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
        <div>Samples <span className="font-medium text-foreground">{formatNumber(record.sample_count, { maximumFractionDigits: 0 })}</span></div>
        <div>Evaluated <span className="font-medium text-foreground">{formatNumber(record.eval_count, { maximumFractionDigits: 0 })}</span></div>
        <div>Direction, {record.window_days}d <span className="font-medium text-foreground">{formatFractionPercent(record.direction_hit_rate_14d)}</span></div>
        <div>MAE, {record.window_days}d <span className="font-medium text-foreground">{record.mae_14d_pct == null ? "–" : `${formatNumber(record.mae_14d_pct, { maximumFractionDigits: 2 })}%`}</span></div>
      </div>
    </section>
  );
}

/**
 * Module 7: read-only, advisory-only view of every open position's FSM state
 * (in_trade → trailing → exit_pending) and recommended action
 * (hold/tighten_stop/exit). Never places, closes, or edits an order — this
 * page only tells you what the advisor thinks should happen, grouped by
 * strategy so a multi-leg spread reads as one card group instead of N
 * unrelated legs.
 */
export function ExecutionAdvisor() {
  const [grouped, setGrouped] = useState<Record<string, ExecutionAdvisorAdvisory[]> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [trackRecord, setTrackRecord] = useState<TrackRecordResponse | null>(null);
  const [trackRecordError, setTrackRecordError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getExecutionAdvisorAdvisories();
      if (!res.ok) {
        setError(res.error || "Request failed");
        return;
      }
      setError(null);
      setGrouped(res.grouped);
      setLastUpdated(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTrackRecord = useCallback(async () => {
    try {
      const res = await api.getKnowledgeTrackRecord({ window: 14, ticker: "NIFTY" });
      if (!res.ok) {
        setTrackRecord(null);
        setTrackRecordError(res.error || "Track record unavailable");
        return;
      }
      setTrackRecord(res);
      setTrackRecordError(null);
    } catch (e) {
      setTrackRecord(null);
      setTrackRecordError(e instanceof Error ? e.message : "Track record unavailable");
    }
  }, []);

  useEffect(() => {
    void load();
    void loadTrackRecord();
    pollRef.current = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load, loadTrackRecord]);

  const groupEntries = grouped ? Object.entries(grouped) : [];
  const totalCount = groupEntries.reduce((sum, [, advisories]) => sum + advisories.length, 0);

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <TrackRecordPanel record={trackRecord} />
      {trackRecordError && (
        <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
          Track record unavailable: {trackRecordError}
        </div>
      )}
      <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Execution Advisor</div>
            <div className="text-xs text-muted-foreground">
              Advisory only — surfaces recommended hold/tighten-stop/exit actions for every open
              position. Never places, closes, or edits an order.
              {lastUpdated && ` Fetched ${formatIstTime(lastUpdated)}; each card shows when its advice was written.`}
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-border/60 bg-background px-3 py-1.5 text-sm font-medium transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </button>
        </div>

        {error && (
          <div className="mb-3 rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>
        )}

        {!error && grouped && totalCount === 0 && (
          <p className="text-sm text-muted-foreground">No open positions.</p>
        )}

        {!error && groupEntries.length > 0 && (
          <div className="space-y-4">
            {groupEntries.map(([strategyId, advisories]) => (
              <StrategyGroup
                key={strategyId}
                strategyId={strategyId}
                advisories={advisories}
                now={lastUpdated?.getTime() ?? Date.now()}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
