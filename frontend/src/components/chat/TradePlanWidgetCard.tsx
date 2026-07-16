import { memo, useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import {
  BarChart3,
  Check,
  ExternalLink,
  Loader2,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type TradePlanLeg,
  type TradePlanScenario,
  type TradePlanStrategyVariant,
  type TradePlanWidget,
  type TradeExecutionMode,
} from "@/lib/api";
import { AgentAvatar } from "./AgentAvatar";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { MiniPnlOverTimeChart } from "@/components/charts/MiniPnlOverTimeChart";
import { buildOptionSymbol, type StrategyLeg } from "@/lib/strategyMath";
import {
  inferStrikeStep,
  resolveWidgetSpot,
  strategyLegsToTradePlanLegs,
  tradePlanLegsToStrategyLegs,
  widgetPayoffInputs,
} from "@/lib/tradePlanLegs";
import {
  isTradeWidgetModified,
  setTradeWidgetAdjustment,
  type TradeWidgetAdjustment,
} from "@/lib/tradeWidgetContext";

const PayoffChart = lazy(() =>
  import("@/components/charts/PayoffChart").then((m) => ({ default: m.PayoffChart })),
);

interface Props {
  widget: TradePlanWidget;
}

function formatInr(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
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
  const key = Object.keys(variants).find(
    (k) => k.toLowerCase() === hint.toLowerCase(),
  );
  return key ? variants[key] : null;
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

export const TradePlanWidgetCard = memo(function TradePlanWidgetCard({ widget }: Props) {
  const [selectedScenario, setSelectedScenario] = useState(0);
  const [executing, setExecuting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [executed, setExecuted] = useState(false);
  const [execMode, setExecMode] = useState<TradeExecutionMode | null>(null);
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const baselineLegsRef = useRef<TradePlanLeg[]>([]);
  const widgetIdRef = useRef(widget.widget_id);

  useEffect(() => {
    api.getTradeExecutionMode().then(setExecMode).catch(() => null);
  }, []);

  const scenarios = widget.scenarios || [];
  const ranked = widget.ranked_strategies || [];
  const pred = widget.prediction || {};
  const agentPick = widget.agent_recommended_strategy || widget.recommended?.name || "";
  const isOptions =
    widget.asset_type !== "stock" && widget.instrument_type !== "stock";

  const activeScenario = scenarios[selectedScenario];
  const scenarioVariant = useMemo(
    () => resolveVariant(widget, activeScenario?.strategy_hint),
    [widget, activeScenario?.strategy_hint],
  );

  const rec = scenarioVariant?.recommended || widget.recommended || {};
  const charges = scenarioVariant?.charges || widget.charges || {};
  const pnlOverTime = scenarioVariant?.payoff_over_time || widget.payoff_over_time || {};
  const steps = scenarioVariant?.implementation_steps || widget.implementation_steps || [];

  const originalTradeLegs = useMemo(
    () => (rec.legs || []) as TradePlanLeg[],
    [rec.legs],
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
    scenarioVariant && rec.name && rec.name !== agentPick,
  );
  const isPaper = execMode?.mode === "paper" || execMode?.paper_env;
  const assetLabel = isOptions ? "Options" : "Stock";

  const pnlTimeSamples = useMemo(
    () =>
      (pnlOverTime.samples || []).filter(
        (s: { pnl?: number; net_pnl?: number }) => s.pnl != null || s.net_pnl != null,
      ),
    [pnlOverTime.samples],
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
  const executeLabel = isPaper ? "Execute (Paper)" : "Execute in OpenAlgo";

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
              {pred.view || "neutral"}
              {pred.iv_regime ? ` · IV ${pred.iv_regime}` : ""}
              {pred.confidence != null ? ` · confidence ${pred.confidence}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase text-muted-foreground">
              {assetLabel}
            </span>
            {isPaper && (
              <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-amber-700 dark:text-amber-400">
                Paper
              </span>
            )}
            {rec.tier && (
              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold uppercase text-primary">
                {rec.tier}
              </span>
            )}
            {legsModified && (
              <span className="rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-sky-700 dark:text-sky-400">
                Legs modified
              </span>
            )}
          </div>
        </div>

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
                  onSelect={() => setSelectedScenario(i)}
                />
              ))}
            </div>
          </div>
        )}

        {isOptions && payoffInputs && legs.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">
              Interactive payoff{isScenarioOverride ? ` — ${rec.name}` : " (recommended)"}
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

        {pnlTimeSamples.length >= 2 && widget.asset_type !== "stock" && (
          <div>
            <p className="text-[11px] font-medium text-muted-foreground mb-1">
              P&amp;L vs days to expiry (current spot)
            </p>
            <MiniPnlOverTimeChart samples={pnlTimeSamples} height={100} />
          </div>
        )}

        {rec.name && (
          <div className="rounded-lg border border-border/50 bg-muted/20 p-3 text-xs space-y-2">
            <div className="flex items-center gap-2 font-semibold text-foreground">
              <TrendingUp className="h-3.5 w-3.5" />
              {isScenarioOverride ? `Scenario: ${rec.name}` : `Recommended: ${rec.name}`}
              {rec.score != null ? ` (score ${rec.score})` : ""}
            </div>
            {isScenarioOverride && agentPick && (
              <p className="text-[10px] text-amber-700 dark:text-amber-400">
                Agent recommended <span className="font-mono">{agentPick}</span> — you selected this alternative.
              </p>
            )}
            {rec.rationale && <p className="text-muted-foreground">{rec.rationale}</p>}
            <div className="grid grid-cols-2 gap-2 font-mono text-[11px]">
              <div>
                Max P:{" "}
                {formatInr(
                  payoffInputs?.payoff.maxProfit ?? rec.net_max_profit ?? rec.max_profit,
                )}
              </div>
              <div>
                Max L:{" "}
                {formatInr(
                  payoffInputs?.payoff.maxLoss ?? rec.net_max_loss ?? rec.max_loss,
                )}
              </div>
            </div>
            {(legs.length > 0 ? strategyLegsToTradePlanLegs(legs) : rec.legs || []).length > 0 && (
              <ul className="space-y-0.5 text-[11px]">
                {(legs.length > 0 ? strategyLegsToTradePlanLegs(legs) : rec.legs || []).map(
                  (leg, i) => (
                  <li key={`${leg.symbol}-${i}`} className="font-mono">
                    {leg.side} {leg.quantity}× {leg.symbol} @ {leg.price}
                  </li>
                  ),
                )}
              </ul>
            )}
          </div>
        )}

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

        {ranked.length > 1 && (
          <div className="text-[10px] text-muted-foreground">
            Alternatives: {ranked.slice(1, 4).map((s) => `${s.name} (${s.tier})`).join(" · ")}
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          <button
            type="button"
            disabled={executing || executed || executeOrders.length === 0}
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
