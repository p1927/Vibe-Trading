import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ClipboardCheck, Gauge, History } from "lucide-react";
import {
  api,
  type AutonomousAgentInstance,
  type HubIndexHistoryBar,
  type IndexNewsImpactReport,
  type IndexPredictionArtifact,
  type IndexUpcomingEvent,
  type LivePositionGroup,
  type LivePositionSkipped,
} from "@/lib/api";
import { PnlForecastBandChart } from "@/components/board/PnlForecastBandChart";
import { IndexEventsForecastChart } from "@/components/charts/IndexEventsForecastChart";
import { PriorDayPriceStrip } from "@/components/charts/PriorDayPriceStrip";
import { NewsImpactPanel } from "@/components/prediction/NewsImpactPanel";
import { EventsCalendar } from "@/components/news/EventsCalendar";
import { useSSE, type SSEStatus } from "@/hooks/useSSE";
import { safeGet, safeSet } from "@/lib/storage";
import { cn } from "@/lib/utils";

const EVENTS_HORIZON_DAYS = 7;

type DailyBandRow = { days_ahead: number; p10: number; p50: number; p90: number };

/** Fill every day 0..horizonDays by piecewise-linear interpolation between the forecast
 * fan's sparse real per-horizon points (e.g. 1/3/5/10/21 trading days), anchored at `spot`
 * for day 0. `IndexEventsForecastChart` only uses a `dailyBand` when it covers every day in
 * its range (see its own `bandByDay` check) — the fan alone (3-5 points inside a 7-day
 * window) wouldn't pass that, so this expansion is what actually makes the real fan usable
 * by the existing chart component without changing the chart itself. Interpolating between
 * multiple real, independently-retrained-per-horizon anchor points is still real data, and
 * strictly more granular than `daily_range_band`'s own GBM interpolation between just the
 * two terminal endpoints of a single call. */
function interpolateFanToDailyBand(
  fanBand: DailyBandRow[],
  spot: number,
  horizonDays: number,
): DailyBandRow[] | undefined {
  if (!fanBand || fanBand.length === 0 || !Number.isFinite(spot) || spot <= 0) return undefined;
  const anchors = [{ days_ahead: 0, p10: spot, p50: spot, p90: spot }, ...fanBand]
    .filter((row, i, arr) => arr.findIndex((other) => other.days_ahead === row.days_ahead) === i)
    .sort((a, b) => a.days_ahead - b.days_ahead);
  if (anchors.length < 2) return undefined;

  const rows: DailyBandRow[] = [];
  for (let d = 0; d <= horizonDays; d++) {
    let lo = anchors[0];
    let hi = anchors[anchors.length - 1];
    for (let i = 0; i < anchors.length - 1; i++) {
      if (anchors[i].days_ahead <= d && anchors[i + 1].days_ahead >= d) {
        lo = anchors[i];
        hi = anchors[i + 1];
        break;
      }
    }
    const span = hi.days_ahead - lo.days_ahead;
    const t = span === 0 ? 0 : (d - lo.days_ahead) / span;
    rows.push({
      days_ahead: d,
      p10: lo.p10 + (hi.p10 - lo.p10) * t,
      p50: lo.p50 + (hi.p50 - lo.p50) * t,
      p90: lo.p90 + (hi.p90 - lo.p90) * t,
    });
  }
  return rows;
}

/** Pick the fan point closest to `targetDays` (defaults to this panel's 7-day horizon) — the
 * fan's own horizons (1/3/5/10/21 by default) rarely land exactly on 7, so this is the
 * closest real anchor rather than an interpolated one, kept deliberately simple/honest for a
 * single headline number. */
function pickHeadlineFanPoint(band: DailyBandRow[] | undefined, targetDays = 7): DailyBandRow | undefined {
  if (!band || band.length === 0) return undefined;
  return band.reduce((best, row) =>
    Math.abs(row.days_ahead - targetDays) < Math.abs(best.days_ahead - targetDays) ? row : best,
  );
}

/** Spread as a percent of the median (p50) — the fan's own p10/p90 half-width is a genuine
 * per-run uncertainty measure (see `forecast_nifty_fan`'s own docstring on this), not a
 * derived/fabricated confidence score. */
function fanSpreadPct(point: DailyBandRow | undefined): number | null {
  if (!point || !Number.isFinite(point.p50) || point.p50 === 0) return null;
  return ((point.p90 - point.p10) / point.p50) * 100;
}

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
  /** Real `date|label` keys for the 7-day-window upcoming events this page rendered last
   * visit — lets a fresh page load diff "which events are new since I last looked" without
   * duplicating `EventsRangePanel`'s own filtering logic. Optional/absent on an
   * older-shape stored snapshot (pre-dates this field) — treated as "no prior event
   * baseline" rather than a parse error. */
  eventKeys?: string[];
}

function eventKeyFor(ev: IndexUpcomingEvent): string {
  return `${ev.date ?? ""}|${ev.label ?? ev.event_type ?? ""}`;
}

function events7dKeys(artifact: IndexPredictionArtifact | null): string[] {
  return (artifact?.upcoming_events ?? [])
    .filter((e) => e.days_from_now != null && e.days_from_now >= 0 && e.days_from_now <= EVENTS_HORIZON_DAYS)
    .map(eventKeyFor);
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

/** One push-driven SSE connection to GET /trade/command-center/stream backs the whole
 * dashboard — positions/prediction/news all multiplexed over it, each leg diffed
 * server-side so a frame only arrives when something actually changed. Replaces the
 * page's old per-panel setInterval polling and manual refresh buttons entirely (per user
 * request — see .claude/backlog/items/2026-08-28-command-center-real-time-push.md). */
function useCommandCenterStream(agentId: string) {
  const { connect, disconnect, onStatusChange } = useSSE();
  const [status, setStatus] = useState<SSEStatus>("disconnected");
  const [positions, setPositions] = useState<{ groups: LivePositionGroup[]; skipped: LivePositionSkipped[] } | null>(
    null,
  );
  const [prediction, setPrediction] = useState<IndexPredictionArtifact | null>(null);
  const [news, setNews] = useState<IndexNewsImpactReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const baselineRef = useRef<LastSeenPrediction | null | undefined>(undefined);

  useEffect(() => {
    onStatusChange(setStatus);
    connect(api.commandCenterStreamUrl(agentId, "NIFTY"), {
      positions: (data) => {
        setError(null);
        setPositions({
          groups: (data.groups as LivePositionGroup[]) ?? [],
          skipped: (data.skipped as LivePositionSkipped[]) ?? [],
        });
      },
      positions_error: (data) => setError(String(data.message ?? "positions stream error")),
      prediction: (data) => {
        setError(null);
        const artifact = (data.artifact as IndexPredictionArtifact | null) ?? null;
        if (baselineRef.current === undefined) baselineRef.current = readLastSeenPrediction();
        setPrediction(artifact);
        if (artifact?.as_of) {
          writeLastSeenPrediction({
            as_of: artifact.as_of,
            expected_return_pct: artifact.prediction?.expected_return_pct ?? null,
            range_low: artifact.prediction?.range?.low ?? null,
            range_high: artifact.prediction?.range?.high ?? null,
            eventKeys: events7dKeys(artifact),
          });
        }
      },
      prediction_error: (data) => setError(String(data.message ?? "prediction stream error")),
      news: (data) => {
        setError(null);
        setNews((data.report as IndexNewsImpactReport | null) ?? null);
      },
      news_error: (data) => setError(String(data.message ?? "news stream error")),
    });
    return () => disconnect();
  }, [agentId, connect, disconnect, onStatusChange]);

  const revision = useMemo(() => {
    const prev = baselineRef.current;
    if (!prev || !prediction?.as_of || prev.as_of === prediction.as_of) return null;
    const curRet = prediction.prediction?.expected_return_pct ?? null;
    return {
      prevAsOf: prev.as_of,
      returnDeltaPct: curRet != null && prev.expected_return_pct != null ? curRet - prev.expected_return_pct : null,
      prevRangeLow: prev.range_low,
      prevRangeHigh: prev.range_high,
    };
    // baselineRef.current is a ref, deliberately excluded — only prediction.as_of changes this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prediction?.as_of, prediction?.prediction?.expected_return_pct]);

  // "What changed since I last looked" for events specifically — the shipped
  // prediction-revised-indicator (`revision` above) already covers expected-return/range
  // revisions; this covers the real, separable gap its own closing note flagged (new
  // upcoming events), using the same frozen-at-mount baseline comparison and the same real
  // `IndexPredictionArtifact.upcoming_events` data the events strip already renders from —
  // no new data source. `null` (not an empty Set) when there's no prior baseline to diff
  // against (first-ever visit, or an old-shape stored snapshot with no `eventKeys`), so the
  // UI can tell "nothing new" apart from "can't tell yet".
  const newEventKeys = useMemo(() => {
    const prev = baselineRef.current;
    if (!prev?.eventKeys) return null;
    const current = events7dKeys(prediction);
    const added = current.filter((k) => !prev.eventKeys!.includes(k));
    return new Set(added);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prediction]);

  return { status, positions, prediction, news, error, revision, newEventKeys };
}

function LiveDot({ status }: { status: SSEStatus }) {
  return (
    <span
      className={cn(
        "h-2 w-2 shrink-0 rounded-full",
        status === "connected"
          ? "bg-emerald-500"
          : status === "reconnecting"
            ? "animate-pulse bg-amber-500"
            : "bg-muted-foreground/40",
      )}
      title={`Live feed: ${status}`}
      aria-label={`Live feed: ${status}`}
    />
  );
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

interface PnlPositionsPanelProps {
  agents: AutonomousAgentInstance[];
  agentId: string;
  onAgentChange: (id: string) => void;
  groups: LivePositionGroup[];
  skipped: LivePositionSkipped[];
  status: SSEStatus;
}

function PnlPositionsPanel({ agents, agentId, onAgentChange, groups, skipped, status }: PnlPositionsPanelProps) {
  // trajectory[0] is today's real observed P&L per LivePositionGroup's documented shape.
  const totalPnl = useMemo(
    () => groups.reduce((sum, g) => sum + (g.trajectory[0]?.pnl_inr ?? 0), 0),
    [groups],
  );
  const maxExpiryDays = useMemo(
    () => groups.reduce((max, g) => Math.max(max, g.expiry_days), 0),
    [groups],
  );
  const hasData = groups.length > 0 || skipped.length > 0;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-2 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <LiveDot status={status} />
          <select
            value={agentId}
            onChange={(e) => onAgentChange(e.target.value)}
            className="rounded-md border bg-background px-2 py-1 text-xs"
          >
            {agents.length === 0 ? <option value="">No agents</option> : null}
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name || a.id}
              </option>
            ))}
          </select>
        </div>
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
        </div>
      </div>

      {!agentId ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          No autonomous agents exist yet.
        </div>
      ) : !hasData ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          {status === "connected" ? "No open positions for this agent." : "Connecting…"}
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

interface EventsRangePanelProps {
  artifact: IndexPredictionArtifact | null;
  status: SSEStatus;
  revision: {
    prevAsOf: string;
    returnDeltaPct: number | null;
    prevRangeLow: number | null;
    prevRangeHigh: number | null;
  } | null;
  newEventKeys: Set<string> | null;
}

function EventsRangePanel({ artifact, status, revision, newEventKeys }: EventsRangePanelProps) {
  const [priorDay, setPriorDay] = useState<{ day: string; bars: HubIndexHistoryBar[] } | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Yesterday's closed session never changes, so this is a one-time fetch on mount — not
    // part of the live stream. "Yesterday" is resolved from the real recorded-days list
    // (the most recent recorded day strictly before today, IST) rather than naive date
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

  const events7d: IndexUpcomingEvent[] = useMemo(
    () =>
      (artifact?.upcoming_events ?? []).filter(
        (e) => e.days_from_now != null && e.days_from_now >= 0 && e.days_from_now <= EVENTS_HORIZON_DAYS,
      ),
    [artifact],
  );

  // Prefer the real multi-horizon forecast fan (`forecast_nifty_fan` — independently
  // retrained per horizon) over `daily_range_band` (a GBM interpolation between the single
  // headline range's own terminal endpoints, kept as a fallback for artifacts predating the
  // fan wiring or a hub dir with no fan ever persisted yet). Both are sliced to this panel's
  // 7-day window — the backend artifact's own pipeline horizon (commonly 14d) covers more
  // days than this dashboard shows; slicing to a real band's first week is honest (still
  // real data), unlike fabricating a 7-day one. See
  // .claude/backlog/items/2026-08-26-wire-nifty-forecast-fan-into-consumers.md.
  const dailyBand7d = useMemo(() => {
    const fanBand = artifact?.prediction?.forecast_fan?.band;
    const spot = artifact?.spot;
    if (fanBand && fanBand.length > 0 && spot != null) {
      const interpolated = interpolateFanToDailyBand(fanBand, spot, EVENTS_HORIZON_DAYS);
      if (interpolated) return interpolated;
    }
    return artifact?.prediction?.daily_range_band?.filter((row) => row.days_ahead <= EVENTS_HORIZON_DAYS);
  }, [artifact]);

  // Confidence/uncertainty indicator: the forecast fan's own p10/p90 spread at the horizon
  // closest to this panel's 7-day window, compared against the same real measurement from
  // the previous persisted fan run (when one exists). Real per-run data either way — never a
  // fabricated "confidence score". See .claude/backlog/items/
  // 2026-08-27-unified-trading-command-center-dashboard.md's "confidence/uncertainty
  // indicator" candidate panel.
  const uncertainty = useMemo(() => {
    const fan = artifact?.prediction?.forecast_fan;
    if (!fan || !fan.band || fan.band.length === 0) return null;
    const current = pickHeadlineFanPoint(fan.band, EVENTS_HORIZON_DAYS);
    const currentSpreadPct = fanSpreadPct(current);
    if (current == null || currentSpreadPct == null) return null;

    const previous = fan.previous?.band ? pickHeadlineFanPoint(fan.previous.band, EVENTS_HORIZON_DAYS) : undefined;
    const previousSpreadPct = fanSpreadPct(previous);

    return {
      daysAhead: current.days_ahead,
      spreadPct: currentSpreadPct,
      predictedAt: fan.predicted_at,
      previous:
        previous != null && previousSpreadPct != null
          ? {
              daysAhead: previous.days_ahead,
              spreadPct: previousSpreadPct,
              predictedAt: fan.previous?.predicted_at ?? null,
              deltaPct: currentSpreadPct - previousSpreadPct,
            }
          : null,
    };
  }, [artifact]);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-2 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <LiveDot status={status} />
      </div>

      {!artifact ? (
        <div className="rounded-lg border border-dashed border-border/60 bg-muted/10 p-3 text-center text-xs text-muted-foreground">
          {status === "connected" ? "No prediction artifact available yet." : "Connecting…"}
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

          {uncertainty ? (
            <div
              className="flex items-center gap-1.5 rounded-lg border border-border/50 bg-muted/10 px-2 py-1 text-xs text-muted-foreground"
              title={`Forecast fan p10–p90 spread at +${uncertainty.daysAhead}d, as of ${new Date(uncertainty.predictedAt).toLocaleString()}${
                uncertainty.previous
                  ? ` — previous run ${new Date(uncertainty.previous.predictedAt ?? "").toLocaleString()} at +${uncertainty.previous.daysAhead}d`
                  : ""
              }`}
            >
              <Gauge className="h-3.5 w-3.5 shrink-0" />
              <span>
                Uncertainty (+{uncertainty.daysAhead}d): ±{(uncertainty.spreadPct / 2).toFixed(1)}%
              </span>
              {uncertainty.previous ? (
                <span
                  className={cn(
                    "font-medium",
                    uncertainty.previous.deltaPct > 0.05
                      ? "text-amber-600 dark:text-amber-400"
                      : uncertainty.previous.deltaPct < -0.05
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "",
                  )}
                >
                  {uncertainty.previous.deltaPct > 0.05
                    ? "widened"
                    : uncertainty.previous.deltaPct < -0.05
                      ? "tightened"
                      : "unchanged"}{" "}
                  vs prior run
                </span>
              ) : (
                <span className="italic">no prior run to compare yet</span>
              )}
            </div>
          ) : null}

          <EventsCalendar
            structuralEvents={artifact?.upcoming_events ?? []}
            isNewStructuralEvent={(ev) => newEventKeys?.has(eventKeyFor(ev)) ?? false}
          />

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
  const [agents, setAgents] = useState<AutonomousAgentInstance[]>([]);
  const [agentId, setAgentId] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    // One-time metadata fetch (which agents exist) — not something that needs to ride the
    // live stream; the stream itself starts once an agentId is picked below.
    api
      .listAutonomousAgents()
      .then((res) => {
        if (cancelled) return;
        setAgents(res.agents);
        if (res.agents.length > 0) setAgentId((prev) => prev || res.agents[0].id);
      })
      .catch(() => {
        /* PnlPositionsPanel's own empty state covers "no agents" honestly */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const { status, positions, prediction, news, error, revision, newEventKeys } = useCommandCenterStream(agentId);

  return (
    <div className="mx-auto min-h-screen max-w-none space-y-2 bg-background p-3 text-foreground">
      {error ? (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-2 text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[2fr_1fr]">
        <div className="space-y-2">
          <PnlPositionsPanel
            agents={agents}
            agentId={agentId}
            onAgentChange={setAgentId}
            groups={positions?.groups ?? []}
            skipped={positions?.skipped ?? []}
            status={status}
          />
          <EventsRangePanel artifact={prediction} status={status} revision={revision} newEventKeys={newEventKeys} />
        </div>
        <div className="rounded-xl border border-border/60 bg-card p-2 shadow-sm">
          <NewsImpactPanel horizonDays={EVENTS_HORIZON_DAYS} externalReport={news} />
        </div>
      </div>
    </div>
  );
}
