import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ClipboardCheck, History, RefreshCw } from "lucide-react";
import {
  api,
  type AutonomousAgentInstance,
  type IndexPredictionArtifact,
  type IndexUpcomingEvent,
  type LivePositionGroup,
  type LivePositionSkipped,
} from "@/lib/api";
import { PnlForecastBandChart } from "@/components/board/PnlForecastBandChart";
import { IndexEventsForecastChart } from "@/components/charts/IndexEventsForecastChart";
import { NewsImpactPanel } from "@/components/prediction/NewsImpactPanel";
import { safeGet, safeSet } from "@/lib/storage";
import { cn } from "@/lib/utils";

// Same live-reprice cadence as PositionsBoard.tsx — this panel reads the same
// compute_live_pop_for_agent-backed endpoint.
const POSITIONS_POLL_MS = 20_000;
// Matches Prediction.tsx's index-prediction refresh cadence for the same artifact.
const PREDICTION_POLL_MS = 60_000;
const EVENTS_HORIZON_DAYS = 7;

function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

const LAST_SEEN_PREDICTION_KEY = "command-center:last-seen-nifty-prediction";

/** What this browser last saw for the NIFTY prediction artifact — real fields (`as_of`,
 * `expected_return_pct`, `range.low/high`) from the last artifact this page rendered, not a
 * fabricated delta feed. "Revised since I last looked" is genuinely a per-viewer, per-browser
 * concept for this single-operator dashboard, so localStorage is the honest home for it —
 * there's no multi-user identity system to hang a server-side "last seen" state off. */
interface LastSeenPrediction {
  as_of: string;
  expected_return_pct: number | null;
  range_low: number | null;
  range_high: number | null;
}

function readLastSeenPrediction(): LastSeenPrediction | null {
  const raw = safeGet(LAST_SEEN_PREDICTION_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.as_of === "string") return parsed as LastSeenPrediction;
  } catch {
    /* corrupt/old-shape value — treat as no prior snapshot */
  }
  return null;
}

function writeLastSeenPrediction(snapshot: LastSeenPrediction): void {
  safeSet(LAST_SEEN_PREDICTION_KEY, JSON.stringify(snapshot));
}

/** Real expiry_days-derived progress bar, normalized against the longest current
 * expiry among open groups so bars are relatively comparable. Not a fabricated
 * "exit plan" figure — expiry_days is the only real distance-to-exit data this
 * API exposes today (options only; see backlog sub-item for the generalized gap). */
function ExitTimelineRow({ group, maxDays }: { group: LivePositionGroup; maxDays: number }) {
  const pct = maxDays > 0 ? Math.max(4, 100 - (group.expiry_days / maxDays) * 100) : 100;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-28 shrink-0 truncate text-muted-foreground">{group.underlying}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
        <div className="h-2 rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-16 shrink-0 text-right font-mono text-muted-foreground">
        {group.expiry_days}d to exit
      </span>
    </div>
  );
}

function PnlPositionsPanel() {
  const [agents, setAgents] = useState<AutonomousAgentInstance[]>([]);
  const [agentId, setAgentId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [groups, setGroups] = useState<LivePositionGroup[]>([]);
  const [skipped, setSkipped] = useState<LivePositionSkipped[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listAutonomousAgents()
      .then((res) => {
        if (cancelled) return;
        setAgents(res.agents);
        if (res.agents.length > 0) setAgentId((prev) => prev || res.agents[0].id);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load agents.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .getAgentLivePositions(id)
      .then((res) => {
        setGroups(res.groups);
        setSkipped(res.skipped);
      })
      .catch(() => setError("Failed to load live positions for this agent."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!agentId) return;
    load(agentId);
    const timer = window.setInterval(() => load(agentId), POSITIONS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [agentId, load]);

  // trajectory[0] is today's real observed P&L per LivePositionGroup's documented shape.
  const totalPnl = useMemo(
    () => groups.reduce((sum, g) => sum + (g.trajectory[0]?.pnl_inr ?? 0), 0),
    [groups],
  );
  const maxExpiryDays = useMemo(
    () => groups.reduce((max, g) => Math.max(max, g.expiry_days), 0),
    [groups],
  );

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">P&amp;L, open positions &amp; exit timeline</h2>
          <p className="text-xs text-muted-foreground">Live re-price, same source as Positions board.</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="rounded-md border bg-background px-2 py-1 text-xs"
          >
            {agents.length === 0 ? <option value="">No agents</option> : null}
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name || a.id}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => agentId && load(agentId)}
            disabled={!agentId || loading}
            title="Refresh now"
            aria-label="Refresh now"
            className="rounded-md border bg-background p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          </button>
        </div>
      </div>

      {error ? (
        <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/5 p-3 text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      ) : null}

      {!agentId ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-4 text-center text-xs text-muted-foreground">
          No autonomous agents exist yet.
        </div>
      ) : loading && groups.length === 0 && skipped.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-4 text-center text-xs text-muted-foreground">
          Loading…
        </div>
      ) : groups.length === 0 && skipped.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-4 text-center text-xs text-muted-foreground">
          No open positions for this agent.
        </div>
      ) : (
        <div className="space-y-4">
          <div
            className={cn(
              "flex items-center justify-between rounded-lg px-3 py-2 text-sm font-medium",
              totalPnl >= 0
                ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                : "bg-red-500/10 text-red-600 dark:text-red-400",
            )}
          >
            <span>Total P&amp;L (today, across {groups.length} position group{groups.length === 1 ? "" : "s"})</span>
            <span>{fmtInr(totalPnl)}</span>
          </div>

          {groups.length > 0 ? (
            <div className="space-y-2">
              <div className="text-xs font-medium text-muted-foreground">Exit timeline</div>
              {groups.map((g) => (
                <ExitTimelineRow key={`${g.underlying}-${g.expiry_days}`} group={g} maxDays={maxExpiryDays} />
              ))}
            </div>
          ) : null}

          <div className="space-y-3">
            {groups.map((g) => (
              <div key={`${g.underlying}-${g.expiry_days}-band`} className="space-y-1">
                <div className="text-xs text-muted-foreground">{g.underlying} P&amp;L forecast band</div>
                <PnlForecastBandChart band={g.pnl_forecast_band} height={140} />
              </div>
            ))}
          </div>

          {skipped.map((row, i) => (
            <div
              key={`${row.underlying}-${i}`}
              className="rounded-lg border border-dashed border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
            >
              {row.underlying}: exit distance not tracked yet — {row.reason}
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 border-t border-border/50 pt-3">
        <Link
          to="/advisory-board"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
        >
          <ClipboardCheck className="h-3.5 w-3.5" />
          Review strategy recommendations →
        </Link>
      </div>
    </div>
  );
}

function EventsRangePanel() {
  const [artifact, setArtifact] = useState<IndexPredictionArtifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Captured once, from storage, the first time this component sees data — not re-read on
  // every poll — so the "revised since you looked" comparison stays anchored to what this
  // browser saw when the page was opened, not to the previous poll a minute ago.
  const baselineRef = useRef<LastSeenPrediction | null | undefined>(undefined);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getIndexPrediction("NIFTY", EVENTS_HORIZON_DAYS)
      .then((res) => {
        const art = res.artifact ?? null;
        if (baselineRef.current === undefined) {
          baselineRef.current = readLastSeenPrediction();
        }
        setArtifact(art);
        if (art?.as_of) {
          writeLastSeenPrediction({
            as_of: art.as_of,
            expected_return_pct: art.prediction?.expected_return_pct ?? null,
            range_low: art.prediction?.range?.low ?? null,
            range_high: art.prediction?.range?.high ?? null,
          });
        }
      })
      .catch(() => setError("Failed to load Nifty prediction artifact."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, PREDICTION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const events7d: IndexUpcomingEvent[] = useMemo(
    () =>
      (artifact?.upcoming_events ?? []).filter(
        (e) => e.days_from_now != null && e.days_from_now >= 0 && e.days_from_now <= EVENTS_HORIZON_DAYS,
      ),
    [artifact],
  );

  const revision = useMemo(() => {
    const prev = baselineRef.current;
    if (!prev || !artifact?.as_of || prev.as_of === artifact.as_of) return null;
    const curRet = artifact.prediction?.expected_return_pct ?? null;
    return {
      prevAsOf: prev.as_of,
      returnDeltaPct: curRet != null && prev.expected_return_pct != null ? curRet - prev.expected_return_pct : null,
      prevRangeLow: prev.range_low,
      prevRangeHigh: prev.range_high,
    };
    // Only artifact.as_of actually changes this outcome once baselineRef is captured;
    // baselineRef.current is intentionally excluded since refs don't participate in deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifact?.as_of, artifact?.prediction?.expected_return_pct]);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Next {EVENTS_HORIZON_DAYS} days — Nifty events &amp; projected range</h2>
          <p className="text-xs text-muted-foreground">
            Shaded band is a linear projection between real spot and the model's real
            {" "}{EVENTS_HORIZON_DAYS}d range endpoints (not a per-day distribution — see
            backlog for the real daily-quantile-band gap).
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          title="Refresh now"
          aria-label="Refresh now"
          className="rounded-md border bg-background p-1.5 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </button>
      </div>

      {error ? (
        <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/5 p-3 text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      ) : null}

      {!artifact && loading ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-4 text-center text-xs text-muted-foreground">
          Loading…
        </div>
      ) : !artifact ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-4 text-center text-xs text-muted-foreground">
          No prediction artifact available yet.
        </div>
      ) : (
        <div className="space-y-3">
          {revision ? (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
              <History className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                Prediction revised since you last checked (previous run {" "}
                {new Date(revision.prevAsOf).toLocaleString("en-IN")}). Expected return moved{" "}
                {fmtPct(revision.returnDeltaPct)}
                {revision.prevRangeLow != null && revision.prevRangeHigh != null
                  ? `; range was ${fmtLevel(revision.prevRangeLow)} – ${fmtLevel(revision.prevRangeHigh)}`
                  : ""}
                .
              </span>
            </div>
          ) : null}
          <IndexEventsForecastChart
            spot={artifact.spot ?? 0}
            horizonDays={EVENTS_HORIZON_DAYS}
            expectedReturnPct={artifact.prediction?.expected_return_pct ?? 0}
            rangeLow={artifact.prediction?.range?.low}
            rangeHigh={artifact.prediction?.range?.high}
            upcomingEvents={events7d}
            height={260}
          />
          <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <div>
              Spot: <span className="font-mono text-foreground">{fmtLevel(artifact.spot)}</span>
            </div>
            <div>
              Projected range: {" "}
              <span className="font-mono text-foreground">
                {fmtLevel(artifact.prediction?.range?.low)} – {fmtLevel(artifact.prediction?.range?.high)}
              </span>
            </div>
          </div>

          {events7d.length > 0 ? (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {events7d.map((ev, i) => (
                <div
                  key={`${ev.date ?? i}-${ev.label ?? i}`}
                  className="rounded-lg border border-border/50 bg-muted/10 px-3 py-2 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{ev.label ?? ev.event_type ?? "Event"}</span>
                    <span className="font-mono text-muted-foreground">+{ev.days_from_now}d</span>
                  </div>
                  <div className="mt-0.5 text-muted-foreground">
                    {ev.category ?? ev.event_type ?? "—"}
                    {ev.symbol ? ` · ${ev.symbol}` : ""}
                    {ev.impact ? ` · ${ev.impact}` : ""}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">No dated events in the next {EVENTS_HORIZON_DAYS} days.</p>
          )}
        </div>
      )}
    </div>
  );
}

export function CommandCenter() {
  return (
    <div className="mx-auto max-w-[1600px] space-y-4 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Command Center</h1>
        <p className="text-sm text-muted-foreground">
          One screen: current P&amp;L and exit timeline, the next {EVENTS_HORIZON_DAYS} days of
          Nifty events and projected range, and live news impact. Display only — no
          auto-trigger or exit decision happens here.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[2fr_1fr]">
        <div className="space-y-4">
          <PnlPositionsPanel />
          <EventsRangePanel />
        </div>
        <div>
          <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
            <h2 className="mb-3 text-sm font-semibold">News events &amp; Nifty impact</h2>
            <NewsImpactPanel horizonDays={EVENTS_HORIZON_DAYS} />
          </div>
        </div>
      </div>
    </div>
  );
}
