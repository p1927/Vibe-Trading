import { memo, useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import {
  BarChart3,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Loader2,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type TradePlanLeg,
  type TradePlanRecommended,
  type TradePlanScenario,
  type TradePlanStrategyVariant,
  type TradePlanWidget,
  type TradeExecutionMode,
} from "@/lib/api";
import { AgentAvatar } from "./AgentAvatar";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { MiniPnlOverTimeChart } from "@/components/charts/MiniPnlOverTimeChart";
import { IndexFactorChart } from "@/components/charts/IndexFactorChart";
import {
  NewsScenarioPathChart,
  formatOutcomeReturn,
} from "@/components/charts/NewsScenarioPathChart";
import { buildOptionSymbol, type StrategyLeg } from "@/lib/strategyMath";
import {
  computePnlOverTimeSamples,
  inferStrikeStep,
  normalizeStrategyKey,
  resolveWidgetSpot,
  strategyLegsToTradePlanLegs,
  tradePlanLegsToStrategyLegs,
  widgetPayoffInputs,
} from "@/lib/tradePlanLegs";
import { formatStrategyName, formatViewLabel } from "@/lib/planDisplay";
import {
  isTradeWidgetModified,
  setTradeWidgetAdjustment,
  type TradeWidgetAdjustment,
} from "@/lib/tradeWidgetContext";
import { useLivePlanContext } from "@/hooks/useLivePlanContext";

const PayoffChart = lazy(() =>
  import("@/components/charts/PayoffChart").then((m) => ({ default: m.PayoffChart })),
);

interface Props {
  widget: TradePlanWidget;
  autonomousMode?: boolean;
  planApproved?: boolean;
}

function formatInr(value: unknown, precise = false): string {
  const n = Number(value);
  if (value == null || !Number.isFinite(n)) return "—";
  if (precise || (Math.abs(n) > 0 && Math.abs(n) < 100)) {
    return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatProbability(value: unknown): string {
  const n = Number(value);
  if (Number.isFinite(n) && n > 0 && n <= 1) return `${(n * 100).toFixed(0)}%`;
  if (typeof value === "string" && value) return value;
  return "—";
}

function resolveVariant(
  widget: TradePlanWidget,
  hint: string | undefined,
): TradePlanStrategyVariant | null {
  if (!hint) return null;
  const variants = widget.strategy_variants || {};
  if (variants[hint]) return variants[hint];
  const norm = normalizeStrategyKey(hint);
  const key = Object.keys(variants).find(
    (k) => normalizeStrategyKey(k) === norm,
  );
  return key ? variants[key] : null;
}

function resolveVariantByName(
  widget: TradePlanWidget,
  strategyName: string | undefined,
): TradePlanStrategyVariant | null {
  return resolveVariant(widget, strategyName);
}

function ScenarioTile({
  scenario,
  active,
  onSelect,
}: {
  scenario: TradePlanScenario;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={[
        "rounded-lg border p-2.5 text-left text-xs transition-colors w-full",
        active ? "border-primary/60 bg-primary/5" : "border-border/60 bg-muted/15 hover:border-primary/30",
      ].join(" ")}
    >
      <div className="font-semibold text-foreground">{scenario.name}</div>
      <div className="mt-1 text-muted-foreground line-clamp-2">{scenario.trigger}</div>
      <div className="mt-1 font-mono text-[10px] text-primary">
        {formatProbability(scenario.probability)} · {scenario.strategy_hint}
      </div>
    </button>
  );
}

export const TradePlanWidgetCard = memo(function TradePlanWidgetCard({
  widget,
  autonomousMode = false,
  planApproved = false,
}: Props) {
  const agentPick = widget.agent_recommended_strategy || widget.recommended?.name || "";
  const [selectedScenario, setSelectedScenario] = useState(0);
  const [selectedStrategyName, setSelectedStrategyName] = useState(agentPick);
  const [executing, setExecuting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [executed, setExecuted] = useState(false);
  const [execMode, setExecMode] = useState<TradeExecutionMode | null>(null);
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const baselineLegsRef = useRef<TradePlanLeg[]>([]);
  const widgetIdRef = useRef(widget.widget_id);

  useEffect(() => {
    const loadMode = () => {
      api.getTradeExecutionMode().then(setExecMode).catch(() => null);
    };
    loadMode();
    window.addEventListener("focus", loadMode);
    return () => window.removeEventListener("focus", loadMode);
  }, []);

  useEffect(() => {
    setSelectedStrategyName(agentPick);
    setSelectedScenario(0);
  }, [widget.widget_id, agentPick]);

  const scenarios = widget.scenarios || [];
  const ranked = widget.ranked_strategies || [];
  const pred = widget.prediction || {};
  const isOptions =
    widget.asset_type !== "stock" &&
    widget.asset_type !== "index" &&
    widget.instrument_type !== "stock";
  const isIndex = widget.asset_type === "index";
  const presentationMode =
    widget.presentation_mode ??
    (isIndex ? "index_outlook" : isOptions ? "options_strategy" : "stock_trade");
  const showPayoff = presentationMode === "options_strategy";
  const showCharges =
    presentationMode === "options_strategy" || presentationMode === "stock_trade";
  const showIndexChart = presentationMode === "index_outlook";

  const strategyIndex = useMemo(() => {
    if (!ranked.length) return 0;
    const idx = ranked.findIndex(
      (s) => normalizeStrategyKey(s.name || "") === normalizeStrategyKey(selectedStrategyName),
    );
    return idx >= 0 ? idx : 0;
  }, [ranked, selectedStrategyName]);

  const activeScenario = scenarios[selectedScenario];
  const scenarioVariant = useMemo(
    () => resolveVariant(widget, activeScenario?.strategy_hint),
    [widget, activeScenario?.strategy_hint],
  );
  const strategyVariant = useMemo(
    () => resolveVariantByName(widget, selectedStrategyName),
    [widget, selectedStrategyName],
  );
  const activeVariant = strategyVariant || scenarioVariant;

  const rec: TradePlanRecommended = activeVariant?.recommended || widget.recommended || {};
  const rankedRow = ranked[strategyIndex] || ranked[0];
  const displayRec: TradePlanRecommended = rec.name
    ? rec
    : rankedRow
      ? {
          name: rankedRow.name,
          score: rankedRow.score,
          tier: rankedRow.tier,
          rationale: rankedRow.rationale,
          legs: [],
          max_profit: rankedRow.max_profit,
          max_loss: rankedRow.max_loss,
          net_max_profit: rankedRow.net_max_profit,
          net_max_loss: rankedRow.net_max_loss,
        }
      : rec;
  const charges = activeVariant?.charges || widget.charges || {};
  const pnlOverTime = activeVariant?.payoff_over_time || widget.payoff_over_time || {};
  const steps = activeVariant?.implementation_steps || widget.implementation_steps || [];

  const originalTradeLegs = useMemo(
    () => (displayRec.legs?.length ? displayRec.legs : rec.legs || []) as TradePlanLeg[],
    [displayRec.legs, rec.legs],
  );

  const resolvedSpot = resolveWidgetSpot(widget);

  useEffect(() => {
    if (!isOptions || !resolvedSpot || originalTradeLegs.length === 0) {
      setLegs([]);
      baselineLegsRef.current = [];
      return;
    }
    const expiry = widget.expiry || "";
    const next = tradePlanLegsToStrategyLegs(
      originalTradeLegs,
      widget.underlying,
      expiry,
    );
    setLegs(next);
    baselineLegsRef.current = originalTradeLegs.map((l) => ({ ...l }));
  }, [
    isOptions,
    widget.widget_id,
    widget.underlying,
    widget.expiry,
    widget.spot,
    resolvedSpot,
    originalTradeLegs,
    selectedScenario,
    selectedStrategyName,
  ]);

  const payoffInputs = useMemo(
    () => (legs.length && resolvedSpot ? widgetPayoffInputs(widget, legs) : null),
    [widget, legs, resolvedSpot],
  );

  const strikeStep = useMemo(
    () => inferStrikeStep(widget.underlying, strategyLegsToTradePlanLegs(legs)),
    [widget.underlying, legs],
  );

  const legsModified = useMemo(() => {
    const adj: TradeWidgetAdjustment = {
      widget_id: widget.widget_id,
      underlying: widget.underlying,
      agent_recommended: agentPick,
      strategy_name: rec.name || agentPick,
      original_legs: baselineLegsRef.current,
      adjusted_legs: strategyLegsToTradePlanLegs(legs),
      payoff_summary: payoffInputs
        ? {
            max_profit: payoffInputs.payoff.maxProfit,
            max_loss: payoffInputs.payoff.maxLoss,
            breakevens: payoffInputs.payoff.breakevens,
          }
        : undefined,
    };
    return isTradeWidgetModified(adj);
  }, [widget, agentPick, rec.name, legs, payoffInputs]);

  useEffect(() => {
    if (!isOptions || legs.length === 0) {
      if (widgetIdRef.current === widget.widget_id) {
        setTradeWidgetAdjustment(null);
      }
      return;
    }
    const adj: TradeWidgetAdjustment = {
      widget_id: widget.widget_id,
      underlying: widget.underlying,
      agent_recommended: agentPick,
      strategy_name: rec.name || agentPick,
      original_legs: baselineLegsRef.current,
      adjusted_legs: strategyLegsToTradePlanLegs(legs),
      payoff_summary: payoffInputs
        ? {
            max_profit: payoffInputs.payoff.maxProfit,
            max_loss: payoffInputs.payoff.maxLoss,
            breakevens: payoffInputs.payoff.breakevens,
          }
        : undefined,
    };
    widgetIdRef.current = widget.widget_id;
    setTradeWidgetAdjustment(adj);
    return () => setTradeWidgetAdjustment(null);
  }, [isOptions, widget.widget_id, widget.underlying, agentPick, rec.name, legs, payoffInputs]);

  const handleStrikeChange = useCallback(
    (legId: string, strike: number) => {
      setLegs((prev) =>
        prev.map((leg) => {
          if (leg.id !== legId || leg.segment !== "OPTION" || !leg.optionType) return leg;
          const expiry = widget.expiry || leg.expiry;
          const symbol = buildOptionSymbol(
            widget.underlying,
            expiry,
            strike,
            leg.optionType,
          );
          return { ...leg, strike, expiry, symbol };
        }),
      );
    },
    [widget.underlying, widget.expiry],
  );

  const resetStrikes = useCallback(() => {
    if (!resolvedSpot || baselineLegsRef.current.length === 0) return;
    setLegs(
      tradePlanLegsToStrategyLegs(
        baselineLegsRef.current,
        widget.underlying,
        widget.expiry || "",
      ),
    );
  }, [widget.underlying, widget.expiry, resolvedSpot]);

  const isScenarioOverride = Boolean(
    activeVariant && displayRec.name && displayRec.name !== agentPick,
  );
  const isPaper = execMode?.mode === "paper";
  const liveBlocked = Boolean(execMode?.paper_env && !execMode?.live_allowed);
  const assetLabel =
    presentationMode === "index_outlook"
      ? "Index outlook"
      : presentationMode === "stock_trade"
        ? "Stock"
        : "Options";
  const planWarnings = widget.data_warnings ?? [];
  const livePlan = useLivePlanContext(widget.underlying);
  const staleness =
    livePlan.monitorEnabled && livePlan.staleness
      ? { ...widget.staleness, ...livePlan.staleness }
      : widget.staleness;
  const liveContext =
    livePlan.monitorEnabled && livePlan.liveContext
      ? { ...widget.live_context, ...livePlan.liveContext }
      : widget.live_context;
  const isStale = staleness?.status === "stale";
  const showFreshStrip = staleness?.status === "fresh";
  const spotDriftPct = staleness?.spot_drift_pct;
  const planIncomplete = widget.plan_status === "incomplete" || widget.plan_status === "partial";
  const viewLabel = formatViewLabel(pred.view) || "Neutral / range-bound";
  const strategyLabel =
    displayRec.name || agentPick || activeScenario?.strategy_hint
      ? formatStrategyName(displayRec.name || agentPick || activeScenario?.strategy_hint)
      : null;

  const pnlTimeSamples = useMemo(() => {
    const backend = (pnlOverTime.samples || []).filter(
      (s: { pnl?: number; net_pnl?: number }) => s.pnl != null || s.net_pnl != null,
    );
    if (backend.length >= 2) return backend;
    if (!isOptions || !resolvedSpot || legs.length === 0) return backend;
    const entryCharges = Number(
      (charges.total as { total_charges?: number } | undefined)?.total_charges ??
        charges.round_trip_charges ??
        0,
    );
    return computePnlOverTimeSamples(
      legs,
      resolvedSpot,
      widget.expiry || "",
      entryCharges,
    );
  }, [
    pnlOverTime.samples,
    isOptions,
    resolvedSpot,
    legs,
    charges,
    widget.expiry,
  ]);

  const handleScenarioSelect = useCallback(
    (index: number) => {
      setSelectedScenario(index);
      const hint = scenarios[index]?.strategy_hint;
      if (hint) setSelectedStrategyName(hint);
    },
    [scenarios],
  );

  const handleStrategyNav = useCallback(
    (delta: number) => {
      if (!ranked.length) return;
      const next = (strategyIndex + delta + ranked.length) % ranked.length;
      const name = ranked[next]?.name;
      if (name) setSelectedStrategyName(name);
    },
    [ranked, strategyIndex],
  );

  const executeOrders = useMemo(() => {
    if (legsModified && legs.length > 0) {
      return strategyLegsToTradePlanLegs(legs) as Record<string, unknown>[];
    }
    for (const step of steps) {
      if (step.action === "execute_basket" && step.payload?.orders) {
        return step.payload.orders as Record<string, unknown>[];
      }
    }
    return (rec.legs || []) as Record<string, unknown>[];
  }, [legsModified, legs, steps, rec.legs]);

  const handleExecute = useCallback(async () => {
    setExecuting(true);
    try {
      const result = await api.executeTradeBasket({
        widget_id: widget.widget_id,
        orders: executeOrders,
      });
      setExecuted(true);
      const mode = result.execution_mode === "paper" ? " (paper)" : "";
      toast.success((result.message || "Basket order submitted") + mode);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Execution failed");
    } finally {
      setExecuting(false);
      setConfirmOpen(false);
    }
  }, [executeOrders, widget.widget_id]);

  const builderUrl = widget.meta?.strategy_builder_execute_url || widget.meta?.strategy_builder_url;
  const executeLabel = isPaper ? "Execute (Paper)" : "Execute (Live)";
  const isNewsScenario = widget.widget_kind === "news_event_scenario";

  if (isNewsScenario) {
    const eventTitle =
      (widget.event?.title as string | undefined)?.trim() ||
      (widget.event?.headline as string | undefined)?.trim() ||
      "News event scenario";
    const outcomes = widget.outcomes ?? [];
    const selected = outcomes.find((o) => o.id === widget.selected_outcome_id) ?? outcomes[0];

    return (
      <div className="flex gap-3">
        <AgentAvatar />
        <div className="flex-1 min-w-0 space-y-3 rounded-xl border border-border/60 bg-card/50 p-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold">
                <BarChart3 className="h-4 w-4 text-primary" />
                News scenario — {widget.underlying}
              </div>
              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{eventTitle}</p>
            </div>
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase text-muted-foreground">
              Scenario paths
            </span>
          </div>

          <NewsScenarioPathChart
            baselinePath={widget.baseline?.path}
            outcomes={outcomes}
            fanBand={widget.fan_band}
            selectedOutcomeId={widget.selected_outcome_id}
            height={200}
            compact
            showLegend={outcomes.length <= 3}
          />

          {outcomes.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 text-[10px]">
              {outcomes.slice(0, 4).map((outcome) => (
                <span
                  key={outcome.id || outcome.label}
                  className={[
                    "rounded-md border px-2 py-1 font-mono",
                    outcome.id === widget.selected_outcome_id
                      ? "border-primary/50 bg-primary/5 text-primary"
                      : "border-border/50 text-muted-foreground",
                  ].join(" ")}
                >
                  {outcome.label || outcome.id}: {formatOutcomeReturn(outcome)}
                </span>
              ))}
            </div>
          ) : null}

          {selected ? (
            <p className="text-[10px] text-muted-foreground">
              Highlighted branch:{" "}
              <span className="font-medium text-foreground">{selected.label || selected.id}</span>
              {selected.range?.low != null && selected.range?.high != null ? (
                <span className="ml-1 font-mono">
                  · range{" "}
                  {Number(selected.range.low).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                  –
                  {Number(selected.range.high).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                </span>
              ) : null}
            </p>
          ) : null}
        </div>
      </div>
    );
  }

  const isHoldCash =
    normalizeStrategyKey(displayRec.name || "") === "hold_cash" ||
    normalizeStrategyKey(agentPick) === "hold_cash";
  const autonomousDisplayOnly = autonomousMode && planApproved;
  const hideExecute =
    autonomousDisplayOnly ||
    isHoldCash ||
    executeOrders.length === 0 ||
    (!isPaper && liveBlocked);
  const executeDisabled = executing || executed || hideExecute;
  const openAlgoUrl = execMode?.switch_url || "";

  if (
    showPayoff &&
    legs.length === 0 &&
    ranked.length === 0 &&
    !(widget.payoff?.samples?.length)
  ) {
    return null;
  }

  if (
    showIndexChart &&
    !widget.factor_explanation?.contributors?.length &&
    !(widget.scenarios?.length)
  ) {
    return null;
  }

  return (
    <div className="flex gap-3">
      <AgentAvatar />
      <div className="flex-1 min-w-0 space-y-3 rounded-xl border border-border/60 bg-card/50 p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <BarChart3 className="h-4 w-4 text-primary" />
              Trade plan — {widget.underlying}
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              {strategyLabel
                ? `Recommended: ${strategyLabel}`
                : planIncomplete
                  ? "No concrete strategy ranked yet"
                  : viewLabel}
              {pred.iv_regime ? ` · IV ${pred.iv_regime}` : ""}
              {pred.confidence != null && pred.confidence > 0.05
                ? ` · confidence ${pred.confidence}`
                : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase text-muted-foreground">
              {assetLabel}
            </span>
            {planIncomplete && (
              <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-400">
                {widget.plan_status === "partial" ? "Partial plan" : "Incomplete plan"}
              </span>
            )}
            {isPaper && (
              <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-400">
                Paper
              </span>
            )}
            {autonomousDisplayOnly && (
              <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-sky-700 dark:text-sky-400">
                Autonomous · Nautilus
              </span>
            )}
            {!isPaper && (
              <span className="rounded-full bg-rose-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-rose-700 dark:text-rose-400">
                Live
              </span>
            )}
            {displayRec.tier && (
              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold uppercase text-primary">
                {displayRec.tier}
              </span>
            )}
            {legsModified && (
              <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-sky-700 dark:text-sky-400">
                Legs modified
              </span>
            )}
          </div>
        </div>

        {widget.error && (
          <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            {widget.error}
          </p>
        )}

        {isStale && (
          <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
            Plan may be outdated
            {spotDriftPct != null && Number.isFinite(spotDriftPct)
              ? ` · spot moved ${spotDriftPct.toFixed(1)}%`
              : ""}
            {liveContext?.spot != null && liveContext?.plan_spot != null ? (
              <span className="mt-1 block font-mono text-[10px] text-amber-800/90 dark:text-amber-300/90">
                Live {formatInr(liveContext.spot)} vs plan {formatInr(liveContext.plan_spot)}
              </span>
            ) : null}
          </p>
        )}

        {showFreshStrip && (
          <p className="rounded-lg border border-emerald-500/25 bg-emerald-500/5 px-3 py-2 text-xs text-emerald-900 dark:text-emerald-200">
            Plan current
            {liveContext?.spot != null && liveContext?.plan_spot != null ? (
              <span className="ml-1 font-mono text-[10px] text-emerald-800/90 dark:text-emerald-300/90">
                · Live {formatInr(liveContext.spot)} vs plan {formatInr(liveContext.plan_spot)}
              </span>
            ) : null}
          </p>
        )}

        {planWarnings.length > 0 && (
          <p className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-900 dark:text-amber-200">
            {planWarnings[0]}
          </p>
        )}

        {!showPayoff && pred && (pred.range || pred.expected_return_pct != null) && (
          <div className="rounded-lg border border-border/50 bg-muted/15 p-3 text-xs space-y-1">
            <p className="font-semibold text-foreground">Forecast</p>
            <p className="text-muted-foreground">
              {viewLabel}
              {pred.horizon_days != null ? ` · ${pred.horizon_days}d horizon` : ""}
              {pred.expected_return_pct != null
                ? ` · expected ${Number(pred.expected_return_pct) >= 0 ? "+" : ""}${pred.expected_return_pct}%`
                : ""}
              {pred.confidence != null && pred.confidence > 0.05
                ? ` · confidence ${pred.confidence}`
                : ""}
            </p>
            {pred.range?.low != null && pred.range?.high != null && (
              <p className="font-mono text-[11px] text-muted-foreground">
                Range {formatInr(pred.range.low)} – {formatInr(pred.range.high)}
              </p>
            )}
            {pred.provenance && (
              <p className="text-[10px] text-muted-foreground">
                Sources:{" "}
                {[
                  pred.provenance.direction ? `direction (${pred.provenance.direction})` : null,
                  pred.provenance.range ? `range (${pred.provenance.range})` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            )}
          </div>
        )}

        {scenarios.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1.5">
              Scenarios — tap to preview strategy
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              {scenarios.slice(0, 4).map((sc, i) => (
                <ScenarioTile
                  key={sc.name || i}
                  scenario={sc}
                  active={selectedScenario === i}
                  onSelect={() => handleScenarioSelect(i)}
                />
              ))}
            </div>
          </div>
        )}

        {ranked.length > 0 && (
          <div className="rounded-lg border border-border/50 bg-muted/10 p-2.5">
            <p className="text-[11px] font-medium text-muted-foreground mb-2">
              Ranked strategies — browse to compare
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                aria-label="Previous strategy"
                disabled={ranked.length <= 1}
                onClick={() => handleStrategyNav(-1)}
                className="rounded-md border border-border/60 p-1.5 text-muted-foreground hover:text-foreground disabled:opacity-40"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <div className="flex-1 min-w-0 text-center">
                <div className="text-xs font-semibold truncate">
                  {formatStrategyName(ranked[strategyIndex]?.name || selectedStrategyName)}
                </div>
                <div className="text-[10px] text-muted-foreground font-mono">
                  {strategyIndex + 1} / {ranked.length}
                  {ranked[strategyIndex]?.tier ? ` · ${ranked[strategyIndex]?.tier}` : ""}
                  {ranked[strategyIndex]?.score != null
                    ? ` · score ${ranked[strategyIndex]?.score}`
                    : ""}
                </div>
              </div>
              <button
                type="button"
                aria-label="Next strategy"
                disabled={ranked.length <= 1}
                onClick={() => handleStrategyNav(1)}
                className="rounded-md border border-border/60 p-1.5 text-muted-foreground hover:text-foreground disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
            {ranked[strategyIndex]?.rationale && (
              <p className="mt-2 text-[10px] text-muted-foreground line-clamp-2">
                {ranked[strategyIndex]?.rationale}
              </p>
            )}
            <p className="mt-1.5 text-[10px] text-muted-foreground">
              Ask in chat about this strategy — your message includes widget context when legs change.
            </p>
          </div>
        )}

        {showIndexChart &&
        (widget.factor_sensitivity?.length ||
          widget.event_impact_curves?.length ||
          widget.factor_explanation?.contributors?.length) ? (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">
              Index vs factor / event shocks
            </p>
            <IndexFactorChart
              sensitivity={widget.factor_sensitivity || []}
              eventCurves={widget.event_impact_curves}
              spot={widget.spot ?? undefined}
              height={220}
            />
            {widget.factor_explanation?.contributors?.length ? (
              <ul className="mt-2 space-y-1 text-[10px] text-muted-foreground">
                {(widget.factor_explanation.contributors as Array<Record<string, unknown>>)
                  .slice(0, 5)
                  .map((row) => (
                    <li key={String(row.factor)}>
                      <span className="font-medium text-foreground">
                        {String(row.label || row.factor)}
                      </span>
                      {": "}
                      {typeof row.contribution_pct === "number"
                        ? `${row.contribution_pct >= 0 ? "+" : ""}${row.contribution_pct.toFixed(2)}%`
                        : "—"}
                      {" macro"}
                      {typeof row.share_of_macro === "number"
                        ? ` (${Math.round(row.share_of_macro * 100)}% of macro block)`
                        : ""}
                    </li>
                  ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {showPayoff && payoffInputs && legs.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">
              Interactive payoff{isScenarioOverride ? ` — ${displayRec.name}` : " (recommended)"}
            </p>
            <Suspense fallback={<div className="h-[280px] animate-pulse rounded-lg bg-muted/40" />}>
              <PayoffChart
                title={`${widget.underlying} — ${widget.expiry || "—"}`}
                spot={payoffInputs.spot}
                atmIv={payoffInputs.atmIv}
                tYears={payoffInputs.tYears}
                payoff={payoffInputs.payoff}
                legs={legs}
                strikeStep={strikeStep}
                onStrikeChange={handleStrikeChange}
                onResetStrikes={resetStrikes}
                canResetStrikes={legsModified}
                height={280}
              />
            </Suspense>
          </div>
        )}

        {showPayoff && widget.asset_type !== "stock" && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">
              P&amp;L vs days to expiry (current spot)
            </p>
            {pnlTimeSamples.length >= 2 ? (
              <MiniPnlOverTimeChart samples={pnlTimeSamples} height={100} />
            ) : (
              <p className="text-[10px] text-muted-foreground rounded-lg border border-dashed border-border/60 px-3 py-2">
                {planIncomplete
                  ? "P&L over time unavailable — live chain or legs missing. Ask the agent to refresh with OpenAlgo running."
                  : "P&L over time needs at least two expiry snapshots — select a strategy with legs or refresh research."}
              </p>
            )}
          </div>
        )}

        {displayRec.name && (
          <div className="rounded-lg border border-border/50 bg-muted/20 p-3 text-xs space-y-2">
            <div className="flex items-center gap-2 font-semibold text-foreground">
              <TrendingUp className="h-3.5 w-3.5" />
              {isScenarioOverride ? `Scenario: ${displayRec.name}` : `Recommended: ${displayRec.name}`}
              {displayRec.score != null ? ` (score ${displayRec.score})` : ""}
            </div>
            {isScenarioOverride && agentPick && (
              <p className="text-[10px] text-amber-700 dark:text-amber-400">
                Agent recommended <span className="font-mono">{agentPick}</span> — you selected this alternative.
              </p>
            )}
            {displayRec.rationale && <p className="text-muted-foreground">{displayRec.rationale}</p>}
            {(displayRec.target != null || displayRec.stop != null) && (
              <p className="font-mono text-[11px] text-muted-foreground">
                {displayRec.target != null ? `Target ${formatInr(displayRec.target)}` : ""}
                {displayRec.target != null && displayRec.stop != null ? " · " : ""}
                {displayRec.stop != null ? `Stop ${formatInr(displayRec.stop)}` : ""}
              </p>
            )}
            <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
              <div>
                Max P:{" "}
                {formatInr(
                  payoffInputs?.payoff.maxProfit ??
                    displayRec.net_max_profit ??
                    displayRec.max_profit,
                )}
              </div>
              <div>
                Max L:{" "}
                {formatInr(
                  payoffInputs?.payoff.maxLoss ??
                    displayRec.net_max_loss ??
                    displayRec.max_loss,
                )}
              </div>
            </div>
            {(legs.length > 0 ? strategyLegsToTradePlanLegs(legs) : displayRec.legs || rec.legs || []).length > 0 && (
              <ul className="space-y-0.5 text-[11px]">
                {(legs.length > 0 ? strategyLegsToTradePlanLegs(legs) : displayRec.legs || rec.legs || []).map(
                  (leg, i) => (
                  <li key={`${leg.symbol}-${i}`} className="font-mono">
                    {leg.side} {leg.quantity}× {leg.symbol} @ {leg.price}
                  </li>
                  ),
                )}
              </ul>
            )}
            {isHoldCash && (
              <p className="text-[11px] text-muted-foreground italic">Remain in cash — no equity order on this recommendation.</p>
            )}
          </div>
        )}

        {showCharges ? (
        <div className="rounded-lg border border-border/50 p-3 text-xs">
          <div className="flex items-center gap-2 font-semibold mb-1">
            <Wallet className="h-3.5 w-3.5" />
            Charges &amp; costs
          </div>
          <div className="grid grid-cols-2 gap-1 font-mono text-[11px] text-muted-foreground">
            <div>Net debit/credit: {formatInr(charges.net_debit_credit)}</div>
            <div>Round-trip est.: {formatInr(charges.round_trip_charges)}</div>
          </div>
          {(charges.per_leg || []).length > 0 && (
            <ul className="mt-2 space-y-0.5 text-[10px] text-muted-foreground">
              {(charges.per_leg || []).slice(0, 4).map((row, i) => (
                <li key={`${String(row.symbol || row.leg)}-${i}`}>
                  {String(row.symbol || row.leg)}: brokerage {formatInr(row.brokerage)} · STT {formatInr(row.stt)} · GST {formatInr(row.gst)}
                </li>
              ))}
            </ul>
          )}
        </div>
        ) : null}

        {ranked.length > 1 && (
          <div className="text-[10px] text-muted-foreground">
            Also ranked:{" "}
            {ranked
              .filter((_, i) => i !== strategyIndex)
              .slice(0, 3)
              .map((s) => `${s.name} (${s.tier})`)
              .join(" · ")}
          </div>
        )}

        {execMode && (
          <p className="text-[10px] text-muted-foreground">
            Execution mode follows OpenAlgo ({isPaper ? "Analyze" : "Live"}).
            {openAlgoUrl ? (
              <>
                {" "}
                <a
                  href={openAlgoUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-primary hover:underline"
                >
                  Switch in OpenAlgo
                </a>
              </>
            ) : null}
            {liveBlocked ? " · Live orders from Vibe are blocked while OPENALGO_PAPER_MODE=true." : null}
          </p>
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            type="button"
            disabled={executeDisabled}
            onClick={() => setConfirmOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
          >
            {executing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : executed ? <Check className="h-3.5 w-3.5" /> : null}
            {executed ? "Submitted" : executeLabel}
          </button>
          {builderUrl && (
            <a
              href={builderUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Open Strategy Builder
            </a>
          )}
        </div>

        <ConfirmDialog
          open={confirmOpen}
          title={isPaper ? "Execute trade plan (paper)?" : "Execute trade plan?"}
          description={`Place ${executeOrders.length} leg(s) for ${widget.underlying} via OpenAlgo${isPaper ? " sandbox (no live broker)" : ""}. Net debit/credit ${formatInr(charges.net_debit_credit)}.`}
          confirmLabel={isPaper ? "Place paper basket" : "Place basket order"}
          cancelLabel="Cancel"
          onConfirm={handleExecute}
          onCancel={() => setConfirmOpen(false)}
        />
      </div>
    </div>
  );
});
