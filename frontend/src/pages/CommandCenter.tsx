import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ClipboardCheck, History, RefreshCw } from "lucide-react";
import {
  api,
  type AutonomousAgentInstance,
  type HubIndexHistoryBar,
  type IndexPredictionArtifact,
  type IndexUpcomingEvent,
  type LivePositionGroup,
  type LivePositionSkipped,
} from "@/lib/api";
import { PnlForecastBandChart } from "@/components/board/PnlForecastBandChart";
import { IndexEventsForecastChart } from "@/components/charts/IndexEventsForecastChart";
import { PriorDayPriceStrip } from "@/components/charts/PriorDayPriceStrip";
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
    <div className="rounded-xl border border-border/60 bg-card p-2 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
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
        <div className="flex items-center gap-2">
          {groups.length > 0 ? (
            <span
              className={cn(
                "rounded px-2 py-0.5 text-sm font-semibold",
                totalPnl >= 0
                  ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                  : "bg-red-500/10 text-red-600 dark:text-red-400",
              )}
            >
              {fmtInr(totalPnl)}
            </span>
          ) : null}
          <Link
            to="/advisory-board"
            title="Strategy recommendations"
            aria-label="Strategy recommendations"
            className="rounded-md border bg-background p-1.5 text-muted-foreground transition-colors hover:text-foreground"
          >
            <ClipboardCheck className="h-3.5 w-3.5" />
          </Link>
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
        <div className="mb-2 rounded-lg border border-red-500/40 bg-red-500/5 p-2 text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      ) : null}

      {!agentId ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          No autonomous agents exist yet.
        </div>
      ) : loading && groups.length === 0 && skipped.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          Loading…
        </div>
      ) : groups.length === 0 && skipped.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          No open positions for this agent.
        </div>
      ) : (
        <div className="space-y-2">
          {groups.length > 0 ? (
            <div className="space-y-1">
              {groups.map((g) => (
                <ExitTimelineRow key={`${g.underlying}-${g.expiry_days}`} group={g} maxDays={maxExpiryDays} />
              ))}
            </div>
          ) : null}

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {groups.map((g) => (
              <div key={`${g.underlying}-${g.expiry_days}-band`} className="relative">
                <span className="absolute left-2 top-1 z-10 text-[10px] font-medium text-muted-foreground">
                  {g.underlying}
                </span>
                <PnlForecastBandChart band={g.pnl_forecast_band} height={100} />
              </div>
            ))}
          </div>

          {skipped.map((row, i) => (
            <div
              key={`${row.underlying}-${i}`}
              className="rounded-lg border border-dashed border-amber-500/40 bg-amber-500/5 px-2 py-1 text-xs text-amber-700 dark:text-amber-400"
            >
              {row.underlying}: {row.reason}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EventsRangePanel() {
  const [artifact, setArtifact] = useState<IndexPredictionArtifact | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [priorDay, setPriorDay] = useState<{ day: string; bars: HubIndexHistoryBar[] } | null>(null);
  // Captured once, from storage, the first time this component sees data — not re-read on
  // every poll — so the "revised since you looked" comparison stays anchored to what this
  // browser saw when the page was opened, not to the previous poll a minute ago.
  const baselineRef = useRef<LastSeenPrediction | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    // Yesterday's closed session never changes, so this is a one-time fetch on mount, not
    // part of the 60s prediction poll. "Yesterday" is resolved from the real recorded-days
    // list (the most recent recorded day strictly before today, IST) rather than naive date
    // arithmetic, so weekends/holidays resolve correctly to the actual last trading session.
    api
      .getHubIndexHistoryDays({ symbol: "NIFTY", exchange: "NSE_INDEX" })
      .then((daysRes) => {
        if (cancelled || daysRes.status !== "ok" || !daysRes.days.length) return null;
        const todayIst = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
        const priorDays = daysRes.days.filter((d) => d < todayIst);
        if (!priorDays.length) return null;
        const day = priorDays.reduce((latest, d) => (d > latest ? d : latest));
        return api
          .getHubIndexHistoryBars({
            symbol: "NIFTY",
            exchange: "NSE_INDEX",
            since_ist: `${day}T09:15:00+05:30`,
            until_ist: `${day}T15:30:00+05:30`,
          })
          .then((barsRes) => {
            if (cancelled || barsRes.status !== "ok" || !barsRes.bars.length) return;
            setPriorDay({ day, bars: barsRes.bars });
          });
      })
      .catch(() => {
        /* prior-day strip is a supplementary panel — leave it in its honest empty state */
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  // Real per-day band, sliced to this panel's 7-day window — the backend artifact's own
  // pipeline horizon (commonly 14d) covers more days than this dashboard shows; slicing to
  // a real band's first week is honest (still real data), unlike fabricating a 7-day one.
  const dailyBand7d = useMemo(
    () => artifact?.prediction?.daily_range_band?.filter((row) => row.days_ahead <= EVENTS_HORIZON_DAYS),
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
    <div className="rounded-xl border border-border/60 bg-card p-2 shadow-sm">
      <div className="mb-2 flex items-center justify-end">
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
        <div className="mb-2 rounded-lg border border-red-500/40 bg-red-500/5 p-2 text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      ) : null}

      {!artifact && loading ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          Loading…
        </div>
      ) : !artifact ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          No prediction artifact available yet.
        </div>
      ) : (
        <div className="space-y-2">
          {revision ? (
            <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/5 px-2 py-1 text-xs text-amber-700 dark:text-amber-400">
              <History className="h-3.5 w-3.5 shrink-0" />
              <span>
                Revised: {fmtPct(revision.returnDeltaPct)}
                {revision.prevRangeLow != null && revision.prevRangeHigh != null
                  ? ` (was ${fmtLevel(revision.prevRangeLow)}–${fmtLevel(revision.prevRangeHigh)})`
                  : ""}
              </span>
            </div>
          ) : null}

          {events7d.length > 0 ? (
            <div className="flex gap-1.5 overflow-x-auto pb-1">
              {events7d.map((ev, i) => (
                <div
                  key={`${ev.date ?? i}-${ev.label ?? i}`}
                  className="flex w-32 shrink-0 flex-col gap-0.5 rounded-md border border-border/50 bg-muted/10 px-2 py-1 text-[11px]"
                  title={[ev.label, ev.category ?? ev.event_type, ev.symbol, ev.impact].filter(Boolean).join(" · ")}
                >
                  <span className="font-mono text-muted-foreground">+{ev.days_from_now}d</span>
                  <span className="truncate font-medium">{ev.label ?? ev.event_type ?? "Event"}</span>
                </div>
              ))}
            </div>
          ) : null}

          <PriorDayPriceStrip day={priorDay?.day ?? null} bars={priorDay?.bars ?? []} currentSpot={artifact.spot} height={64} />
          <IndexEventsForecastChart
            spot={artifact.spot ?? 0}
            horizonDays={EVENTS_HORIZON_DAYS}
            expectedReturnPct={artifact.prediction?.expected_return_pct ?? 0}
            rangeLow={artifact.prediction?.range?.low}
            rangeHigh={artifact.prediction?.range?.high}
            dailyBand={dailyBand7d}
            upcomingEvents={events7d}
            height={220}
          />
        </div>
      )}
    </div>
  );
}

export function CommandCenter() {
  return (
    <div className="mx-auto min-h-screen max-w-none space-y-2 bg-background p-3 text-foreground">
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[2fr_1fr]">
        <div className="space-y-2">
          <PnlPositionsPanel />
          <EventsRangePanel />
        </div>
        <div className="rounded-xl border border-border/60 bg-card p-2 shadow-sm">
          <NewsImpactPanel horizonDays={EVENTS_HORIZON_DAYS} />
        </div>
      </div>
    </div>
  );
}
