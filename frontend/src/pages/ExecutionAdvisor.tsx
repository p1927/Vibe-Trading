import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { ExecutionAdvisorAdvisory } from "@/lib/options";

const POLL_INTERVAL_MS = 15_000;
const UNGROUPED_KEY = "ungrouped";

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return `${fmtNum(v * 100, 0)}%`;
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

function AdvisoryCard({ advisory }: { advisory: ExecutionAdvisorAdvisory }) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">{advisory.symbol}</div>
        <div className="flex items-center gap-1.5">
          <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", fsmStateBadgeClass(advisory.fsm_state))}>
            {advisory.fsm_state}
          </span>
          <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium", actionBadgeClass(advisory.action))}>
            {advisory.action}
          </span>
        </div>
      </div>
      <p className="mb-2 text-[11px] text-muted-foreground">{advisory.reason.replace(/_/g, " ")}</p>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
        <div>
          LTP <span className="font-medium text-foreground">{fmtNum(advisory.ltp)}</span>
        </div>
        <div>
          Entry <span className="font-medium text-foreground">{fmtNum(advisory.entry_price)}</span>
        </div>
        <div>
          Direction <span className="font-medium text-foreground">{advisory.entry_direction}</span>
        </div>
        <div>
          Trail stop <span className="font-medium text-foreground">{fmtNum(advisory.recommended_stop_price)}</span>
        </div>
        <div>
          Confidence (EMA) <span className="font-medium text-foreground">{fmtPct(advisory.confidence_ema)}</span>
        </div>
        <div>
          Flip count <span className="font-medium text-foreground">{advisory.consecutive_flip_count}</span>
        </div>
      </div>
    </div>
  );
}

function StrategyGroup({
  strategyId,
  advisories,
}: {
  strategyId: string;
  advisories: ExecutionAdvisorAdvisory[];
}) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {strategyId === UNGROUPED_KEY ? "Ungrouped positions" : strategyId}
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {advisories.map((advisory) => (
          <AdvisoryCard key={advisory.symbol} advisory={advisory} />
        ))}
      </div>
    </div>
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

  useEffect(() => {
    void load();
    pollRef.current = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load]);

  const groupEntries = grouped ? Object.entries(grouped) : [];
  const totalCount = groupEntries.reduce((sum, [, advisories]) => sum + advisories.length, 0);

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold">Execution Advisor</div>
            <div className="text-xs text-muted-foreground">
              Advisory only — surfaces recommended hold/tighten-stop/exit actions for every open
              position. Never places, closes, or edits an order.
              {lastUpdated && ` Updated ${lastUpdated.toLocaleTimeString()}.`}
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
              <StrategyGroup key={strategyId} strategyId={strategyId} advisories={advisories} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
